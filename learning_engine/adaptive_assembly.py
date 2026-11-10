from dataclasses import dataclass
from decimal import Decimal


ADAPTIVE_ASSEMBLY_RULE_VERSION = (
    "adaptive_assembly_v1"
)

TARGET_QUESTION_COUNT = 10
TARGET_DISTINCT_SKILLS = 5
PREFERRED_MAX_PER_SKILL = 2

ADAPTIVE_SELECTION_REASONS = {
    "normal",
    "review",
}


@dataclass(frozen=True)
class AssemblySkillCandidate:
    """
    Pure input for adaptive assessment assembly.

    ranked_question_ids must already follow the
    appropriate upstream Question-ranking policy:

    - normal adaptive Skill:
        canonical normal ranking;
    - guaranteed D2 review Skill:
        D2 review-specific ranking.

    The pure planner does not re-rank Questions.

    selection_reason describes why Questions selected
    through this Skill candidate enter the assessment.

    seen_before_question_ids is a frozen snapshot of
    Questions that had genuinely been viewed by the
    learner before this assessment was assembled.

    Question IDs may legitimately appear in more than
    one Skill candidate because one Question may map to
    multiple Skills.
    """

    skill_id: int
    skill_code: str
    priority_score: Decimal

    ranked_question_ids: tuple[int, ...]

    # Preparation owns portfolio ordering. When set,
    # selection_rank overrides raw priority ordering.
    selection_rank: int | None = None

    # C3 historical planner metadata.
    selection_reason: str = "normal"
    seen_before_question_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class AdaptiveAssessmentPlanSlot:
    """
    One planned adaptive-assessment slot.

    focus_skill_* describes WHY this Question was
    selected into the assessment.

    selection_reason and was_seen_before are frozen at
    planning time and are later persisted directly onto
    SessionQuestion. They must not be recalculated by
    the persistence layer.

    A multi-skill Question may later provide mastery
    evidence to several Skills through its immutable
    SessionQuestion skill snapshot.
    """

    slot_number: int

    focus_skill_id: int
    focus_skill_code: str

    priority_score: Decimal

    question_id: int

    # 1 means the highest-ranked Question for this
    # Skill was used, 2 means the first was already
    # consumed by another focus Skill, etc.
    question_rank_within_skill: int

    # Number of Questions assigned to this focus
    # Skill including the current slot.
    occurrence_for_skill: int

    # C3 frozen audit metadata.
    selection_reason: str
    was_seen_before: bool


def _validate_positive_int(
    *,
    value,
    field_name,
):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
    ):
        raise ValueError(
            f"{field_name} must be a positive integer."
        )


def _normalize_priority_score(
    value,
):
    priority = Decimal(
        str(value)
    )

    if not priority.is_finite():
        raise ValueError(
            "priority_score must be finite."
        )

    if not (
        Decimal("0")
        <= priority
        <= Decimal("1")
    ):
        raise ValueError(
            "priority_score must be between 0 and 1."
        )

    return priority


def _validate_skill_candidate(
    candidate,
):
    if not isinstance(
        candidate,
        AssemblySkillCandidate,
    ):
        raise ValueError(
            "Each skill candidate must be an "
            "AssemblySkillCandidate."
        )

    _validate_positive_int(
        value=candidate.skill_id,
        field_name="skill_id",
    )

    if (
        not isinstance(
            candidate.skill_code,
            str,
        )
        or not candidate.skill_code.strip()
    ):
        raise ValueError(
            "skill_code must be a non-blank string."
        )

    priority = _normalize_priority_score(
        candidate.priority_score
    )

    question_ids = tuple(
        candidate.ranked_question_ids
    )

    for question_id in question_ids:
        _validate_positive_int(
            value=question_id,
            field_name="question_id",
        )

    if len(
        question_ids
    ) != len(
        set(question_ids)
    ):
        raise ValueError(
            "ranked_question_ids must not contain "
            "duplicates within one Skill."
        )

    if candidate.selection_rank is not None:
        _validate_positive_int(
            value=candidate.selection_rank,
            field_name="selection_rank",
        )

    if (
        not isinstance(
            candidate.selection_reason,
            str,
        )
        or candidate.selection_reason
        not in ADAPTIVE_SELECTION_REASONS
    ):
        raise ValueError(
            "selection_reason must be 'normal' "
            "or 'review'."
        )

    seen_before_question_ids = tuple(
        candidate.seen_before_question_ids
    )

    for question_id in seen_before_question_ids:
        _validate_positive_int(
            value=question_id,
            field_name=(
                "seen_before_question_id"
            ),
        )

    if len(
        seen_before_question_ids
    ) != len(
        set(
            seen_before_question_ids
        )
    ):
        raise ValueError(
            "seen_before_question_ids must not "
            "contain duplicates."
        )

    if not set(
        seen_before_question_ids
    ).issubset(
        set(question_ids)
    ):
        raise ValueError(
            "seen_before_question_ids must be a "
            "subset of ranked_question_ids."
        )

    return (
        priority,
        question_ids,
        frozenset(
            seen_before_question_ids
        ),
    )


