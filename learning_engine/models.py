from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import (
    F,
    Q,
)
from decimal import Decimal

MASTERY_RULE_V1 = settings.MASTERY_RULE_V1

MASTERY_MIN_OBSERVATIONS = int(
    MASTERY_RULE_V1["min_observations"]
)

MASTERY_MASTERED_MIN_OBSERVATIONS = int(
    MASTERY_RULE_V1[
        "mastered_min_observations"
    ]
)

MASTERY_MASTERED_THRESHOLD = Decimal(
    str(
        MASTERY_RULE_V1[
            "mastered_threshold"
        ]
    )
)
MASTERY_ALGORITHM_VERSION = str(
    MASTERY_RULE_V1["algorithm_version"]
)


class AssessmentSession(models.Model):
    class Mode(models.TextChoices):
        ADAPTIVE = "adaptive", "Adaptive"
        DIAGNOSTIC = "diagnostic", "Diagnostic"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"

    session_id = models.BigAutoField(
        primary_key=True,
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assessment_sessions",
    )

    mode = models.CharField(
        max_length=16,
        choices=Mode.choices,
        default=Mode.ADAPTIVE,
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    started_at = models.DateTimeField(
        auto_now_add=True,
    )

    ended_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = [
            "-started_at",
            "-session_id",
        ]

        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        status="active",
                        ended_at__isnull=True,
                    )
                    |
                    Q(
                        status__in=[
                            "completed",
                            "abandoned",
                        ],
                        ended_at__isnull=False,
                    )
                ),
                # Giữ tên cũ tạm thời để migration
                # nhỏ hơn. R6 sẽ squash/clean tên.
                name=(
                    "ck_practice_session_"
                    "status_ended_at"
                ),
            ),

            models.CheckConstraint(
                condition=Q(
                    mode__in=[
                        "adaptive",
                        "diagnostic",
                    ]
                ),
                name="ck_assessment_session_mode",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "user",
                    "started_at",
                ],
                name="idx_session_user_started",
            ),
        ]

    def clean(self):
        super().clean()

        errors = {}

        if self.mode not in {
            self.Mode.ADAPTIVE,
            self.Mode.DIAGNOSTIC,
        }:
            errors["mode"] = (
                "Assessment session mode must be "
                "adaptive or diagnostic."
            )

        if self.status == self.Status.ACTIVE:
            if self.ended_at is not None:
                errors["ended_at"] = (
                    "Active assessment sessions "
                    "must not have ended_at."
                )

        elif self.status in {
            self.Status.COMPLETED,
            self.Status.ABANDONED,
        }:
            if self.ended_at is None:
                errors["ended_at"] = (
                    "Finished assessment sessions "
                    "require ended_at."
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"AssessmentSession {self.session_id} "
            f"({self.user_id}, "
            f"{self.mode}, {self.status})"
        )

class SessionQuestion(models.Model):
    session_question_id = models.BigAutoField(
        primary_key=True,
    )

    session = models.ForeignKey(
        AssessmentSession,
        on_delete=models.CASCADE,
        related_name="session_questions",
    )

    question = models.ForeignKey(
        "questionbank.Question",
        on_delete=models.PROTECT,
        related_name="session_questions",
    )

    # Historical assessment metadata frozen at the
    # moment this Question is selected into the session.
    #
    # Do not recalculate historical evidence from the
    # current Question Bank after this point.
    difficulty_score_snapshot = models.DecimalField(
        max_digits=5,
        decimal_places=4,
    )

    # Historical Question -> Skill mappings frozen at
    # selection time.
    #
    # Expected structure:
    # [
    #     {
    #         "skill_id": 123,
    #         "skill_code": "ACC-03",
    #         "is_primary": True,
    #         "weight": "0.70000",
    #     },
    #     ...
    # ]
    skill_snapshot = models.JSONField()

    sequence_number = models.PositiveIntegerField()

    selected_at = models.DateTimeField(
        auto_now_add=True,
    )

    first_viewed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # Total active time spent on this assessment
    # question across all visits within the session.
    #
    # The frontend/service layer adds active intervals
    # as the learner leaves, pauses, or revisits the
    # question. This is deliberately stored on
    # SessionQuestion rather than inferred from the
    # final QuestionAttempt.
    active_response_time_ms = models.PositiveIntegerField(
        default=0,
    )

    class Meta:
        ordering = [
            "session_id",
            "sequence_number",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "session",
                    "sequence_number",
                ],
                name="uq_sq_session_sequence",
            ),
            models.UniqueConstraint(
                fields=[
                    "session",
                    "question",
                ],
                name="uq_sq_session_question",
            ),
            models.CheckConstraint(
                condition=Q(
                    sequence_number__gte=1
                ),
                name="ck_sq_sequence_positive",
            ),
            models.CheckConstraint(
                condition=Q(
                    difficulty_score_snapshot__gte=0,
                    difficulty_score_snapshot__lte=1,
                ),
                name="ck_sq_difficulty_snapshot_range",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "question",
                    "selected_at",
                ],
                name="idx_sq_question_selected",
            ),
        ]

    def __str__(self):
        return (
            f"SessionQuestion "
            f"{self.session_question_id} "
            f"(session={self.session_id}, "
            f"question={self.question_id}, "
            f"sequence={self.sequence_number})"
        )


