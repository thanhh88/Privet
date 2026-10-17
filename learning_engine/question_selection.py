from dataclasses import dataclass
from decimal import Decimal
from curriculum.models import Skill
from learning_engine.models import (
    AssessmentSession,
    SessionQuestion,
)
from questionbank.models import QuestionSkill

@dataclass(frozen=True)
class QuestionCandidate:
    """
    Pure input for adaptive question ranking.

    Eligibility such as:
    - active Question;
    - linked to selected Skill;
    - not already in current assessment;

    is handled by the DB layer before candidates
    reach this pure ranking function.
    """

    question_id: int
    source_id: str

    difficulty_score: Decimal

    is_primary: bool
    was_seen_before: bool


@dataclass(frozen=True)
class RankedQuestionCandidate:
    question_id: int
    source_id: str

    difficulty_score: Decimal
    target_difficulty: Decimal
    difficulty_distance: Decimal

    is_primary: bool
    was_seen_before: bool


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

    return difficulty


def rank_question_candidates(
    *,
    candidates,
    target_difficulty,
):
    """
    Rank already-eligible Question candidates.

    Canonical adaptive ranking policy
    ---------------------------------

    1. Primary Skill mapping first.
    2. Questions never seen in prior assessments first.
    3. Smaller distance from target difficulty first.
    4. source_id ascending.
    5. question_id ascending.

    Target difficulty is supplied by the caller.
    For adaptive assessment the intended rule is:

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

            # Prefer unseen historical questions.
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


def select_top_question_candidate(
    *,
    candidates,
    target_difficulty,
):
    """
    Return the highest-ranked eligible Question.

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

    Eligibility:
    - current session belongs to user;
    - current session is ACTIVE + ADAPTIVE;
    - selected Skill exists and is active;
    - Question is active;
    - Question is linked to selected Skill;
    - Question is not already present in the
      current assessment.

    Historical novelty:
    was_seen_before=True only when the learner
    actually viewed that Question in another
    assessment session.
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

    if session.status != AssessmentSession.Status.ACTIVE:
        raise ValueError(
            "Question candidates can only be built "
            "for an active assessment session."
        )

    if session.mode != AssessmentSession.Mode.ADAPTIVE:
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

    # Questions already selected into THIS assessment
    # are absolutely ineligible.
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

    mappings = list(
        QuestionSkill.objects
        .filter(
            skill=skill,
            question__is_active=True,
        )
        .exclude(
            question_id__in=current_question_ids,
        )
        .select_related(
            "question"
        )
        .order_by(
            "question__source_id",
            "question_id",
        )
    )

    candidate_question_ids = [
        mapping.question_id
        for mapping in mappings
    ]

    # "Seen before" means genuine learner exposure:
    # first_viewed_at must exist.
    #
    # Exclude current session defensively even though
    # its questions were already removed above.
    previously_seen_question_ids = set(
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
        .values_list(
            "question_id",
            flat=True,
        )
        .distinct()
    )

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
                in previously_seen_question_ids
            ),
        )
        for mapping in mappings
    )