
"""
Privet Vocabulary v2 Importer.

Supported modes:
--validate-only
--dry-run
--commit

Does not create learning or assessment records.
"""

import hashlib
import json
import re
import unicodedata

from pathlib import Path

from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from django.db import transaction

from curriculum.models import Skill

from vocabulary.models import (
    VocabularyItem,
    VocabularyItemTopic,
    VocabularySRSState,
    VocabularyTopic,
)


SLUG = re.compile(
    r"^[a-z0-9][a-z0-9_-]*$"
)

VOC_SKILL = re.compile(
    r"^VOC-\d{2,}$"
)

LEVELS = {"A1", "A2"}


TOPIC_FIELDS = (
    "title_vi",
    "title_ru",
    "cefr_level",
    "description",
    "display_order",
    "is_active",
)

ITEM_FIELDS = (
    "lemma",
    "stress_marked",
    "meaning_vi",
    "meaning_en",
    "part_of_speech",
    "cefr_level",
    "example_ru",
    "example_vi",
    "notes_vi",
    "audio_url",
    "is_active",
    "source_entries",
)

# Do not change studied content silently.
PROTECTED_AFTER_START = {
    "lemma",
    "stress_marked",
    "meaning_vi",
    "meaning_en",
    "source_entries",
}


# ============================================================
# VALIDATION HELPERS
# ============================================================

