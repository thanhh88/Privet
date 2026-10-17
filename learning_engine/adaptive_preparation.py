from django.utils import timezone

from learning_engine.adaptive_assembly import (
    AssemblySkillCandidate,
)
from learning_engine.models import AssessmentSession
from learning_engine.priority import (
    rank_user_skill_priorities,
)
from learning_engine.question_selection import (
    build_question_candidates,
    rank_question_candidates,
)


def prepare_adaptive_assessment_candidates(
    *,
    user,
    session_id,
    eligible_skill_ids,
    as_of=None,
):
    """
    Prepare Skill -> ranked Question candidates
    for one adaptive assessment.

    Responsibilities
    ----------------
    1. Validate the current adaptive session.
    2. Rank caller-supplied eligible Skills once.
    3. Reuse each Skill's mastery_estimate as
       target difficulty.
    4. Build DB-eligible Question candidates.
    5. Rank Questions using canonical 9D.9 policy.
    6. Return pure AssemblySkillCandidate objects.

    Skills with zero available Questions are skipped.

    This function does not write to the database.
    """

    if user.pk is None:
        raise ValueError(
            "User must be saved before adaptive "
            "assessment preparation."
        )

    if as_of is None:
        as_of = timezone.now()

    eligible_skill_ids = list(
        eligible_skill_ids
    )

    if len(
        eligible_skill_ids
    ) != len(
        set(eligible_skill_ids)
    ):
        raise ValueError(
            "eligible_skill_ids must not contain "
            "duplicates."
        )

    try:
        session = AssessmentSession.objects.get(
            pk=session_id,
            user=user,
        )
    except AssessmentSession.DoesNotExist as exc:
        raise ValueError(
            "Assessment session does not exist "
            "or does not belong to this user."
        ) from exc

    if session.status != AssessmentSession.Status.ACTIVE:
        raise ValueError(
            "Adaptive assessment preparation "
            "requires an active session."
        )

    if session.mode != AssessmentSession.Mode.ADAPTIVE:
        raise ValueError(
            "Adaptive assessment preparation "
            "requires adaptive mode."
        )

    ranked_skills = rank_user_skill_priorities(
        user=user,
        skill_ids=eligible_skill_ids,
        as_of=as_of,
    )

    prepared = []

    for skill_priority in ranked_skills:
        question_candidates = (
            build_question_candidates(
                user=user,
                session_id=session.pk,
                skill_id=skill_priority.skill_id,
            )
        )

        # A Skill may be curriculum-eligible but
        # currently have no usable Question.
        #
        # Such a Skill is not viable for assembly
        # and is therefore skipped rather than
        # causing the whole preparation to fail.
        if not question_candidates:
            continue

        ranked_questions = (
            rank_question_candidates(
                candidates=question_candidates,
                target_difficulty=(
                    skill_priority.mastery_estimate
                ),
            )
        )

        prepared.append(
            AssemblySkillCandidate(
                skill_id=skill_priority.skill_id,
                skill_code=skill_priority.skill_code,
                priority_score=(
                    skill_priority.priority_score
                ),
                ranked_question_ids=tuple(
                    row.question_id
                    for row in ranked_questions
                ),
            )
        )

    return tuple(
        prepared
    )