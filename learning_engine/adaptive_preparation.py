from decimal import Decimal

from django.utils import timezone

from learning_engine.adaptive_assembly import (
    AssemblySkillCandidate,
    TARGET_DISTINCT_SKILLS,
)
from learning_engine.models import (
    AssessmentSession,
    SessionQuestion,
)
from learning_engine.priority import (
    rank_user_skill_priorities,
)
from learning_engine.question_selection import (
    build_question_candidates,
    rank_question_candidates,
    rank_review_question_candidates,
)
from learning_engine.review_policy import (
    build_user_review_due_pool,
    determine_review_slot_count,
)

MAX_REMEDIATION_FOCUS_SKILLS = 2

REMEDIATION_MASTERY_THRESHOLD = Decimal(
    "0.50000"
)


def _is_remediation_or_review(
    skill_priority,
):
    """
    Return True when a Skill already has assessment
    evidence and deserves remediation/review focus.

    This intentionally does not alter the canonical
    priority formula.

    A Skill qualifies when:
    - it has real assessment evidence; and
    - it is currently weak, has consecutive errors,
      or has become review-due.
    """

    if skill_priority.observation_count <= 0:
        return False

    return (
        skill_priority.mastery_estimate
        < REMEDIATION_MASTERY_THRESHOLD
        or skill_priority.consecutive_errors > 0
        or skill_priority.review_due > Decimal("0")
    )


def _copy_with_selection_rank(
    *,
    candidate,
    selection_rank,
):
    return AssemblySkillCandidate(
        skill_id=candidate.skill_id,
        skill_code=candidate.skill_code,
        priority_score=candidate.priority_score,
        ranked_question_ids=(
            candidate.ranked_question_ids
        ),
        selection_rank=selection_rank,
        selection_reason=(
            candidate.selection_reason
        ),
        seen_before_question_ids=(
            candidate.seen_before_question_ids
        ),
    )

