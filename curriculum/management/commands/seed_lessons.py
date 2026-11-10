import json
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from curriculum.models import Lesson, LessonSkill, Skill


EXPECTED_SCHEMA_VERSION = "privet_lessons_v1"
ALLOWED_LESSON_TYPES = {"grammar", "vocabulary"}

ROOT_KEYS = {"schema_version", "scope", "lessons"}
LESSON_KEYS = {
    "lesson_key",
    "title",
    "lesson_type",
    "display_order",
    "is_active",
    "audio_url",
    "skill_codes",
    "content",
}
CONTENT_KEYS = {"objective_vi", "theory", "examples", "practice"}


class Command(BaseCommand):
    help = (
        "Seed canonical Privet A1 beginner lessons. "
        "Validate the complete file before writing and "
        "never silently overwrite changed content."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=str(
                Path(settings.BASE_DIR)
                / "data"
                / "canonical"
                / "privet_lessons_v1.json"
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        dry_run = bool(options["dry_run"])

        payload = self._load_payload(path)
        lessons = self._validate_payload(payload)
        skill_by_code = self._resolve_and_validate_skills(lessons)

        created = 0
        skipped = 0

        with transaction.atomic():
            for item in lessons:
                outcome = self._seed_one_lesson(
                    item=item,
                    skill_by_code=skill_by_code,
                )
                if outcome == "created":
                    created += 1
                else:
                    skipped += 1

            if dry_run:
                transaction.set_rollback(True)

        label = "DRY RUN" if dry_run else "COMMITTED"
        self.stdout.write(
            self.style.SUCCESS(
                f"{label}: lessons={len(lessons)}, "
                f"created={created}, skipped={skipped}, "
                f"skills={len(skill_by_code)}"
            )
        )

    def _load_payload(self, path):
        if not path.exists():
            raise CommandError(
                f"Lesson seed file does not exist: {path}"
            )

        try:
            return json.loads(
                path.read_text(encoding="utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise CommandError(
                f"Lesson seed file must be valid UTF-8 JSON: {exc}"
            ) from exc

    def _validate_payload(self, payload):
        if not isinstance(payload, dict):
            raise CommandError(
                "Lesson seed root must be an object."
            )

        self._check_exact_keys(
            actual=set(payload),
            expected=ROOT_KEYS,
            label="root",
        )

        if payload["schema_version"] != EXPECTED_SCHEMA_VERSION:
            raise CommandError(
                "Unsupported schema_version: "
                f"{payload['schema_version']!r}"
            )

        lessons = payload["lessons"]
        if not isinstance(lessons, list) or not lessons:
            raise CommandError(
                "lessons must be a non-empty list."
            )

        normalized = []
        lesson_keys = []
        titles = []
        display_orders = []
        all_skill_codes = []
        practice_ids = []

        for index, raw in enumerate(lessons, start=1):
            item = self._validate_lesson(
                raw=raw,
                index=index,
            )
            normalized.append(item)
            lesson_keys.append(item["lesson_key"])
            titles.append(item["title"])
            display_orders.append(item["display_order"])
            all_skill_codes.extend(item["skill_codes"])
            practice_ids.extend(
                practice["id"]
                for practice in item["content"]["practice"]
            )

        self._require_unique(
            lesson_keys,
            "lesson_key",
        )
        self._require_unique(
            titles,
            "title",
        )
        self._require_unique(
            display_orders,
            "display_order",
        )
        self._require_unique(
            all_skill_codes,
            "skill_code across all lessons",
        )
        self._require_unique(
            practice_ids,
            "practice id",
        )

        return normalized

    def _validate_lesson(self, *, raw, index):
        if not isinstance(raw, dict):
            raise CommandError(
                f"Lesson #{index} must be an object."
            )

        self._check_exact_keys(
            actual=set(raw),
            expected=LESSON_KEYS,
            label=f"lesson #{index}",
        )

        lesson_key = self._require_text(
            raw["lesson_key"],
            f"lessons[{index}].lesson_key",
        )
        title = self._require_text(
            raw["title"],
            f"{lesson_key}.title",
        )
        lesson_type = self._require_text(
            raw["lesson_type"],
            f"{lesson_key}.lesson_type",
        )

        if lesson_type not in ALLOWED_LESSON_TYPES:
            raise CommandError(
                f"{lesson_key}: unsupported lesson_type "
                f"{lesson_type!r}."
            )

        display_order = raw["display_order"]
        if (
            isinstance(display_order, bool)
            or not isinstance(display_order, int)
            or display_order < 1
        ):
            raise CommandError(
                f"{lesson_key}: display_order must be "
                "a positive integer."
            )

        if not isinstance(raw["is_active"], bool):
            raise CommandError(
                f"{lesson_key}: is_active must be boolean."
            )

        audio_url = raw["audio_url"]
        if audio_url is not None and not isinstance(audio_url, str):
            raise CommandError(
                f"{lesson_key}: audio_url must be string or null."
            )

        raw_codes = raw["skill_codes"]
        if not isinstance(raw_codes, list) or not raw_codes:
            raise CommandError(
                f"{lesson_key}: skill_codes must be a "
                "non-empty list."
            )

        skill_codes = [
            self._require_text(
                value,
                f"{lesson_key}.skill_codes",
            )
            for value in raw_codes
        ]
        self._require_unique(
            skill_codes,
            f"{lesson_key}.skill_codes",
        )

        content = raw["content"]
        self._validate_content(
            content=content,
            lesson_key=lesson_key,
        )

        return {
            "lesson_key": lesson_key,
            "title": title,
            "lesson_type": lesson_type,
            "display_order": display_order,
            "is_active": raw["is_active"],
            "audio_url": audio_url,
            "skill_codes": skill_codes,
            "content": content,
        }

    def _validate_content(self, *, content, lesson_key):
        if not isinstance(content, dict):
            raise CommandError(
                f"{lesson_key}: content must be an object."
            )

        self._check_exact_keys(
            actual=set(content),
            expected=CONTENT_KEYS,
            label=f"{lesson_key}.content",
        )

        self._require_text(
            content["objective_vi"],
            f"{lesson_key}.content.objective_vi",
        )

        for field in ("theory", "examples", "practice"):
            value = content[field]
            if not isinstance(value, list) or not value:
                raise CommandError(
                    f"{lesson_key}.content.{field} must "
                    "be a non-empty list."
                )

        for text_index, theory in enumerate(
            content["theory"],
            start=1,
        ):
            self._require_text(
                theory,
                f"{lesson_key}.theory[{text_index}]",
            )

        for example_index, example in enumerate(
            content["examples"],
            start=1,
        ):
            if (
                not isinstance(example, dict)
                or set(example) != {"ru", "vi"}
            ):
                raise CommandError(
                    f"{lesson_key}.examples[{example_index}] "
                    "must contain exactly ru and vi."
                )
            self._require_text(
                example["ru"],
                f"{lesson_key}.examples[{example_index}].ru",
            )
            self._require_text(
                example["vi"],
                f"{lesson_key}.examples[{example_index}].vi",
            )

        for practice_index, practice in enumerate(
            content["practice"],
            start=1,
        ):
            self._validate_practice(
                practice=practice,
                label=(
                    f"{lesson_key}.practice[{practice_index}]"
                ),
            )

    def _validate_practice(self, *, practice, label):
        expected = {
            "id",
            "type",
            "prompt",
            "options",
            "correct_option_key",
            "feedback_vi",
        }

        if not isinstance(practice, dict):
            raise CommandError(
                f"{label} must be an object."
            )

        self._check_exact_keys(
            actual=set(practice),
            expected=expected,
            label=label,
        )

        practice_id = self._require_text(
            practice["id"],
            f"{label}.id",
        )

        if practice["type"] != "single_choice":
            raise CommandError(
                f"{practice_id}: only single_choice "
                "practice is supported in v1."
            )

        self._require_text(
            practice["prompt"],
            f"{practice_id}.prompt",
        )
        self._require_text(
            practice["feedback_vi"],
            f"{practice_id}.feedback_vi",
        )

        options = practice["options"]
        if not isinstance(options, list) or len(options) < 2:
            raise CommandError(
                f"{practice_id}: options must contain "
                "at least two choices."
            )

        option_keys = []

        for option_index, option in enumerate(
            options,
            start=1,
        ):
            if (
                not isinstance(option, dict)
                or set(option) != {"key", "text"}
            ):
                raise CommandError(
                    f"{practice_id}.options[{option_index}] "
                    "must contain exactly key and text."
                )

            option_keys.append(
                self._require_text(
                    option["key"],
                    f"{practice_id}.options[{option_index}].key",
                )
            )
            self._require_text(
                option["text"],
                f"{practice_id}.options[{option_index}].text",
            )

        self._require_unique(
            option_keys,
            f"{practice_id} option keys",
        )

        answer = self._require_text(
            practice["correct_option_key"],
            f"{practice_id}.correct_option_key",
        )

        if answer not in option_keys:
            raise CommandError(
                f"{practice_id}: correct_option_key "
                "must reference an existing option."
            )

    def _resolve_and_validate_skills(self, lessons):
        requested_codes = [
            code
            for item in lessons
            for code in item["skill_codes"]
        ]

        skills = list(
            Skill.objects
            .filter(skill_code__in=requested_codes)
            .select_related("topic")
        )
        skill_by_code = {
            skill.skill_code: skill
            for skill in skills
        }

        missing = set(requested_codes) - set(skill_by_code)
        if missing:
            raise CommandError(
                f"Unknown skill_codes: {sorted(missing)}"
            )

        for code in requested_codes:
            skill = skill_by_code[code]
            if (
                skill.cefr_level != "A1"
                or skill.is_extension
                or not skill.is_active
                or not skill.topic.is_active
            ):
                raise CommandError(
                    f"{code} is not an active core A1 Skill."
                )

        db_a1_codes = set(
            Skill.objects
            .filter(
                cefr_level="A1",
                is_extension=False,
                is_active=True,
                topic__is_active=True,
            )
            .values_list("skill_code", flat=True)
        )
        file_a1_codes = set(requested_codes)

        if file_a1_codes != db_a1_codes:
            raise CommandError(
                "Canonical A1 coverage does not match "
                "the database. Missing from file="
                f"{sorted(db_a1_codes - file_a1_codes)}; "
                "extra in file="
                f"{sorted(file_a1_codes - db_a1_codes)}."
            )

        return skill_by_code

    def _seed_one_lesson(self, *, item, skill_by_code):
        title = item["title"]

        existing = list(
            Lesson.objects.filter(title=title)[:2]
        )
        if len(existing) > 1:
            raise CommandError(
                f"More than one Lesson uses title {title!r}."
            )

        order_conflict = (
            Lesson.objects
            .filter(display_order=item["display_order"])
            .exclude(title=title)
            .exists()
        )
        if order_conflict:
            raise CommandError(
                "Another Lesson already uses "
                f"display_order {item['display_order']}."
            )

        if existing:
            lesson = existing[0]
            differences = []

            expected_fields = {
                "lesson_type": item["lesson_type"],
                "content": item["content"],
                "audio_url": item["audio_url"],
                "display_order": item["display_order"],
                "is_active": item["is_active"],
            }

            for field, expected in expected_fields.items():
                if getattr(lesson, field) != expected:
                    differences.append(field)

            actual_codes = set(
                LessonSkill.objects
                .filter(lesson=lesson)
                .values_list(
                    "skill__skill_code",
                    flat=True,
                )
            )
            expected_codes = set(item["skill_codes"])

            if actual_codes != expected_codes:
                differences.append("skill_codes")

            if differences:
                raise CommandError(
                    f"Existing Lesson {title!r} differs "
                    "from canonical seed in "
                    f"{sorted(differences)}. Refusing "
                    "to overwrite."
                )

            return "skipped"

        lesson = Lesson(
            title=title,
            lesson_type=item["lesson_type"],
            content=item["content"],
            audio_url=item["audio_url"],
            display_order=item["display_order"],
            is_active=item["is_active"],
        )
        lesson.full_clean()
        lesson.save()

        for skill_code in item["skill_codes"]:
            mapping = LessonSkill(
                lesson=lesson,
                skill=skill_by_code[skill_code],
            )
            mapping.full_clean()
            mapping.save()

        return "created"

    def _check_exact_keys(
        self,
        *,
        actual,
        expected,
        label,
    ):
        unknown = actual - expected
        missing = expected - actual

        if unknown:
            raise CommandError(
                f"{label}: unknown keys {sorted(unknown)}."
            )
        if missing:
            raise CommandError(
                f"{label}: missing keys {sorted(missing)}."
            )

    def _require_text(self, value, field):
        if not isinstance(value, str):
            raise CommandError(
                f"{field} must be a string."
            )

        value = value.strip()
        if not value:
            raise CommandError(
                f"{field} must not be blank."
            )
        return value

    def _require_unique(self, values, label):
        counts = Counter(values)
        duplicates = sorted(
            value
            for value, count in counts.items()
            if count > 1
        )

        if duplicates:
            raise CommandError(
                f"Duplicate {label}: {duplicates}"
            )
