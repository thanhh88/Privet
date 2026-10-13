from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class StudySession(models.Model):
    """
    Active study-time tracking session.

    This is separate from learning_engine.PracticeSession.

    PracticeSession represents an assessment / lesson
    workflow.

    StudySession represents behavioral study tracking:
    lesson, guided practice, or assessment activity.
    """

    class ActivityType(models.TextChoices):
        LESSON = "lesson", "Lesson"
        PRACTICE = "practice", "Practice"
        TEST = "test", "Test"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"

    study_session_id = models.BigAutoField(
        primary_key=True,
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="study_sessions",
    )

    lesson = models.ForeignKey(
        "curriculum.Lesson",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="study_sessions",
    )

    # Current Privet architecture uses PracticeSession
    # for adaptive / diagnostic assessments.
    assessment_session = models.ForeignKey(
        "learning_engine.PracticeSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="study_sessions",
    )

    activity_type = models.CharField(
        max_length=16,
        choices=ActivityType.choices,
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    started_at = models.DateTimeField(
        default=timezone.now,
    )

    last_activity_at = models.DateTimeField(
        default=timezone.now,
    )

    ended_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    active_seconds = models.PositiveIntegerField(
        default=0,
    )

    class Meta:
        db_table = "study_session"

        ordering = [
            "-started_at",
            "-study_session_id",
        ]

        constraints = [
            models.CheckConstraint(
                condition=Q(
                    activity_type__in=[
                        "lesson",
                        "practice",
                        "test",
                    ]
                ),
                name="ck_study_session_activity_type",
            ),

            models.CheckConstraint(
                condition=Q(
                    status__in=[
                        "active",
                        "paused",
                        "completed",
                        "abandoned",
                    ]
                ),
                name="ck_study_session_status",
            ),

            models.CheckConstraint(
                condition=(
                    (
                        Q(
                            activity_type__in=[
                                "lesson",
                                "practice",
                            ],
                            lesson__isnull=False,
                            assessment_session__isnull=True,
                        )
                    )
                    |
                    (
                        Q(
                            activity_type="test",
                            lesson__isnull=True,
                            assessment_session__isnull=False,
                        )
                    )
                ),
                name="ck_study_session_target",
            ),

            models.CheckConstraint(
                condition=(
                    (
                        Q(
                            status__in=[
                                "active",
                                "paused",
                            ],
                            ended_at__isnull=True,
                        )
                    )
                    |
                    (
                        Q(
                            status__in=[
                                "completed",
                                "abandoned",
                            ],
                            ended_at__isnull=False,
                        )
                    )
                ),
                name="ck_study_session_end_state",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "user",
                    "-started_at",
                ],
                name="idx_study_session_user_recent",
            ),
        ]

    def clean(self):
        super().clean()

        errors = {}

        if self.activity_type in {
            self.ActivityType.LESSON,
            self.ActivityType.PRACTICE,
        }:
            if self.lesson_id is None:
                errors["lesson"] = (
                    "Lesson and practice study sessions "
                    "require a lesson."
                )

            if self.assessment_session_id is not None:
                errors["assessment_session"] = (
                    "Lesson and practice study sessions "
                    "must not reference an assessment."
                )

        elif self.activity_type == self.ActivityType.TEST:
            if self.lesson_id is not None:
                errors["lesson"] = (
                    "Test study sessions must not "
                    "reference a lesson."
                )

            if self.assessment_session_id is None:
                errors["assessment_session"] = (
                    "Test study sessions require an "
                    "assessment session."
                )

        if self.status in {
            self.Status.ACTIVE,
            self.Status.PAUSED,
        }:
            if self.ended_at is not None:
                errors["ended_at"] = (
                    "Active or paused study sessions "
                    "must not have ended_at."
                )

        elif self.status in {
            self.Status.COMPLETED,
            self.Status.ABANDONED,
        }:
            if self.ended_at is None:
                errors["ended_at"] = (
                    "Finished study sessions require "
                    "ended_at."
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"StudySession {self.study_session_id} "
            f"(user={self.user_id}, "
            f"type={self.activity_type}, "
            f"status={self.status})"
        )


