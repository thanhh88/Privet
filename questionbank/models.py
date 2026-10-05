from django.db import models

from curriculum.models import Skill


class Question(models.Model):
    class QuestionType(models.TextChoices):
        MULTIPLE_CHOICE = "multiple_choice", "Multiple choice"
        FILL_BLANK = "fill_blank", "Fill blank"
        FILL_CHOICE = "fill_choice", "Fill choice"
        CONTEXTUAL = "contextual", "Contextual"

    class CefrLevel(models.TextChoices):
        A1 = "A1", "A1"
        A2 = "A2", "A2"
        B1 = "B1", "B1"

    question_id = models.BigAutoField(
        primary_key=True,
    )

    source_id = models.CharField(
        max_length=128,
        unique=True,
    )

    question_type = models.CharField(
        max_length=32,
        choices=QuestionType.choices,
    )

    question_text = models.TextField()

    cefr_level = models.CharField(
        max_length=2,
        choices=CefrLevel.choices,
    )

    is_extension = models.BooleanField(
        default=False,
    )

    difficulty_score = models.DecimalField(
        max_digits=5,
        decimal_places=4,
    )

    correct_answer = models.TextField(
        null=True,
        blank=True,
    )

    accepted_answers = models.JSONField(
        null=True,
        blank=True,
    )

    explanation = models.TextField(
        null=True,
        blank=True,
    )

    audio_url = models.TextField(
        null=True,
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    class Meta:
        db_table = "question"

        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(difficulty_score__gte=0)
                    & models.Q(difficulty_score__lte=1)
                ),
                name="ck_question_difficulty",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    cefr_level__in=("A1", "A2", "B1")
                ),
                name="ck_question_cefr",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    question_type__in=(
                        "multiple_choice",
                        "fill_blank",
                        "fill_choice",
                        "contextual",
                    )
                ),
                name="ck_question_type",
            ),
        ]

    def __str__(self):
        return f"{self.source_id}: {self.question_text[:60]}"

class QuestionSkill(models.Model):
    question_skill_id = models.BigAutoField(
        primary_key=True,
    )

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="question_skills",
    )

    skill = models.ForeignKey(
        Skill,
        on_delete=models.PROTECT,
        related_name="question_skills",
    )

    is_primary = models.BooleanField(
        default=False,
    )

    weight = models.DecimalField(
        max_digits=6,
        decimal_places=5,
    )

    class Meta:
        db_table = "question_skill"

        constraints = [
            models.UniqueConstraint(
                fields=("question", "skill"),
                name="uq_question_skill_pair",
            ),

            models.CheckConstraint(
                condition=(
                    models.Q(weight__gt=0)
                    & models.Q(weight__lte=1)
                ),
                name="ck_qs_weight",
            ),

            models.UniqueConstraint(
                fields=("question",),
                condition=models.Q(is_primary=True),
                name="uq_qs_primary",
            ),
        ]

        indexes = [
            models.Index(
                fields=("skill", "question"),
                name="idx_qs_skill_question",
            ),
        ]

    def __str__(self):
        return (
            f"{self.question.source_id} -> "
            f"{self.skill.skill_name}"
        )
class QuestionOption(models.Model):
    option_id = models.BigAutoField(
        primary_key=True,
    )

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="options",
    )

    option_text = models.TextField()

    is_correct = models.BooleanField(
        default=False,
    )

    display_order = models.IntegerField(
        default=0,
    )

    class Meta:
        db_table = "question_option"
        ordering = ("question", "display_order", "option_id")

        constraints = [
            models.UniqueConstraint(
                fields=("option_id", "question"),
                name="uq_option_question_pair",
            ),
        ]

        indexes = [
            models.Index(
                fields=("question", "display_order"),
                name="idx_qopt_question_order",
            ),
        ]

    def __str__(self):
        return self.option_text