def _copy_with_ranked_question_ids(
    *,
    candidate,
    ranked_question_ids,
    selection_reason=None,
):
    if selection_reason is None:
        selection_reason = (
            candidate.selection_reason
        )

    return AssemblySkillCandidate(
        skill_id=candidate.skill_id,
        skill_code=candidate.skill_code,
        priority_score=candidate.priority_score,
        ranked_question_ids=tuple(
            ranked_question_ids
        ),
        selection_rank=(
            candidate.selection_rank
        ),
        selection_reason=selection_reason,
        seen_before_question_ids=(
            candidate.seen_before_question_ids
        ),
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

    Adaptive focus policy v2
    ------------------------
    1. Validate the active adaptive session.
    2. Rank eligible Skills using canonical
       learner priority.
    3. Remove Skills with no usable Questions.
    4. Build a five-Skill focus portfolio:
       - up to 2 remediation/review Skills;
       - remaining slots prefer exploration Skills
         with zero assessment observations;
       - if either category is insufficient,
         relax gracefully using remaining viable
         Skills.
    5. Preserve additional viable Skills as reserves.
       They may be used only if the preferred focus
       pool cannot supply enough unique Questions.
    6. Rank normal Skills using canonical 9D.9
       Question ranking. Guaranteed D2 review Skills
       are re-ranked using D2 review policy.
    7. Freeze C3 audit metadata on each prepared
       Skill candidate:
       - selection_reason = normal / review;
       - seen_before_question_ids.
    8. Attach deterministic selection_rank so the
       pure planner does not reintroduce alphabetic
       bias.

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

    if (
        session.status
        != AssessmentSession.Status.ACTIVE
    ):
        raise ValueError(
            "Adaptive assessment preparation "
            "requires an active session."
        )

    if (
        session.mode
        != AssessmentSession.Mode.ADAPTIVE
    ):
        raise ValueError(
            "Adaptive assessment preparation "
            "requires adaptive mode."
        )

    # -----------------------------------------
    # Canonical learner priority.
    #
    # Equal scores now follow curriculum order,
    # not skill_code alphabet.
    # -----------------------------------------

    ranked_skills = (
        rank_user_skill_priorities(
            user=user,
            skill_ids=eligible_skill_ids,
            as_of=as_of,
        )
    )

    # -----------------------------------------
    # First determine VIABLE Skills.
    #
    # Portfolio selection must happen AFTER this
    # point so an empty Question pool cannot consume
    # one of the five intended focus slots.
    # -----------------------------------------

    viable = []

    for skill_priority in ranked_skills:
        question_candidates = (
            build_question_candidates(
                user=user,
                session_id=session.pk,
                skill_id=skill_priority.skill_id,
            )
        )

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

        candidate = AssemblySkillCandidate(
            skill_id=skill_priority.skill_id,
            skill_code=skill_priority.skill_code,
            priority_score=(
                skill_priority.priority_score
            ),
            ranked_question_ids=tuple(
                row.question_id
                for row in ranked_questions
            ),
            selection_reason=(
                SessionQuestion
                .SelectionReason
                .NORMAL
            ),
            seen_before_question_ids=tuple(
                row.question_id
                for row in question_candidates
                if row.was_seen_before
            ),
        )

        viable.append(
            (
                skill_priority,
                candidate,
            )
        )

    if not viable:
        return ()

    # =========================================
    # D2-R + E1 adaptive focus portfolio
    # =========================================
    #
    # D2-R reserves deterministic review slots first.
    #
    # Existing E1 anti-starvation then fills the
    # remaining focus capacity.
    #
    # A reserved D2 review Skill counts toward the
    # existing E1 cap of two remediation/review
    # focus Skills, preserving exploration.

    viable_by_skill_id = {
        row[1].skill_id: row
        for row in viable
    }

    review_due_pool = (
        build_user_review_due_pool(
            user=user,
            eligible_skill_ids=list(
                viable_by_skill_id
            ),
            as_of=as_of,
        )
    )

    review_slot_count = (
        determine_review_slot_count(
            pool_size=len(
                review_due_pool
            ),
        )
    )

    review_due_skill_ids = {
        candidate.skill_id
        for candidate in review_due_pool
    }

    focus = []
    focus_skill_ids = set()

    # -----------------------------------------
    # Phase R:
    # guaranteed D2 review reservation.
    #
    # Review pool is already deterministic:
    # oldest review evidence
    # -> lower mastery
    # -> skill_code
    # -> skill_id.
    # -----------------------------------------

    for review_skill in (
            review_due_pool[
            :review_slot_count
            ]
    ):
        (
            skill_priority,
            candidate,
        ) = viable_by_skill_id[
            review_skill.skill_id
        ]

        # Rebuild the Question candidates only for the
        # guaranteed D2 review Skill.
        #
        # Normal adaptive Skills keep their existing
        # canonical ranking unchanged.
        review_question_candidates = (
            build_question_candidates(
                user=user,
                session_id=session.pk,
                skill_id=(
                    review_skill.skill_id
                ),
            )
        )

        ranked_review_questions = (
            rank_review_question_candidates(
                candidates=(
                    review_question_candidates
                ),
                target_difficulty=(
                    skill_priority.mastery_estimate
                ),
            )
        )

        review_candidate = (
            _copy_with_ranked_question_ids(
                candidate=candidate,
                ranked_question_ids=(
                    row.question_id
                    for row
                    in ranked_review_questions
                ),
                selection_reason=(
                    SessionQuestion
                    .SelectionReason
                    .REVIEW
                ),
            )
        )

        row = (
            skill_priority,
            review_candidate,
        )

        focus.append(
            row
        )

        focus_skill_ids.add(
            review_candidate.skill_id
        )

    # -----------------------------------------
    # D2 review-due Skills not selected into the
    # guaranteed review slots stay outside the
    # normal E1 focus competition.
    #
    # This prevents a backlog of review-due Skills
    # from silently consuming more than the P2-8
    # reservation.
    # -----------------------------------------

    normal_viable = [
        row
        for row in viable
        if row[1].skill_id
        not in review_due_skill_ids
    ]

    remediation = [
        row
        for row in normal_viable
        if _is_remediation_or_review(
            row[0]
        )
    ]

    exploration = [
        row
        for row in normal_viable
        if row[0].observation_count == 0
    ]

    # -----------------------------------------
    # Phase A:
    # preserve the existing E1 cap of at most two
    # remediation/review focus Skills total.
    #
    # D2 reserved review Skills already consume
    # part (or all) of this capacity.
    # -----------------------------------------

    remaining_remediation_capacity = max(
        0,
        (
            MAX_REMEDIATION_FOCUS_SKILLS
            - len(focus)
        ),
    )

    for row in remediation[
        :remaining_remediation_capacity
    ]:
        if (
            len(focus)
            >= TARGET_DISTINCT_SKILLS
        ):
            break

        focus.append(
            row
        )

        focus_skill_ids.add(
            row[1].skill_id
        )

    # -----------------------------------------
    # Phase B:
    # fill remaining focus slots with unseen
    # exploration Skills.
    #
    # exploration already follows canonical
    # priority / curriculum ordering.
    # -----------------------------------------

    for row in exploration:
        if (
            len(focus)
            >= TARGET_DISTINCT_SKILLS
        ):
            break

        if (
            row[1].skill_id
            in focus_skill_ids
        ):
            continue

        focus.append(
            row
        )

        focus_skill_ids.add(
            row[1].skill_id
        )

    # -----------------------------------------
    # Phase C:
    # normal graceful fallback.
    #
    # Use remaining NON-DUE viable Skills before
    # consuming extra D2 review backlog.
    # -----------------------------------------

    if (
        len(focus)
        < TARGET_DISTINCT_SKILLS
    ):
        for row in normal_viable:
            if (
                len(focus)
                >= TARGET_DISTINCT_SKILLS
            ):
                break

            if (
                row[1].skill_id
                in focus_skill_ids
            ):
                continue

            focus.append(
                row
            )

            focus_skill_ids.add(
                row[1].skill_id
            )

    # -----------------------------------------
    # Phase D:
    # emergency fallback only.
    #
    # If there are not enough non-due viable Skills
    # to reach the preferred five-focus portfolio,
    # allow additional review-due Skills rather than
    # failing or unnecessarily shrinking diversity.
    #
    # This is a scarcity fallback, not normal P2-8
    # behavior.
    # -----------------------------------------

    if (
        len(focus)
        < TARGET_DISTINCT_SKILLS
    ):
        for review_candidate in (
            review_due_pool[
                review_slot_count:
            ]
        ):
            if (
                len(focus)
                >= TARGET_DISTINCT_SKILLS
            ):
                break

            row = viable_by_skill_id[
                review_candidate.skill_id
            ]

            if (
                row[1].skill_id
                in focus_skill_ids
            ):
                continue

            focus.append(
                row
            )

            focus_skill_ids.add(
                row[1].skill_id
            )

    # -----------------------------------------
    # All non-focus viable Skills remain reserves.
    # -----------------------------------------

    remaining = [
        row
        for row in viable
        if row[1].skill_id
        not in focus_skill_ids
    ]

    ordered = (
        focus
        + remaining
    )

    prepared = []

    for selection_rank, (
        _skill_priority,
        candidate,
    ) in enumerate(
        ordered,
        start=1,
    ):
        prepared.append(
            _copy_with_selection_rank(
                candidate=candidate,
                selection_rank=selection_rank,
            )
        )

    return tuple(
        prepared
    )
