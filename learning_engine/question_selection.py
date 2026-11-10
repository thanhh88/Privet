from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Max

from curriculum.models import Skill
from learning_engine.models import (
    AssessmentSession,
    SessionQuestion,
)
from questionbank.models import QuestionSkill


@dataclass(frozen=True)
class QuestionCandidate:
    """
    Pure input for adaptive Question ranking.

    Eligibility such as:
    - active Question;
    - linked to selected Skill;
    - not already in current assessment;

    is handled by the DB layer before candidates
    reach the pure ranking functions.
    """

    question_id: int
    source_id: str

    difficulty_score: Decimal

    is_primary: bool
    was_seen_before: bool

    # Most recent genuine learner exposure.
    #
    # None means the Question has never actually
    # been viewed by this learner.
    last_seen_at: datetime | None = None

    # True when this exact Question was genuinely
    # viewed in the learner's most recent prior
    # assessment.
    seen_in_previous_assessment: bool = False


@dataclass(frozen=True)
class RankedQuestionCandidate:
    question_id: int
    source_id: str

    difficulty_score: Decimal
    target_difficulty: Decimal
    difficulty_distance: Decimal

    is_primary: bool
    was_seen_before: bool

    last_seen_at: datetime | None = None
    seen_in_previous_assessment: bool = False


def _validate_target_difficulty(
    target_difficulty,
):
    target = Decimal(
        str(
            target_difficulty
        )
    )

    if not (
        Decimal("0")
        <= target
        <= Decimal("1")
    ):
        raise ValueError(
            "target_difficulty must be between "
            "0 and 1."
        )

    return target


def _validate_candidate(
    candidate,
):
    if not isinstance(
        candidate,
        QuestionCandidate,
    ):
        raise ValueError(
            "Each candidate must be a "
            "QuestionCandidate."
        )

    if (
        isinstance(
            candidate.question_id,
            bool,
        )
        or not isinstance(
            candidate.question_id,
            int,
        )
        or candidate.question_id < 1
    ):
        raise ValueError(
            "question_id must be a positive integer."
        )

    if (
        not isinstance(
            candidate.source_id,
            str,
        )
        or not candidate.source_id.strip()
    ):
        raise ValueError(
            "source_id must be a non-blank string."
        )

    difficulty = Decimal(
        str(
            candidate.difficulty_score
        )
    )

    if not (
        Decimal("0")
        <= difficulty
        <= Decimal("1")
    ):
        raise ValueError(
            "difficulty_score must be between "
            "0 and 1."
        )

    if not isinstance(
        candidate.is_primary,
        bool,
    ):
        raise ValueError(
            "is_primary must be boolean."
        )

    if not isinstance(
        candidate.was_seen_before,
        bool,
    ):
        raise ValueError(
            "was_seen_before must be boolean."
        )

    if (
        candidate.last_seen_at is not None
        and not isinstance(
            candidate.last_seen_at,
            datetime,
        )
    ):
        raise ValueError(
            "last_seen_at must be datetime or None."
        )

    if not isinstance(
        candidate.seen_in_previous_assessment,
        bool,
    ):
        raise ValueError(
            "seen_in_previous_assessment must "
            "be boolean."
        )

    return difficulty


def rank_question_candidates(
    *,
    candidates,
    target_difficulty,
):
    """
    Rank already-eligible Question candidates.

    Canonical NORMAL adaptive ranking policy
    ----------------------------------------

    1. Primary Skill mapping first.
    2. Questions never seen in prior assessments first.
    3. Smaller distance from target difficulty first.
    4. source_id ascending.
    5. question_id ascending.

    Important:
    This is the existing normal adaptive policy.

    D2 review-specific historical ordering is handled
    separately by rank_review_question_candidates().

    Target difficulty is supplied by the caller.

    For adaptive assessment:

        target_difficulty = current mastery estimate

    Therefore cold start naturally uses:

        M-hat = 0.50
        target difficulty = 0.50
    """

    target = _validate_target_difficulty(
        target_difficulty
    )

    candidates = list(
        candidates
    )

    question_ids = []
    ranked = []

    for candidate in candidates:
        difficulty = _validate_candidate(
            candidate
        )

        question_ids.append(
            candidate.question_id
        )

        distance = abs(
            difficulty
            - target
        )

        ranked.append(
            RankedQuestionCandidate(
                question_id=(
                    candidate.question_id
                ),
                source_id=(
                    candidate.source_id.strip()
                ),
                difficulty_score=(
                    difficulty
                ),
                target_difficulty=(
                    target
                ),
                difficulty_distance=(
                    distance
                ),
                is_primary=(
                    candidate.is_primary
                ),
                was_seen_before=(
                    candidate.was_seen_before
                ),
                last_seen_at=(
                    candidate.last_seen_at
                ),
                seen_in_previous_assessment=(
                    candidate
                    .seen_in_previous_assessment
                ),
            )
        )

    if len(
        question_ids
    ) != len(
        set(
            question_ids
        )
    ):
        raise ValueError(
            "Question candidates must not contain "
            "duplicate question_id values."
        )

    ranked.sort(
        key=lambda row: (
            # Primary Skill mapping wins.
            0
            if row.is_primary
            else 1,

            # Prefer historically unseen Questions.
            0
            if not row.was_seen_before
            else 1,

            # Then match learner ability.
            row.difficulty_distance,

            # Deterministic final tie-breaks.
            row.source_id,
            row.question_id,
        )
    )

    return tuple(
        ranked
    )


