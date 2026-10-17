from dataclasses import dataclass
from decimal import Decimal


ADAPTIVE_ASSEMBLY_RULE_VERSION = (
    "adaptive_assembly_v1"
)

TARGET_QUESTION_COUNT = 10
TARGET_DISTINCT_SKILLS = 5
PREFERRED_MAX_PER_SKILL = 2


@dataclass(frozen=True)
class AssemblySkillCandidate:
    """
    Pure input for adaptive assessment assembly.

    ranked_question_ids must already follow the
    canonical 9D.9 Question ranking for this Skill:

        primary
        -> unseen
        -> difficulty distance
        -> source_id
        -> question_id

    Question IDs may legitimately appear in more
    than one Skill candidate because one Question
    may map to multiple Skills.
    """

    skill_id: int
    skill_code: str
    priority_score: Decimal
    ranked_question_ids: tuple[int, ...]


@dataclass(frozen=True)
class AdaptiveAssessmentPlanSlot:
    """
    One planned assessment slot.

    focus_skill_* describes WHY this Question was
    selected into the assessment.

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

    return (
        priority,
        question_ids,
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

    Canonical policy
    ----------------

    Hard rules:
    - target_question_count unique Questions;
    - no duplicate Question inside the assessment;
    - insufficient global Question availability fails.

    Diversity phase:
    - first try to introduce target_distinct_skills
      distinct focus Skills;
    - unseen focus Skills are chosen by canonical
      Skill Priority order.

    Fill phase:
    1. prefer Skills below preferred_max_per_skill
       and different from the previous Skill;
    2. then any Skill below the preferred cap;
    3. then relax the cap, still avoiding adjacent
       duplicate Skills where possible;
    4. finally allow the same Skill consecutively
       when no alternative remains.

    Skill order:
        priority_score DESC
        skill_code ASC
        skill_id ASC

    Within each Skill, ranked_question_ids order is
    preserved exactly.

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

    # Canonical Skill Priority order.
    normalized.sort(
        key=lambda row: (
            -row[1],
            row[0].skill_code.strip(),
            row[0].skill_id,
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
        # Phase A:
        # diversity pass
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
                # `available` already follows
                # canonical priority order.
                chosen = (
                    unused_skill_options[0]
                )

        # =====================================
        # Phase B:
        # priority fill
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
                chosen = (
                    preferred_non_adjacent[0]
                )

            elif preferred_any:
                chosen = (
                    preferred_any[0]
                )

            elif relaxed_non_adjacent:
                chosen = (
                    relaxed_non_adjacent[0]
                )

            else:
                # Only the previous Skill remains
                # viable. Adjacent repetition is
                # therefore allowed.
                chosen = available[0]

        (
            candidate,
            priority,
            question_id,
            question_rank,
        ) = chosen

        skill_counts[
            candidate.skill_id
        ] += 1

        used_skill_ids.add(
            candidate.skill_id
        )

        used_question_ids.add(
            question_id
        )

        slots.append(
            AdaptiveAssessmentPlanSlot(
                slot_number=len(slots) + 1,

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
                    skill_counts[
                        candidate.skill_id
                    ]
                ),
            )
        )

        previous_skill_id = (
            candidate.skill_id
        )

    return tuple(
        slots
    )