class StudyEvent(models.Model):
    """
    Low-volume milestone event.

    This is deliberately not a generic clickstream.
    """

    class EventType(models.TextChoices):
        LESSON_OPENED = (
            "lesson_opened",
            "Lesson opened",
        )
        PRACTICE_STARTED = (
            "practice_started",
            "Practice started",
        )
        PRACTICE_COMPLETED = (
            "practice_completed",
            "Practice completed",
        )
        LESSON_COMPLETED = (
            "lesson_completed",
            "Lesson completed",
        )
        TEST_STARTED = (
            "test_started",
            "Test started",
        )
        TEST_COMPLETED = (
            "test_completed",
            "Test completed",
        )

    study_event_id = models.BigAutoField(
        primary_key=True,
    )

    session = models.ForeignKey(
        StudySession,
        on_delete=models.CASCADE,
        related_name="events",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="study_events",
    )

    event_type = models.CharField(
        max_length=32,
        choices=EventType.choices,
    )

    lesson = models.ForeignKey(
        "curriculum.Lesson",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="study_events",
    )

    assessment_session = models.ForeignKey(
        "learning_engine.PracticeSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="study_events",
    )

    occurred_at = models.DateTimeField(
        default=timezone.now,
    )

    class Meta:
        db_table = "study_event"

        ordering = [
            "occurred_at",
            "study_event_id",
        ]

        constraints = [
            models.CheckConstraint(
                condition=Q(
                    event_type__in=[
                        "lesson_opened",
                        "practice_started",
                        "practice_completed",
                        "lesson_completed",
                        "test_started",
                        "test_completed",
                    ]
                ),
                name="ck_study_event_type",
            ),

            models.CheckConstraint(
                condition=(
                    (
                        Q(
                            event_type__in=[
                                "lesson_opened",
                                "practice_started",
                                "practice_completed",
                                "lesson_completed",
                            ],
                            lesson__isnull=False,
                            assessment_session__isnull=True,
                        )
                    )
                    |
                    (
                        Q(
                            event_type__in=[
                                "test_started",
                                "test_completed",
                            ],
                            lesson__isnull=True,
                            assessment_session__isnull=False,
                        )
                    )
                ),
                name="ck_study_event_target",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "user",
                    "event_type",
                    "-occurred_at",
                ],
                name="idx_study_event_user_type",
            ),

            models.Index(
                fields=[
                    "lesson",
                    "-occurred_at",
                ],
                name="idx_study_event_lesson_recent",
            ),
        ]

    def clean(self):
        super().clean()

        errors = {}

        if (
            self.session_id is not None
            and self.user_id is not None
            and self.session.user_id
            != self.user_id
        ):
            errors["user"] = (
                "StudyEvent user must match "
                "StudySession user."
            )

        if self.event_type in {
            self.EventType.LESSON_OPENED,
            self.EventType.PRACTICE_STARTED,
            self.EventType.PRACTICE_COMPLETED,
            self.EventType.LESSON_COMPLETED,
        }:
            if self.lesson_id is None:
                errors["lesson"] = (
                    "Lesson/practice events require "
                    "a lesson."
                )

            if self.assessment_session_id is not None:
                errors["assessment_session"] = (
                    "Lesson/practice events must not "
                    "reference an assessment."
                )

        elif self.event_type in {
            self.EventType.TEST_STARTED,
            self.EventType.TEST_COMPLETED,
        }:
            if self.lesson_id is not None:
                errors["lesson"] = (
                    "Test events must not reference "
                    "a lesson."
                )

            if self.assessment_session_id is None:
                errors["assessment_session"] = (
                    "Test events require an "
                    "assessment session."
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"StudyEvent {self.study_event_id} "
            f"({self.event_type}, "
            f"user={self.user_id})"
        )