def rank_review_question_candidates(
    *,
    candidates,
    target_difficulty,
):
    """
    Rank Questions for a guaranteed D2 review Skill.

    Review Policy V1
    ----------------

    1. Prefer genuinely unseen Questions.

    2. Among seen Questions, avoid an exact Question
       from the immediately previous assessment when
       another candidate is available.

    3. Among Questions in the same historical tier,
       prefer the least-recently-seen Question.

    4. Then prefer a primary mapping to the selected
       Skill.

    5. Then prefer the Question nearest the current
       mastery estimate.

    6. source_id and question_id provide deterministic
       final tie-breaks.

    Exact recent repeats are therefore not forbidden.
    They become fallback candidates when better
    alternatives are exhausted.
    """

    ranked = list(
        rank_question_candidates(
            candidates=candidates,
            target_difficulty=target_difficulty,
        )
    )

    ranked.sort(
        key=lambda row: (
            # -------------------------------------
            # 1. Unseen first.
            # -------------------------------------
            0
            if not row.was_seen_before
            else 1,

            # -------------------------------------
            # 2. Avoid previous-assessment exact
            #    repeats when possible.
            # -------------------------------------
            0
            if not row.seen_in_previous_assessment
            else 1,

            # -------------------------------------
            # 3. Least-recently-seen first.
            #
            # Unseen rows have None and already win
            # the first key. Using -inf keeps their
            # ordering deterministic without inventing
            # a fake timestamp.
            # -------------------------------------
            (
                row.last_seen_at.timestamp()
                if row.last_seen_at is not None
                else float("-inf")
            ),

            # -------------------------------------
            # 4. Prefer primary Skill mapping.
            # -------------------------------------
            0
            if row.is_primary
            else 1,

            # -------------------------------------
            # 5. Match current learner ability.
            # -------------------------------------
            row.difficulty_distance,

            # -------------------------------------
            # 6. Deterministic tie-breaks.
            # -------------------------------------
            row.source_id,
            row.question_id,
        )
    )

    return tuple(
        ranked
    )


def select_top_question_candidate(
    *,
    candidates,
    target_difficulty,
):
    """
    Return the highest-ranked NORMAL adaptive
    Question candidate.

    Returns None when the candidate set is empty.
    """

    ranked = rank_question_candidates(
        candidates=candidates,
        target_difficulty=target_difficulty,
    )

    if not ranked:
        return None

    return ranked[0]


