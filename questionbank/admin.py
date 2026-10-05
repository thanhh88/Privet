from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from .models import Question, QuestionOption, QuestionSkill


class QuestionSkillInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()

        if any(self.errors):
            return

        primary_count = 0
        total_weight = Decimal("0")
        active_rows = 0

        for form in self.forms:
            cleaned_data = getattr(form, "cleaned_data", None)

            if not cleaned_data:
                continue

            if cleaned_data.get("DELETE"):
                continue

            skill = cleaned_data.get("skill")
            weight = cleaned_data.get("weight")
            is_primary = cleaned_data.get("is_primary", False)

            # Bỏ qua extra form hoàn toàn trống.
            if skill is None and weight is None:
                continue

            active_rows += 1

            if is_primary:
                primary_count += 1

            if weight is not None:
                total_weight += weight

        if active_rows == 0:
            raise ValidationError(
                "A question must have at least one skill."
            )

        if primary_count != 1:
            raise ValidationError(
                "A question must have exactly one primary skill."
            )

        tolerance = Decimal("0.001")

        if abs(total_weight - Decimal("1")) > tolerance:
            raise ValidationError(
                "Question skill weights must sum to 1 "
                "within a tolerance of ±0.001."
            )


class QuestionSkillInline(admin.TabularInline):
    model = QuestionSkill
    formset = QuestionSkillInlineFormSet

    extra = 1

    fields = (
        "skill",
        "is_primary",
        "weight",
    )

    autocomplete_fields = (
        "skill",
    )

class QuestionOptionInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()

        if any(self.errors):
            return

        active_rows = 0
        correct_count = 0

        for form in self.forms:
            cleaned_data = getattr(form, "cleaned_data", None)

            if not cleaned_data:
                continue

            if cleaned_data.get("DELETE"):
                continue

            option_text = cleaned_data.get("option_text")
            is_correct = cleaned_data.get("is_correct", False)

            # Bỏ qua extra form trống.
            if not option_text:
                continue

            active_rows += 1

            if is_correct:
                correct_count += 1

        question_type = self.instance.question_type

        if question_type == Question.QuestionType.MULTIPLE_CHOICE:
            if active_rows < 2:
                raise ValidationError(
                    "A multiple-choice question must have "
                    "at least two options."
                )

            if correct_count != 1:
                raise ValidationError(
                    "A multiple-choice question must have "
                    "exactly one correct option."
                )

        if question_type == Question.QuestionType.FILL_BLANK:
            if active_rows != 0:
                raise ValidationError(
                    "A fill-blank question must not have options."
                )
class QuestionOptionInline(admin.TabularInline):
    model = QuestionOption
    formset = QuestionOptionInlineFormSet

    extra = 1

    fields = (
        "option_text",
        "is_correct",
        "display_order",
    )

    ordering = (
        "display_order",
        "option_id",
    )


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = (
        "source_id",
        "question_type",
        "cefr_level",
        "difficulty_score",
        "is_extension",
        "is_active",
    )

    list_filter = (
        "question_type",
        "cefr_level",
        "is_extension",
        "is_active",
    )

    search_fields = (
        "source_id",
        "question_text",
    )

    ordering = (
        "source_id",
    )

    readonly_fields = (
        "question_id",
    )

    inlines = (
        QuestionSkillInline,
        QuestionOptionInline,
    )

    fieldsets = (
        (
            "Identity",
            {
                "fields": (
                    "question_id",
                    "source_id",
                ),
            },
        ),
        (
            "Question",
            {
                "fields": (
                    "question_type",
                    "question_text",
                    "cefr_level",
                    "is_extension",
                    "difficulty_score",
                ),
            },
        ),
        (
            "Answer",
            {
                "fields": (
                    "correct_answer",
                    "accepted_answers",
                    "explanation",
                ),
            },
        ),
        (
            "Media and status",
            {
                "fields": (
                    "audio_url",
                    "is_active",
                ),
            },
        ),
    )


@admin.register(QuestionOption)
class QuestionOptionAdmin(admin.ModelAdmin):
    list_display = (
        "option_id",
        "question",
        "option_text",
        "is_correct",
        "display_order",
    )

    list_filter = (
        "is_correct",
    )

    search_fields = (
        "question__source_id",
        "question__question_text",
        "option_text",
    )

    readonly_fields = (
        "option_id",
        "question",
        "option_text",
        "is_correct",
        "display_order",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    ordering = (
        "question",
        "display_order",
        "option_id",
    )

    list_select_related = (
        "question",
    )


@admin.register(QuestionSkill)
class QuestionSkillAdmin(admin.ModelAdmin):
    list_display = (
        "question_skill_id",
        "question",
        "skill",
        "is_primary",
        "weight",
    )

    list_filter = (
        "is_primary",
        "skill__cefr_level",
    )

    search_fields = (
        "question__source_id",
        "question__question_text",
        "skill__skill_name",
    )

    readonly_fields = (
        "question_skill_id",
        "question",
        "skill",
        "is_primary",
        "weight",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    list_select_related = (
        "question",
        "skill",
    )