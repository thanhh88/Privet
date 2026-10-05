from django.conf import settings
from django.db import models
from django.utils import timezone


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
        ]

    def __str__(self):
        return f"{self.skill_name} ({self.cefr_level})"

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