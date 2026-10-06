from decimal import Decimal

from django.core.exceptions import ValidationError


WEIGHT_SUM_TOLERANCE = Decimal("0.001")


def validate_skill_rows(rows):
    """
    Validate QuestionSkill data.

    Each row is expected to contain:
    - skill
    - is_primary
    - weight
    """
    if not rows:
        raise ValidationError(
            "A question must have at least one skill."
        )

    # Reject duplicate skills before reaching the database constraint.
    seen_skills = set()

    for row in rows:
        skill = row.get("skill")

        # Some pure unit tests may omit the skill field.
        if skill is None:
            continue

        skill_key = getattr(skill, "pk", skill)

        if skill_key in seen_skills:
            raise ValidationError(
                "A question must not contain duplicate skills."
            )

        seen_skills.add(skill_key)

    primary_count = sum(
        1
        for row in rows
        if row.get("is_primary")
    )

    if primary_count != 1:
        raise ValidationError(
            "A question must have exactly one primary skill."
        )

    total_weight = sum(
        (
            row.get("weight") or Decimal("0")
            for row in rows
        ),
        Decimal("0"),
    )

    if abs(total_weight - Decimal("1")) > WEIGHT_SUM_TOLERANCE:
        raise ValidationError(
            "Question skill weights must sum to 1 "
            f"within a tolerance of ±{WEIGHT_SUM_TOLERANCE}. "
            f"Current sum: {total_weight}."
        )


def validate_options(question_type, options):
    """
    Validate QuestionOption data.

    Each option is expected to contain:
    - option_text
    - is_correct
    """

    # If an option reaches this shared validator,
    # it must contain non-empty text.
    #
    # Django Admin should filter out completely empty extra forms
    # before calling this validator.
    for option in options:
        option_text = option.get("option_text")

        if not isinstance(option_text, str) or not option_text.strip():
            raise ValidationError(
                "Option text must be a non-empty string."
            )

    correct_count = sum(
        1
        for option in options
        if option.get("is_correct")
    )

    if question_type in {
        "multiple_choice",
        "fill_choice",
    }:
        if len(options) < 2:
            raise ValidationError(
                "This question type must have at least two options."
            )

        if correct_count != 1:
            raise ValidationError(
                "This question type must have exactly one correct option."
            )

    elif question_type == "fill_blank":
        if options:
            raise ValidationError(
                "A fill-blank question must not have options."
            )

    elif question_type == "contextual":
        raise ValidationError(
            "Contextual questions are not supported yet. "
            "Define grading rules before using this question type."
        )


def validate_question_answer_fields(
    question_type,
    correct_answer,
    accepted_answers,
):
    """
    Validate answer-related fields stored directly on Question.
    """

    if accepted_answers is not None:
        if not isinstance(accepted_answers, list):
            raise ValidationError(
                {
                    "accepted_answers":
                        "Accepted answers must be a JSON array."
                }
            )

        for answer in accepted_answers:
            if not isinstance(answer, str):
                raise ValidationError(
                    {
                        "accepted_answers":
                            "Every accepted answer must be a string."
                    }
                )

            if not answer.strip():
                raise ValidationError(
                    {
                        "accepted_answers":
                            "Accepted answers must not contain empty strings."
                    }
                )

    normalized_correct_answer = (
        correct_answer.strip()
        if isinstance(correct_answer, str)
        else ""
    )

    has_accepted_answers = bool(accepted_answers)

    if question_type == "fill_blank":
        if (
            not normalized_correct_answer
            and not has_accepted_answers
        ):
            raise ValidationError(
                "A fill-blank question must have either "
                "correct_answer or accepted_answers."
            )

    elif question_type in {
        "multiple_choice",
        "fill_choice",
    }:
        if normalized_correct_answer:
            raise ValidationError(
                {
                    "correct_answer":
                        "Option-based questions must not use correct_answer."
                }
            )

        if accepted_answers is not None:
            raise ValidationError(
                {
                    "accepted_answers":
                        "Option-based questions must not use "
                        "accepted_answers."
                }
            )

    elif question_type == "contextual":
        raise ValidationError(
            "Contextual questions are not supported yet. "
            "Define grading rules before using this question type."
        )