class QuestionAttempt(models.Model):
    attempt_id = models.BigAutoField(
        primary_key=True,
    )

    session_question = models.ForeignKey(
        SessionQuestion,
        on_delete=models.CASCADE,
        related_name="attempts",
    )

    attempt_number = models.PositiveIntegerField()

    # Option-based questions.
    # Store stable option_key rather than an Option FK.
    selected_option_key = models.CharField(
        max_length=16,
        null=True,
        blank=True,
    )

    # Future fill_blank support.
    response_text = models.TextField(
        null=True,
        blank=True,
    )

    # Snapshot of grading at submission time.
    # Never recalculate historical attempts from
    # the current Question Bank.
    is_correct = models.BooleanField()

    response_time_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    answered_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = [
            "session_question_id",
            "attempt_number",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "session_question",
                    "attempt_number",
                ],
                name="uq_attempt_sq_number",
            ),

            models.CheckConstraint(
                condition=Q(
                    attempt_number__gte=1
                ),
                name="ck_attempt_number_positive",
            ),

            # A real submit must contain exactly
            # one response representation.
            models.CheckConstraint(
                condition=(
                    (
                        Q(
                            selected_option_key__isnull=False
                        )
                        &
                        Q(
                            response_text__isnull=True
                        )
                    )
                    |
                    (
                        Q(
                            selected_option_key__isnull=True
                        )
                        &
                        Q(
                            response_text__isnull=False
                        )
                    )
                ),
                name=(
                    "ck_attempt_exactly_one_response"
                ),
            ),

            models.CheckConstraint(
                condition=(
                    Q(
                        selected_option_key__isnull=True
                    )
                    |
                    ~Q(
                        selected_option_key=""
                    )
                ),
                name=(
                    "ck_attempt_option_key_nonblank"
                ),
            ),

            models.CheckConstraint(
                condition=(
                    Q(
                        response_text__isnull=True
                    )
                    |
                    ~Q(
                        response_text=""
                    )
                ),
                name=(
                    "ck_attempt_response_nonblank"
                ),
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "session_question",
                    "answered_at",
                ],
                name="idx_attempt_sq_answered",
            ),

            models.Index(
                fields=[
                    "answered_at",
                ],
                name="idx_attempt_answered",
            ),
        ]

    def clean(self):
        super().clean()

        errors = {}

        if self.selected_option_key is not None:
            self.selected_option_key = (
                self.selected_option_key.strip()
            )

            if not self.selected_option_key:
                errors[
                    "selected_option_key"
                ] = (
                    "Selected option key "
                    "must not be blank."
                )

        if self.response_text is not None:
            self.response_text = (
                self.response_text.strip()
            )

            if not self.response_text:
                errors[
                    "response_text"
                ] = (
                    "Response text "
                    "must not be blank."
                )

        if self.session_question_id:
            question = (
                self.session_question.question
            )

            question_type = (
                question.question_type
            )

            if question_type in {
                "multiple_choice",
                "fill_choice",
            }:
                if self.selected_option_key is None:
                    errors[
                        "selected_option_key"
                    ] = (
                        "Option-based questions "
                        "require selected_option_key."
                    )

                if self.response_text is not None:
                    errors[
                        "response_text"
                    ] = (
                        "Option-based questions "
                        "must not use response_text."
                    )

                if (
                    self.selected_option_key
                    and not (
                        question.options.filter(
                            option_key=(
                                self.selected_option_key
                            )
                        ).exists()
                    )
                ):
                    errors[
                        "selected_option_key"
                    ] = (
                        "Selected option_key does "
                        "not belong to this question."
                    )

            elif question_type == "fill_blank":
                if self.response_text is None:
                    errors[
                        "response_text"
                    ] = (
                        "fill_blank questions "
                        "require response_text."
                    )

                if (
                    self.selected_option_key
                    is not None
                ):
                    errors[
                        "selected_option_key"
                    ] = (
                        "fill_blank questions must "
                        "not use selected_option_key."
                    )

            else:
                errors[
                    "session_question"
                ] = (
                    "Unsupported question_type "
                    f"{question_type!r}."
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"Attempt {self.attempt_id} "
            f"(session_question="
            f"{self.session_question_id}, "
            f"attempt={self.attempt_number})"
        )