def build_question_candidates(
    *,
    user,
    session_id,
    skill_id,
):
    """
    Build eligible adaptive Question candidates
    for one selected Skill.

    Eligibility
    -----------

    - current session belongs to user;
    - current session is ACTIVE + ADAPTIVE;
    - selected Skill exists and is active;
    - Question is active;
    - Question is linked to selected Skill;
    - Question is not already present in the
      current assessment.

    Historical exposure
    -------------------

    Genuine exposure means:

        first_viewed_at IS NOT NULL

    Merely selecting a Question into an assessment
    does not count as learner exposure.

    For every candidate this builder derives:

    - was_seen_before;
    - last_seen_at;
    - seen_in_previous_assessment.

    These fields support D2 review ranking while the
    existing normal adaptive ranking remains unchanged.
    """

    if user.pk is None:
        raise ValueError(
            "User must be saved before question "
            "candidates can be built."
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
            "Question candidates can only be built "
            "for an active assessment session."
        )

    if (
        session.mode
        != AssessmentSession.Mode.ADAPTIVE
    ):
        raise ValueError(
            "Adaptive question selection requires "
            "an adaptive assessment session."
        )

    try:
        skill = Skill.objects.get(
            pk=skill_id,
            is_active=True,
        )

    except Skill.DoesNotExist as exc:
        raise ValueError(
            "Selected Skill does not exist "
            "or is inactive."
        ) from exc

    # =========================================
    # Current assessment exclusion
    # =========================================
    #
    # A Question already selected into THIS
    # assessment can never be selected again,
    # whether it has been viewed or not.

    current_question_ids = set(
        SessionQuestion.objects
        .filter(
            session=session,
        )
        .values_list(
            "question_id",
            flat=True,
        )
    )

    # =========================================
    # DB-eligible Question mappings
    # =========================================

    mappings = list(
        QuestionSkill.objects
        .filter(
            skill=skill,
            question__is_active=True,
        )
        .exclude(
            question_id__in=(
                current_question_ids
            ),
        )
        .select_related(
            "question"
        )
        .order_by(
            "question__source_id",
            "question_id",
        )
    )

    if not mappings:
        return ()

    candidate_question_ids = [
        mapping.question_id
        for mapping in mappings
    ]

    # =========================================
    # Historical genuine exposure
    # =========================================
    #
    # Only Questions that were actually viewed
    # count as seen.
    #
    # This intentionally preserves the existing
    # behavior where a Question selected into an
    # abandoned assessment but NEVER viewed does
    # not count as learner exposure.

    historical_exposures = (
        SessionQuestion.objects
        .filter(
            session__user=user,
            question_id__in=(
                candidate_question_ids
            ),
            first_viewed_at__isnull=False,
        )
        .exclude(
            session=session,
        )
    )

    # -----------------------------------------
    # Most recent genuine exposure per Question
    # -----------------------------------------

    last_seen_rows = (
        historical_exposures
        .values(
            "question_id",
        )
        .annotate(
            last_seen_at=Max(
                "first_viewed_at"
            ),
        )
    )

    last_seen_by_question_id = {
        row["question_id"]: (
            row["last_seen_at"]
        )
        for row in last_seen_rows
    }

    # =========================================
    # Immediately previous viewed assessment
    # =========================================
    #
    # "Previous assessment" means the most recently
    # finished prior assessment in which this learner
    # genuinely viewed at least one Question.
    #
    # It is NOT restricted to this Skill.
    #
    # Both ADAPTIVE and DIAGNOSTIC are assessment
    # history and may therefore be the immediately
    # previous learner exposure.
    #
    # Because the current session is the learner's
    # one ACTIVE assessment, historical viewed
    # sessions should already be finalized. The
    # session_id fallback makes ordering deterministic.

    previous_viewed_session_id = (
        SessionQuestion.objects
        .filter(
            session__user=user,

            # "Previous assessment" for D2 duplicate
            # avoidance means finalized assessment
            # history only.
            session__status=(
                AssessmentSession.Status.COMPLETED
            ),
            session__mode__in=[
                AssessmentSession.Mode.ADAPTIVE,
                AssessmentSession.Mode.DIAGNOSTIC,
            ],

            # The previous assessment must contain at
            # least one genuinely viewed Question.
            first_viewed_at__isnull=False,
        )
        .exclude(
            session=session,
        )
        .order_by(
            "-session__ended_at",
            "-session_id",
        )
        .values_list(
            "session_id",
            flat=True,
        )
        .first()
    )

    previous_assessment_question_ids = set()

    if (
        previous_viewed_session_id
        is not None
    ):
        previous_assessment_question_ids = set(
            SessionQuestion.objects
            .filter(
                session_id=(
                    previous_viewed_session_id
                ),
                question_id__in=(
                    candidate_question_ids
                ),
                first_viewed_at__isnull=False,
            )
            .values_list(
                "question_id",
                flat=True,
            )
        )

    # =========================================
    # Pure candidate objects
    # =========================================

    return tuple(
        QuestionCandidate(
            question_id=(
                mapping.question_id
            ),
            source_id=(
                mapping.question.source_id
            ),
            difficulty_score=(
                mapping.question.difficulty_score
            ),
            is_primary=(
                mapping.is_primary
            ),
            was_seen_before=(
                mapping.question_id
                in last_seen_by_question_id
            ),
            last_seen_at=(
                last_seen_by_question_id.get(
                    mapping.question_id
                )
            ),
            seen_in_previous_assessment=(
                mapping.question_id
                in previous_assessment_question_ids
            ),
        )
        for mapping in mappings
    )