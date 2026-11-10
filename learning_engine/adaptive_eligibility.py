from curriculum.models import (
    LessonProgress,
    LessonSkill,
    Skill,
)


CORE_ADAPTIVE_CEFR_LEVELS = (
    "A1",
    "A2",
)


def _resolve_completed_lesson_skill_ids(
    *,
    user,
):
    """
    Return Skills belonging to active Lessons that
    this learner has actually completed.

    Lesson practice itself is not assessment
    evidence. Completion is used here only as an
    eligibility/progression signal.
    """

    completed_lesson_ids = (
        LessonProgress.objects
        .filter(
            user=user,
            status=(
                LessonProgress.Status.COMPLETED
            ),
            lesson__is_active=True,
        )
        .values_list(
            "lesson_id",
            flat=True,
        )
    )

    return tuple(
        LessonSkill.objects
        .filter(
            lesson_id__in=(
                completed_lesson_ids
            ),
        )
        .values_list(
            "skill_id",
            flat=True,
        )
        .distinct()
    )


def resolve_adaptive_eligible_skill_ids(
    *,
    user,
):
    """
    Resolve Skills eligible for adaptive assessment.

    Baseline core scope:
    - active Topic;
    - active Skill;
    - A1/A2;
    - non-extension.

    Learner policy:
    ----------------
    Learners who explicitly entered through the true
    beginner path (prior_russian_experience=False)
    should not be assessed on core Skills they have
    never been taught.

    Therefore, when completed Lesson history exists,
    their adaptive scope is restricted to Skills
    mapped to completed Lessons.

    Learners with prior experience retain the wider
    A1/A2 core scope.

    Legacy safeguard:
    -----------------
    A prior_russian_experience=False learner who
    already has assessment history but has no Lesson
    completion rows is not forced to an empty scope.
    In that case the baseline A1/A2 scope remains.
    """

    if user.pk is None:
        raise ValueError(
            "User must be saved before adaptive "
            "eligibility can be resolved."
        )

    eligible = (
        Skill.objects
        .filter(
            topic__is_active=True,
            is_active=True,
            is_extension=False,
            cefr_level__in=(
                CORE_ADAPTIVE_CEFR_LEVELS
            ),
        )
    )

    # =========================================
    # True-beginner learned-skill gate
    # =========================================

    if (
        user.prior_russian_experience
        is False
    ):
        completed_skill_ids = (
            _resolve_completed_lesson_skill_ids(
                user=user,
            )
        )

        if completed_skill_ids:
            eligible = eligible.filter(
                pk__in=completed_skill_ids,
            )

    return tuple(
        eligible
        .order_by(
            "cefr_level",
            "topic__display_order",
            "display_order",
            "skill_id",
        )
        .values_list(
            "skill_id",
            flat=True,
        )
    )