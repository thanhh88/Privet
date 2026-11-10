from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from decimal import Decimal

DEFAULT_PREREQUISITE_MASTERY = Decimal("0.7000")

SKILL_CODE_VALIDATOR = RegexValidator(
    regex=r"^[A-Z0-9][A-Z0-9._-]*$",
    message=(
        "Skill code must start with A-Z or 0-9 and contain only "
        "A-Z, 0-9, '.', '_' or '-'."
    ),
)

class Topic(models.Model):
    topic_id = models.BigAutoField(
        primary_key=True,
    )

    topic_name = models.CharField(
        max_length=150,
        unique=True,
    )

    category = models.CharField(
        max_length=100,
    )

    description = models.TextField(
        null=True,
        blank=True,
    )

    display_order = models.PositiveIntegerField(
        default=0,
    )

    is_active = models.BooleanField(
        default=True,
    )

    class Meta:
        db_table = "topic"
        ordering = ("display_order", "topic_id")

    def __str__(self):
        return self.topic_name


class Skill(models.Model):
    class CefrLevel(models.TextChoices):
        A1 = "A1", "A1"
        A2 = "A2", "A2"
        B1 = "B1", "B1"

    skill_id = models.BigAutoField(
        primary_key=True,
    )

    skill_code = models.CharField(
        max_length=64,
        unique=True,
        validators=[SKILL_CODE_VALIDATOR],
    )

    topic = models.ForeignKey(  #create 1 database
        Topic,
        on_delete=models.PROTECT,
        related_name="skills",
    )

    skill_name = models.CharField(
        max_length=150,
    )

    description = models.TextField(
        null=True,
        blank=True,
    )

    cefr_level = models.CharField(
        max_length=2,
        choices=CefrLevel.choices,
    )

    is_extension = models.BooleanField(
        default=False,
    )

    display_order = models.PositiveIntegerField(
        default=0,
    )

    is_active = models.BooleanField(
        default=True,
    )

    class Meta:
        db_table = "skill"

        constraints = [
            models.UniqueConstraint(
                fields=["topic", "skill_name"],
                name="uq_skill_topic_name",
            ),

            models.CheckConstraint(
                condition=models.Q(
                    cefr_level__in=[
                        "A1",
                        "A2",
                        "B1",
                    ]
                ),
                name="ck_skill_cefr",
            ),

            models.CheckConstraint(
                condition=~models.Q(skill_code=""),
                name="ck_skill_code_nonblank",
            ),
        ]

    def __str__(self):
        return f"{self.skill_name} ({self.cefr_level})"


class SkillPrerequisite(models.Model):
    skill_prerequisite_id = models.BigAutoField(
        primary_key=True,
    )

    # Directed edge: skill -> prerequisite_skill.
    skill = models.ForeignKey(
        Skill,
        on_delete=models.PROTECT,
        related_name="prerequisite_links",
    )

    prerequisite_skill = models.ForeignKey(
        Skill,
        on_delete=models.PROTECT,
        related_name="dependent_links",
    )

    required_mastery = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=DEFAULT_PREREQUISITE_MASTERY,
        validators=[
            MinValueValidator(Decimal("0")),
            MaxValueValidator(Decimal("1")),
        ],
    )

    is_active = models.BooleanField(
        default=True,
    )

    class Meta:
        db_table = "skill_prerequisite"
        ordering = (
            "skill_id",
            "prerequisite_skill_id",
        )

        constraints = [
            models.UniqueConstraint(
                fields=(
                    "skill",
                    "prerequisite_skill",
                ),
                name="uq_skill_prerequisite_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(
                    skill=models.F("prerequisite_skill"),
                ),
                name="ck_skill_prerequisite_not_self",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(required_mastery__gte=0)
                    & models.Q(required_mastery__lte=1)
                ),
                name="ck_skill_prerequisite_mastery",
            ),
        ]

        indexes = [
            models.Index(
                fields=(
                    "skill",
                    "is_active",
                ),
                name="idx_skill_prereq_skill",
            ),
            models.Index(
                fields=(
                    "prerequisite_skill",
                    "is_active",
                ),
                name="idx_skill_prereq_req",
            ),
        ]

    def clean(self):
        super().clean()

        if (
            self.skill_id is not None
            and self.prerequisite_skill_id is not None
            and self.skill_id == self.prerequisite_skill_id
        ):
            raise ValidationError(
                {
                    "prerequisite_skill": (
                        "A skill cannot be its own prerequisite."
                    )
                }
            )

        if (
            self.is_active
            and self.skill_id is not None
            and self.prerequisite_skill_id is not None
        ):
            from curriculum.prerequisites import (
                would_create_prerequisite_cycle,
            )

            if would_create_prerequisite_cycle(
                skill_id=self.skill_id,
                prerequisite_skill_id=(
                    self.prerequisite_skill_id
                ),
                exclude_edge_id=self.pk,
            ):
                raise ValidationError(
                    {
                        "prerequisite_skill": (
                            "This prerequisite would create "
                            "a cycle in the skill graph."
                        )
                    }
                )

    def __str__(self):
        return (
            f"{self.skill.skill_code} requires "
            f"{self.prerequisite_skill.skill_code} "
            f"at {self.required_mastery}"
        )