class SkillMastery(models.Model):
    """
    Derived rule-based mastery state for one User × Skill.

    Source of truth remains assessment history:
        AssessmentSession
        -> SessionQuestion
        -> QuestionAttempt

    This row is a recomputable snapshot.
    """

    mastery_id = models.BigAutoField(
        primary_key=True,
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="skill_masteries",
    )

    skill = models.ForeignKey(
        "curriculum.Skill",
        on_delete=models.PROTECT,
        related_name="user_masteries",
    )

    # NULL means:
    # not enough valid observations yet.
    mastery_score = models.DecimalField(
        max_digits=6,
        decimal_places=5,
        null=True,
        blank=True,
    )

    observation_count = models.PositiveIntegerField(
        default=0,
    )

    # Raw count of correct valid assessment
    # observations for this User x Skill.
    correct_attempts = models.PositiveIntegerField(
        default=0,
    )

    # Weighted accuracy across the 10 most recent
    # valid observations.
    recent_accuracy = models.DecimalField(
        max_digits=6,
        decimal_places=5,
        null=True,
        blank=True,
    )

    # Weighted accuracy across all valid
    # observations.
    historical_accuracy = models.DecimalField(
        max_digits=6,
        decimal_places=5,
        null=True,
        blank=True,
    )

    # Mean active response time for valid viewed
    # observations.
    #
    # This remains nullable because response-time
    # aggregation will be implemented separately.
    avg_response_time_ms = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )

    # Number of trailing qualifying incorrect
    # observations.
    consecutive_errors = models.PositiveIntegerField(
        default=0,
    )

    # Most recent lesson / guided-practice activity
    # related to this Skill.
    #
    # Assessment completion does not update this field.
    last_learning_activity_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # Timestamp of the newest evidence included
    # in this mastery snapshot.
    last_evidence_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # When the snapshot was last calculated.
    calculated_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # Important for thesis reproducibility if the
    # rule changes later.
    algorithm_version = models.CharField(
        max_length=32,
        default=MASTERY_ALGORITHM_VERSION,
    )

    class Meta:
        ordering = [
            "user_id",
            "skill_id",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "user",
                    "skill",
                ],
                name="uq_mastery_user_skill",
            ),

            models.CheckConstraint(
                condition=(
                    Q(
                        mastery_score__isnull=True
                    )
                    |
                    Q(
                        mastery_score__gte=0,
                        mastery_score__lte=1,
                    )
                ),
                name="ck_mastery_score_range",
            ),

            # Schema-level enforcement of the configured
            # minimum evidence threshold.
            #
            # If MASTERY_RULE_V1["min_observations"] changes,
            # create and apply a migration so the database
            # constraint changes with it.
            models.CheckConstraint(
                condition=(
                        Q(
                            observation_count__lt=(
                                MASTERY_MIN_OBSERVATIONS
                            ),
                            mastery_score__isnull=True,
                        )
                        |
                        Q(
                            observation_count__gte=(
                                MASTERY_MIN_OBSERVATIONS
                            ),
                            mastery_score__isnull=False,
                        )
                ),
                name="ck_mastery_min_observations",
            ),
            models.CheckConstraint(
                condition=(
                        Q(
                            recent_accuracy__isnull=True
                        )
                        |
                        Q(
                            recent_accuracy__gte=0,
                            recent_accuracy__lte=1,
                        )
                ),
                name="ck_mastery_recent_acc_range",
            ),

            models.CheckConstraint(
                condition=(
                        Q(
                            historical_accuracy__isnull=True
                        )
                        |
                        Q(
                            historical_accuracy__gte=0,
                            historical_accuracy__lte=1,
                        )
                ),
                name="ck_mastery_hist_acc_range",
            ),

            models.CheckConstraint(
                condition=(
                        Q(
                            avg_response_time_ms__isnull=True
                        )
                        |
                        Q(
                            avg_response_time_ms__gte=0
                        )
                ),
                name="ck_mastery_avg_rt_nonnegative",
            ),

            models.CheckConstraint(
                condition=Q(
                    correct_attempts__lte=F(
                        "observation_count"
                    )
                ),
                name="ck_mastery_correct_lte_obs",
            ),

            models.CheckConstraint(
                condition=Q(
                    consecutive_errors__lte=F(
                        "observation_count"
                    )
                ),
                name="ck_mastery_errors_lte_obs",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "user",
                    "mastery_score",
                ],
                name="idx_mastery_user_score",
            ),
        ]

    @property
    def has_sufficient_data(self):
        return (
                self.observation_count
                >= MASTERY_MIN_OBSERVATIONS
                and self.mastery_score is not None
        )

    @property
    def is_mastered(self):
        return (
                self.observation_count
                >= MASTERY_MASTERED_MIN_OBSERVATIONS
                and self.mastery_score is not None
                and self.mastery_score
                >= MASTERY_MASTERED_THRESHOLD
        )

    def __str__(self):
        score = (
            self.mastery_score
            if self.mastery_score is not None
            else "insufficient-data"
        )

        return (
            f"Mastery "
            f"(user={self.user_id}, "
            f"skill={self.skill_id}, "
            f"score={score})"
        )