def _candidate_sort_key(
    *,
    candidate,
    priority,
):
    """
    Preserve preparation-owned portfolio order when a
    selection_rank is available.

    Legacy / direct pure-planner callers that do not
    provide selection_rank retain the canonical raw
    priority ordering, with stable skill_id fallback.
    """

    if candidate.selection_rank is not None:
        return (
            0,
            candidate.selection_rank,
            candidate.skill_code.strip(),
            candidate.skill_id,
        )

    return (
        1,
        0,
        -priority,
        candidate.skill_id,
    )


def _next_available_question(
    *,
    question_ids,
    used_question_ids,
):
    """
    Return:
        (question_id, one-based rank)

    for the highest-ranked Question that has not
    already been consumed by another assessment slot.
    """

    for index, question_id in enumerate(
        question_ids,
        start=1,
    ):
        if question_id not in used_question_ids:
            return (
                question_id,
                index,
            )

    return None


def plan_adaptive_assessment(
    *,
    skill_candidates,
    target_question_count=(
        TARGET_QUESTION_COUNT
    ),
    target_distinct_skills=(
        TARGET_DISTINCT_SKILLS
    ),
    preferred_max_per_skill=(
        PREFERRED_MAX_PER_SKILL
    ),
):
    """
    Build a deterministic adaptive-assessment plan.

    Hard rules
    ----------
    - target_question_count unique Questions;
    - no duplicate Question inside the assessment;
    - insufficient global Question availability fails.

    Diversity phase
    ---------------
    - first try to introduce target_distinct_skills
      distinct focus Skills;
    - candidate ordering is taken from selection_rank
      when preparation supplied it;
    - otherwise canonical raw priority ordering is used.

    Fill phase
    ----------
    1. prefer Skills below preferred_max_per_skill
       and different from the previous Skill;
    2. then any Skill below the preferred cap;
    3. then relax the cap, still avoiding adjacent
       duplicate Skills where possible;
    4. finally allow the same Skill consecutively
       when no alternative remains.

    Within each Skill, ranked_question_ids order is
    preserved exactly. The planner does not know or
    re-run normal/review Question ranking.

    C3 audit metadata
    -----------------
    - selection_reason is copied from the chosen
      AssemblySkillCandidate;
    - was_seen_before is snapshotted by membership in
      candidate.seen_before_question_ids.

    No randomness is used.
    """

    _validate_positive_int(
        value=target_question_count,
        field_name="target_question_count",
    )

    _validate_positive_int(
        value=target_distinct_skills,
        field_name="target_distinct_skills",
    )

    _validate_positive_int(
        value=preferred_max_per_skill,
        field_name="preferred_max_per_skill",
    )

    if (
        target_distinct_skills
        > target_question_count
    ):
        raise ValueError(
            "target_distinct_skills must not exceed "
            "target_question_count."
        )

    raw_candidates = list(
        skill_candidates
    )

    normalized = []
    skill_ids = []

    for candidate in raw_candidates:
        (
            priority,
            question_ids,
            seen_before_question_ids,
        ) = _validate_skill_candidate(
            candidate
        )

        skill_ids.append(
            candidate.skill_id
        )

        normalized.append(
            (
                candidate,
                priority,
                question_ids,
                seen_before_question_ids,
            )
        )

    if len(
        skill_ids
    ) != len(
        set(skill_ids)
    ):
        raise ValueError(
            "skill_candidates must not contain "
            "duplicate skill_id values."
        )

    # Preparation-owned selection_rank wins when
    # present. Direct pure-planner callers without it
    # retain canonical priority ordering.
    normalized.sort(
        key=lambda row: (
            _candidate_sort_key(
                candidate=row[0],
                priority=row[1],
            )
        )
    )

    # -----------------------------------------
    # Global unique-Question preflight
    # -----------------------------------------

    all_question_ids = set()

    for (
        _candidate,
        _priority,
        question_ids,
        _seen_before_question_ids,
    ) in normalized:
        all_question_ids.update(
            question_ids
        )

    if len(
        all_question_ids
    ) < target_question_count:
        raise ValueError(
            "Not enough unique Questions to build "
            "the requested adaptive assessment."
        )

    used_question_ids = set()
    used_skill_ids = set()

    skill_counts = {
        candidate.skill_id: 0
        for (
            candidate,
            _priority,
            _question_ids,
            _seen_before_question_ids,
        ) in normalized
    }

    slots = []
    previous_skill_id = None

    # -----------------------------------------
    # Slot-by-slot deterministic assembly
    # -----------------------------------------

    while len(slots) < target_question_count:
        available = []

        for (
            candidate,
            priority,
            question_ids,
            seen_before_question_ids,
        ) in normalized:
            next_question = (
                _next_available_question(
                    question_ids=question_ids,
                    used_question_ids=(
                        used_question_ids
                    ),
                )
            )

            if next_question is None:
                continue

            (
                question_id,
                question_rank,
            ) = next_question

            available.append(
                (
                    candidate,
                    priority,
                    question_id,
                    question_rank,
                    seen_before_question_ids,
                )
            )

        if not available:
            # The global preflight should normally
            # make this unreachable, but keep a
            # defensive invariant.
            raise ValueError(
                "Adaptive assessment assembly ran "
                "out of unique Questions."
            )

        chosen = None

        # =====================================
        # Phase A: diversity pass
        # =====================================

        if (
            len(used_skill_ids)
            < target_distinct_skills
        ):
            unused_skill_options = [
                row
                for row in available
                if (
                    row[0].skill_id
                    not in used_skill_ids
                )
            ]

            if unused_skill_options:
                # `available` already follows the
                # prepared / canonical Skill order.
                chosen = (
                    unused_skill_options[0]
                )

        # =====================================
        # Phase B: balanced fill
        # =====================================

        if chosen is None:
            preferred_non_adjacent = [
                row
                for row in available
                if (
                    skill_counts[
                        row[0].skill_id
                    ]
                    < preferred_max_per_skill
                    and row[0].skill_id
                    != previous_skill_id
                )
            ]

            preferred_any = [
                row
                for row in available
                if (
                    skill_counts[
                        row[0].skill_id
                    ]
                    < preferred_max_per_skill
                )
            ]

            relaxed_non_adjacent = [
                row
                for row in available
                if (
                    row[0].skill_id
                    != previous_skill_id
                )
            ]

            if preferred_non_adjacent:
                chosen = preferred_non_adjacent[0]

            elif preferred_any:
                chosen = preferred_any[0]

            elif relaxed_non_adjacent:
                chosen = relaxed_non_adjacent[0]

            else:
                chosen = available[0]

        (
            candidate,
            priority,
            question_id,
            question_rank,
            seen_before_question_ids,
        ) = chosen

        used_question_ids.add(
            question_id
        )

        used_skill_ids.add(
            candidate.skill_id
        )

        skill_counts[
            candidate.skill_id
        ] += 1

        occurrence_for_skill = (
            skill_counts[
                candidate.skill_id
            ]
        )

        slots.append(
            AdaptiveAssessmentPlanSlot(
                slot_number=(
                    len(slots) + 1
                ),
                focus_skill_id=(
                    candidate.skill_id
                ),
                focus_skill_code=(
                    candidate.skill_code.strip()
                ),
                priority_score=priority,
                question_id=question_id,
                question_rank_within_skill=(
                    question_rank
                ),
                occurrence_for_skill=(
                    occurrence_for_skill
                ),
                selection_reason=(
                    candidate.selection_reason
                ),
                was_seen_before=(
                    question_id
                    in seen_before_question_ids
                ),
            )
        )

        previous_skill_id = (
            candidate.skill_id
        )

    return tuple(
        slots
    )