class Lesson(models.Model):
    lesson_id = models.BigAutoField(
        primary_key=True,
    )

    title = models.CharField(
        max_length=200,
    )

    lesson_type = models.CharField(
        max_length=32,
    )

    content = models.JSONField()

    audio_url = models.TextField(
        null=True,
        blank=True,
    )

    display_order = models.IntegerField(
        default=0,
    )

    is_active = models.BooleanField(
        default=True,
    )

    class Meta:
        db_table = "lesson"
        ordering = ("display_order", "lesson_id")

    def __str__(self):
        return self.title

class LessonSkill(models.Model):
    lesson_skill_id = models.BigAutoField(
        primary_key=True,
    )

    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.PROTECT,
        related_name="lesson_skills",
    )

    skill = models.ForeignKey(
        Skill,
        on_delete=models.PROTECT,
        related_name="lesson_skills",
    )

    class Meta:
        db_table = "lesson_skill"

        constraints = [
            models.UniqueConstraint(
                fields=("lesson", "skill"),
                name="uq_lesson_skill_pair",
            ),
        ]

        indexes = [
            models.Index(
                fields=("skill", "lesson"),
                name="idx_lesson_skill_skill",
            ),
        ]

    def __str__(self):
        return f"{self.lesson} -> {self.skill}"

class LessonProgress(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    lesson_progress_id = models.BigAutoField(
        primary_key=True,
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="lesson_progress",
    )

    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.PROTECT,
        related_name="progress_records",
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
    )

    completion_ratio = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=0,
    )

    started_at = models.DateTimeField(
        auto_now_add=True,
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    last_accessed_at = models.DateTimeField(
        default=timezone.now,
    )

    class Meta:
        db_table = "lesson_progress"

        constraints = [
            models.UniqueConstraint(
                fields=("user", "lesson"),
                name="uq_lesson_progress_user_lesson",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(completion_ratio__gte=0)
                    & models.Q(completion_ratio__lte=1)
                ),
                name="ck_lesson_progress_completion_ratio",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "in_progress",
                        "completed",
                    ]
                ),
                name="ck_lesson_progress_status",
            ),
            models.CheckConstraint(
                condition=(
                        models.Q(
                            status="completed",
                            completed_at__isnull=False,
                        )
                        |
                        (
                                ~models.Q(status="completed")
                                & models.Q(completed_at__isnull=True)
                        )
                ),
                name="ck_lesson_progress_completed_state",
            ),
        ]

        indexes = [
            models.Index(
                fields=("user", "-last_accessed_at"),
                name="idx_lp_user_recent",
            ),
        ]

    def __str__(self):
        return f"{self.user} - {self.lesson} - {self.status}"