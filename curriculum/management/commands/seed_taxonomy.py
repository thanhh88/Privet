from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from curriculum.models import Skill, Topic
from curriculum.taxonomy import SKILLS, TOPICS


class Command(BaseCommand):
    help = (
        "Seed the canonical Privet curriculum taxonomy "
        "without deleting unrelated data."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Run all validation and database operations, "
                "then roll back the transaction."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        self._validate_taxonomy_definition()

        try:
            with transaction.atomic():
                topic_stats = self._seed_topics()
                skill_stats = self._seed_skills()

                if dry_run:
                    transaction.set_rollback(True)

        except ValidationError as exc:
            raise CommandError(
                f"Taxonomy validation failed: {exc}"
            ) from exc

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN: all database changes were rolled back."
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Topics: "
                f"{topic_stats['created']} created, "
                f"{topic_stats['updated']} updated, "
                f"{topic_stats['unchanged']} unchanged."
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Skills: "
                f"{skill_stats['created']} created, "
                f"{skill_stats['updated']} updated, "
                f"{skill_stats['unchanged']} unchanged."
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Taxonomy seed completed successfully."
            )
        )

    def _validate_taxonomy_definition(self):
        """
        Validate the static taxonomy before touching the database.
        """
        if len(TOPICS) != 24:
            raise CommandError(
                f"Expected 24 topics, found {len(TOPICS)}."
            )

        if len(SKILLS) != 59:
            raise CommandError(
                f"Expected 59 skills, found {len(SKILLS)}."
            )

        topic_names = [
            row["topic_name"]
            for row in TOPICS
        ]

        if len(topic_names) != len(set(topic_names)):
            raise CommandError(
                "Duplicate topic_name found in taxonomy."
            )

        skill_codes = [
            row["skill_code"]
            for row in SKILLS
        ]

        if len(skill_codes) != len(set(skill_codes)):
            raise CommandError(
                "Duplicate skill_code found in taxonomy."
            )

        topic_name_set = set(topic_names)

        missing_topics = sorted(
            {
                row["topic_name"]
                for row in SKILLS
                if row["topic_name"] not in topic_name_set
            }
        )

        if missing_topics:
            raise CommandError(
                "Skills reference undefined topics: "
                + ", ".join(missing_topics)
            )

        semantic_keys = [
            (
                row["topic_name"],
                row["skill_name"],
            )
            for row in SKILLS
        ]

        if len(semantic_keys) != len(set(semantic_keys)):
            raise CommandError(
                "Duplicate (topic_name, skill_name) "
                "found in taxonomy."
            )

        extension_skills = {
            row["skill_code"]
            for row in SKILLS
            if row["is_extension"]
        }

        expected_extension_skills = {
            "MOVE-05",
            "SENT-05",
            "VOC-14",
            "VOC-15",
        }

        if extension_skills != expected_extension_skills:
            raise CommandError(
                "Unexpected extension skill set. "
                f"Expected {sorted(expected_extension_skills)}, "
                f"found {sorted(extension_skills)}."
            )

        for row in SKILLS:
            if (
                row["is_extension"]
                and row["cefr_level"] != "B1"
            ):
                raise CommandError(
                    f'{row["skill_code"]}: extension skill '
                    'must have cefr_level="B1".'
                )

    def _seed_topics(self):
        stats = {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
        }

        for row in TOPICS:
            topic = Topic.objects.filter(
                topic_name=row["topic_name"]
            ).first()

            if topic is None:
                topic = Topic(
                    topic_name=row["topic_name"],
                    category=row["category"],
                    display_order=row["display_order"],
                    is_active=True,
                )

                topic.full_clean()
                topic.save()

                stats["created"] += 1
                continue

            changed = False

            desired_values = {
                "category": row["category"],
                "display_order": row["display_order"],
                "is_active": True,
            }

            for field_name, desired_value in desired_values.items():
                if getattr(topic, field_name) != desired_value:
                    setattr(
                        topic,
                        field_name,
                        desired_value,
                    )
                    changed = True

            if changed:
                topic.full_clean()
                topic.save(
                    update_fields=[
                        "category",
                        "display_order",
                        "is_active",
                    ]
                )
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1

        return stats

    def _seed_skills(self):
        stats = {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
        }

        topics = {
            topic.topic_name: topic
            for topic in Topic.objects.filter(
                topic_name__in=[
                    row["topic_name"]
                    for row in TOPICS
                ]
            )
        }

        if len(topics) != len(TOPICS):
            raise CommandError(
                "Not all canonical topics are available "
                "after topic seeding."
            )

        # Preflight all semantic collisions before writing any Skill.
        for row in SKILLS:
            target_topic = topics[row["topic_name"]]

            existing_by_code = (
                Skill.objects
                .select_related("topic")
                .filter(
                    skill_code=row["skill_code"]
                )
                .first()
            )

            if existing_by_code is not None:
                if (
                    existing_by_code.topic.topic_name
                    != row["topic_name"]
                    or existing_by_code.skill_name
                    != row["skill_name"]
                ):
                    raise CommandError(
                        "Stable skill_code collision: "
                        f'{row["skill_code"]} already exists as '
                        f'"{existing_by_code.skill_name}" '
                        f'in topic '
                        f'"{existing_by_code.topic.topic_name}", '
                        "but the canonical taxonomy assigns it to "
                        f'"{row["skill_name"]}" in '
                        f'"{row["topic_name"]}". '
                        "A stable skill_code must never be reused "
                        "for a different semantic meaning."
                    )

            semantic_collision = (
                Skill.objects
                .filter(
                    topic=target_topic,
                    skill_name=row["skill_name"],
                )
                .exclude(
                    skill_code=row["skill_code"]
                )
                .first()
            )

            if semantic_collision is not None:
                raise CommandError(
                    "Skill semantic collision: "
                    f'"{row["skill_name"]}" in topic '
                    f'"{row["topic_name"]}" already exists with '
                    f'skill_code="{semantic_collision.skill_code}", '
                    f'but taxonomy expects "{row["skill_code"]}".'
                )

        # Only write after all Skill collisions have passed.
        for row in SKILLS:
            target_topic = topics[row["topic_name"]]

            skill = Skill.objects.filter(
                skill_code=row["skill_code"]
            ).first()

            if skill is None:
                skill = Skill(
                    skill_code=row["skill_code"],
                    topic=target_topic,
                    skill_name=row["skill_name"],
                    cefr_level=row["cefr_level"],
                    is_extension=row["is_extension"],
                    display_order=row["display_order"],
                    is_active=True,
                )

                skill.full_clean()
                skill.save()

                stats["created"] += 1
                continue

            changed = False

            desired_values = {
                "cefr_level": row["cefr_level"],
                "is_extension": row["is_extension"],
                "display_order": row["display_order"],
                "is_active": True,
            }

            for field_name, desired_value in desired_values.items():
                if getattr(skill, field_name) != desired_value:
                    setattr(
                        skill,
                        field_name,
                        desired_value,
                    )
                    changed = True

            if changed:
                skill.full_clean()
                skill.save(
                    update_fields=[
                        "cefr_level",
                        "is_extension",
                        "display_order",
                        "is_active",
                    ]
                )
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1

        return stats