def as_text(
    row,
    field,
    *,
    required=False,
    limit=None,
):

    value = row.get(field, "")

    if not isinstance(value, str):
        raise CommandError(
            f"{field}: expected string"
        )

    if required and not value:
        raise CommandError(
            f"{field}: required"
        )

    if (
        value != value.strip()
        or "  " in value
        or "\t" in value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise CommandError(
            f"{field}: whitespace or NFC error"
        )

    if limit and len(value) > limit:
        raise CommandError(
            f"{field}: exceeds {limit} characters"
        )

    return value


def as_slug(row, field, limit):

    value = as_text(
        row,
        field,
        required=True,
        limit=limit,
    )

    if not SLUG.fullmatch(value):
        raise CommandError(
            f"{field}: invalid slug {value!r}"
        )

    return value


def as_level(row):

    level = as_text(
        row,
        "cefr_level",
        required=True,
    )

    if level not in LEVELS:
        raise CommandError(
            "cefr_level must be A1 or A2"
        )

    return level


def as_int(
    row,
    field,
    default=0,
    minimum=0,
):

    value = row.get(field, default)

    if (
        type(value) is not int
        or value < minimum
    ):
        raise CommandError(
            f"{field}: integer >= {minimum} required"
        )

    return value


def as_bool(
    row,
    field,
    default=True,
):

    value = row.get(field, default)

    if type(value) is not bool:
        raise CommandError(
            f"{field}: boolean required"
        )

    return value


# ============================================================
# VALIDATE ORIGINAL SOURCE ENTRIES
# ============================================================

def validate_source_entries(
    row,
    lemma,
    topic_codes,
):

    entries = row.get(
        "source_entries",
        [],
    )

    if not isinstance(entries, list):
        raise CommandError(
            "source_entries must be an array"
        )

    observed = set()

    for entry in entries:

        if not isinstance(entry, dict):
            raise CommandError(
                "source_entries must contain objects"
            )

        level = as_text(
            entry,
            "level",
            required=True,
        )

        if level not in LEVELS:
            raise CommandError(
                "source_entries: invalid level"
            )

        rank = as_int(
            entry,
            "row_number",
            minimum=1,
        )

        key = (level, rank)

        if key in observed:
            raise CommandError(
                f"Duplicate source entry {key}"
            )

        observed.add(key)

        expected_topic = (
            f"level-{level.lower()}"
        )

        if expected_topic not in topic_codes:
            raise CommandError(
                f"Missing {expected_topic} membership"
            )

        russian_entry = as_text(
            entry,
            "russian_entry",
            required=True,
        )

        if russian_entry != lemma:
            raise CommandError(
                "source_entries lemma mismatch"
            )

        as_text(
            entry,
            "english_meaning",
            required=True,
        )

        as_text(
            entry,
            "initial",
            required=True,
        )

        as_text(
            entry,
            "source_file",
            required=True,
        )

        as_int(
            entry,
            "pdf_page",
            minimum=1,
        )

        as_int(
            entry,
            "book_page",
            minimum=1,
        )

    return entries


# ============================================================
# PHASE 1 - PARSE DOCUMENT
# ============================================================

def parse_document(path):

    try:
        raw = Path(path).read_bytes()

        document = json.loads(
            raw.decode("utf-8-sig")
        )

    except (
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        raise CommandError(
            f"Cannot read JSON: {exc}"
        ) from exc

    if (
        not isinstance(document, dict)
        or type(document.get("version")) is not int
        or document["version"] != 1
    ):
        raise CommandError(
            "Canonical JSON version must be 1"
        )

    if (
        not isinstance(document.get("topics"), list)
        or not isinstance(document.get("items"), list)
    ):
        raise CommandError(
            "Expected topics[] and items[]"
        )

    topics = []
    items = []

    seen_topics = set()
    seen_items = set()
    seen_source_rows = set()

    # --------------------------------------------------------
    # TOPICS
    # --------------------------------------------------------

    for index, row in enumerate(
        document["topics"],
        1,
    ):

        if not isinstance(row, dict):
            raise CommandError(
                f"topics[{index}] must be object"
            )

        code = as_slug(
            row,
            "code",
            80,
        )

        if code in seen_topics:
            raise CommandError(
                f"Duplicate topic {code}"
            )

        seen_topics.add(code)

        skill_code = as_text(
            row,
            "assessment_skill_code",
            limit=80,
        )

        if (
            skill_code
            and not VOC_SKILL.fullmatch(skill_code)
        ):
            raise CommandError(
                f"{code}: invalid assessment skill"
            )

        topics.append({
            "code": code,

            "title_vi": as_text(
                row,
                "title_vi",
                required=True,
                limit=160,
            ),

            "title_ru": as_text(
                row,
                "title_ru",
                limit=160,
            ),

            "cefr_level": as_level(row),

            "description": as_text(
                row,
                "description",
            ),

            "display_order": as_int(
                row,
                "display_order",
                index,
            ),

            "is_active": as_bool(
                row,
                "is_active",
            ),

            "assessment_skill_code": skill_code,
        })

    # --------------------------------------------------------
    # ITEMS
    # --------------------------------------------------------

    for index, row in enumerate(
        document["items"],
        1,
    ):

        if not isinstance(row, dict):
            raise CommandError(
                f"items[{index}] must be object"
            )

        source_id = as_slug(
            row,
            "source_id",
            100,
        )

        if source_id in seen_items:
            raise CommandError(
                f"Duplicate item {source_id}"
            )

        seen_items.add(source_id)

        topic_codes = row.get(
            "topic_codes"
        )

        if (
            not isinstance(topic_codes, list)
            or not topic_codes
            or any(
                not isinstance(code, str)
                for code in topic_codes
            )
            or len(topic_codes) != len(set(topic_codes))
            or not set(topic_codes).issubset(seen_topics)
        ):
            raise CommandError(
                f"{source_id}: invalid topic_codes"
            )

        # Each A1/A2 level has its own order.
        topic_orders = row.get(
            "topic_orders"
        )

        # Backward compatibility with old canonical JSON.
        if topic_orders is None:

            order = as_int(
                row,
                "display_order",
                index,
            )

            topic_orders = {
                code: order
                for code in topic_codes
            }

        if (
            not isinstance(topic_orders, dict)
            or set(topic_orders) != set(topic_codes)
            or any(
                type(value) is not int
                or value < 0
                for value in topic_orders.values()
            )
        ):
            raise CommandError(
                f"{source_id}: invalid topic_orders"
            )

        lemma = as_text(
            row,
            "lemma",
            required=True,
            limit=200,
        )

        meaning_vi = as_text(
            row,
            "meaning_vi",
            limit=300,
        )

        meaning_en = as_text(
            row,
            "meaning_en",
            limit=500,
        )

        if not meaning_vi and not meaning_en:
            raise CommandError(
                f"{source_id}: requires meaning_vi or meaning_en"
            )

        source_entries = validate_source_entries(
            row,
            lemma,
            topic_codes,
        )

        for entry in source_entries:

            key = (
                entry["level"],
                entry["row_number"],
            )

            if key in seen_source_rows:
                raise CommandError(
                    f"Duplicate source row: {key}"
                )

            seen_source_rows.add(key)

            topic = f"level-{entry['level'].lower()}"

            if (
                topic_orders[topic]
                != entry["row_number"]
            ):
                raise CommandError(
                    f"{source_id}: source order mismatch"
                )

        items.append({
            "source_id": source_id,
            "lemma": lemma,

            "stress_marked": as_text(
                row,
                "stress_marked",
                limit=200,
            ),

            "meaning_vi": meaning_vi,
            "meaning_en": meaning_en,

            "part_of_speech": as_text(
                row,
                "part_of_speech",
                limit=50,
            ),

            "cefr_level": as_level(row),

            "example_ru": as_text(
                row,
                "example_ru",
            ),

            "example_vi": as_text(
                row,
                "example_vi",
            ),

            "notes_vi": as_text(
                row,
                "notes_vi",
            ),

            "audio_url": as_text(
                row,
                "audio_url",
                limit=500,
            ),

            "is_active": as_bool(
                row,
                "is_active",
            ),

            "source_entries": source_entries,

            "topic_codes": topic_codes,
            "topic_orders": topic_orders,
        })

    sha = hashlib.sha256(raw).hexdigest()

    return sha, topics, items


# ============================================================
# PHASE 1 - VALIDATE VOC SKILL REFERENCES
# ============================================================

def resolve_skills(topics):

    codes = {
        topic["assessment_skill_code"]
        for topic in topics
        if topic["assessment_skill_code"]
    }

    if not codes:
        return {}

    found = {
        skill.skill_code: skill

        for skill in (
            Skill.objects
            .filter(skill_code__in=codes)
            .select_related("topic")
        )
    }

    for code in codes:

        if (
            code not in found
            or found[code].topic.category != "vocabulary"
        ):
            raise CommandError(
                f"Invalid vocabulary skill {code}"
            )

    return found


# ============================================================
# PHASE 1 - COMPARE WITH DATABASE
# ============================================================

def plan_changes(
    topics,
    items,
    skills,
):

    existing_topics = {
        topic.code: topic

        for topic in (
            VocabularyTopic.objects.filter(
                code__in=[
                    row["code"]
                    for row in topics
                ]
            )
        )
    }

    existing_items = {
        item.source_id: item

        for item in (
            VocabularyItem.objects.filter(
                source_id__in=[
                    row["source_id"]
                    for row in items
                ]
            )
        )
    }

    existing_ids = [
        item.pk
        for item in existing_items.values()
    ]

    started_ids = set(
        VocabularySRSState.objects.filter(
            item_id__in=existing_ids
        ).values_list(
            "item_id",
            flat=True,
        )
    )

    report = {
        "topics_create": 0,
        "topics_update": 0,
        "items_create": 0,
        "items_update": 0,
        "items_unchanged": 0,
        "membership_create": 0,
        "membership_update": 0,
        "membership_delete": 0,
    }

    # TOPIC CHANGES
    for row in topics:

        old = existing_topics.get(
            row["code"]
        )

        if old is None:
            report["topics_create"] += 1
            continue

        new_skill = skills.get(
            row["assessment_skill_code"]
        )

        changed = any(
            getattr(old, field) != row[field]
            for field in TOPIC_FIELDS
        )

        changed = (
            changed
            or old.assessment_skill_id != (
                new_skill.pk
                if new_skill
                else None
            )
        )

        report["topics_update"] += int(changed)

    # ITEM CHANGES
    for row in items:

        old = existing_items.get(
            row["source_id"]
        )

        if old is None:

            report["items_create"] += 1

            report["membership_create"] += len(
                row["topic_codes"]
            )

            continue

        changed_fields = {
            field
            for field in ITEM_FIELDS
            if getattr(old, field) != row[field]
        }

        protected_changed = (
            changed_fields & PROTECTED_AFTER_START
        )

        if (
            old.pk in started_ids
            and protected_changed
        ):
            raise CommandError(
                f"BLOCKED {row['source_id']}: "
                "studied content changed: "
                + ", ".join(
                    sorted(protected_changed)
                )
            )

        memberships = {
            link.topic.code: link
            for link in (
                old.topic_links.select_related("topic")
            )
        }

        new_codes = set(
            row["topic_codes"]
        )

        for code in new_codes:

            if code not in memberships:
                report["membership_create"] += 1

            elif (
                memberships[code].display_order
                != row["topic_orders"][code]
            ):
                report["membership_update"] += 1

        report["membership_delete"] += len(
            set(memberships) - new_codes
        )

        membership_changed = (
            new_codes != set(memberships)
        )

        order_changed = any(
            memberships[code].display_order
            != row["topic_orders"][code]
            for code in new_codes
            if code in memberships
        )

        if (
            changed_fields
            or membership_changed
            or order_changed
        ):
            report["items_update"] += 1

        else:
            report["items_unchanged"] += 1

    return report


# ============================================================
# PHASE 2 - APPLY DATA
# ============================================================

def apply_changes(
    topics,
    items,
    skills,
):

    topic_map = {}

    # --------------------------------------------------------
    # CREATE / UPDATE TOPICS
    # --------------------------------------------------------

    for row in topics:

        defaults = {
            field: row[field]
            for field in TOPIC_FIELDS
        }

        skill = skills.get(
            row["assessment_skill_code"]
        )

        obj, created = VocabularyTopic.objects.get_or_create(
            code=row["code"],
            defaults={
                **defaults,
                "assessment_skill": skill,
            },
        )

        if not created:

            changed = []

            for field, value in defaults.items():

                if getattr(obj, field) != value:
                    setattr(obj, field, value)
                    changed.append(field)

            expected_skill_id = (
                skill.pk
                if skill
                else None
            )

            if obj.assessment_skill_id != expected_skill_id:
                obj.assessment_skill = skill
                changed.append("assessment_skill")

            if changed:
                obj.save(
                    update_fields=changed
                )

        topic_map[row["code"]] = obj

    # --------------------------------------------------------
    # CREATE / UPDATE ITEMS
    # --------------------------------------------------------

    for row in items:

        values = {
            field: row[field]
            for field in ITEM_FIELDS
        }

        obj, created = VocabularyItem.objects.get_or_create(
            source_id=row["source_id"],
            defaults=values,
        )

        if not created:

            changed = []

            for field, value in values.items():

                if getattr(obj, field) != value:
                    setattr(obj, field, value)
                    changed.append(field)

            if changed:
                obj.save(
                    update_fields=changed + [
                        "updated_at"
                    ]
                )

        # ----------------------------------------------------
        # ITEM -> TOPIC LINKS
        # ----------------------------------------------------

        old_links = {
            link.topic.code: link

            for link in (
                obj.topic_links.select_related(
                    "topic"
                )
            )
        }

        for code in row["topic_codes"]:

            order = row["topic_orders"][code]

            link = old_links.get(code)

            if link is None:

                VocabularyItemTopic.objects.create(
                    item=obj,
                    topic=topic_map[code],
                    display_order=order,
                )

            elif link.display_order != order:

                link.display_order = order

                link.save(
                    update_fields=[
                        "display_order"
                    ]
                )

        removed = (
            set(old_links)
            - set(row["topic_codes"])
        )

        if removed:

            obj.topic_links.filter(
                topic__code__in=removed
            ).delete()


# ============================================================
# MANAGEMENT COMMAND
# ============================================================

class Command(BaseCommand):

    help = (
        "Import Privet Vocabulary JSON "
        "using validate-only / dry-run / commit."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "file",
            type=Path,
        )

        modes = parser.add_mutually_exclusive_group(
            required=True
        )

        modes.add_argument(
            "--validate-only",
            action="store_true",
        )

        modes.add_argument(
            "--dry-run",
            action="store_true",
        )

        modes.add_argument(
            "--commit",
            action="store_true",
        )

    def handle(self, *args, **options):

        sha, topics, items = parse_document(
            options["file"]
        )

        skills = resolve_skills(
            topics
        )

        report = plan_changes(
            topics,
            items,
            skills,
        )

        self.stdout.write(
            f"File SHA-256: {sha}"
        )

        self.stdout.write(
            f"Topics: {len(topics)}; "
            f"unique items: {len(items)}"
        )

        self.stdout.write(
            json.dumps(
                report,
                sort_keys=True,
            )
        )

        if options["validate_only"]:

            self.stdout.write(
                "VALIDATED (no DB writes)"
            )
            return

        with transaction.atomic():

            # Recheck immediately before changing DB.
            plan_changes(
                topics,
                items,
                skills,
            )

            apply_changes(
                topics,
                items,
                skills,
            )

            if options["dry_run"]:
                transaction.set_rollback(True)

        if options["dry_run"]:
            self.stdout.write(
                "DRY RUN ROLLED BACK"
            )

        else:
            self.stdout.write(
                "COMMIT OK"
            )
