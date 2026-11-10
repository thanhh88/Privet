from django.core.management.base import BaseCommand
from django.utils import timezone

from learning_engine.mastery import (
    recalculate_skill_mastery,
)
from learning_engine.models import SkillMastery


class Command(BaseCommand):
    help = (
        "Rebuild existing SkillMastery rows from "
        "authoritative assessment history so "
        "last_review_evidence_at is backfilled."
    )

    def handle(self, *args, **options):
        as_of = timezone.now()

        states = (
            SkillMastery.objects
            .select_related(
                "user",
                "skill",
            )
            .order_by(
                "user_id",
                "skill_id",
            )
        )

        total = states.count()
        rebuilt = 0

        for state in states.iterator():
            recalculate_skill_mastery(
                user=state.user,
                skill_id=state.skill_id,
                as_of=as_of,
            )

            rebuilt += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Rebuilt {rebuilt} of "
                f"{total} SkillMastery rows."
            )
        )