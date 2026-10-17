from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
)
from decimal import (
    Decimal,
    localcontext,
)
from django.contrib.auth import get_user_model
from django.utils import timezone

from curriculum.models import Skill
from learning_engine.mastery import (
    calculate_mastery,
    collect_skill_evidence,
)
from tracking.models import StudyEvent


_PRIORITY_QUANTUM = Decimal("0.00001")

_WEAKNESS_WEIGHT = Decimal("0.40")
_ERROR_WEIGHT = Decimal("0.20")
_REVIEW_WEIGHT = Decimal("0.15")
_UNCERTAINTY_WEIGHT = Decimal("0.10")
_RECENT_LEARNING_WEIGHT = Decimal("0.15")

_ERROR_SATURATION_COUNT = Decimal("3")
_REVIEW_HALF_LIFE_DAYS = Decimal("14")
_RECENT_LEARNING_DAYS = Decimal("7")

_SECONDS_PER_DAY = Decimal("86400")
_MICROSECONDS_PER_SECOND = Decimal(
    "1000000"
)


@dataclass(frozen=True)
class SkillPriorityResult:
    priority_score: Decimal

    weakness: Decimal
    recurring_errors: Decimal
    review_due: Decimal
    uncertainty: Decimal
    recent_learning: Decimal

@dataclass(frozen=True)
class UserSkillPriorityResult:
    skill_id: int
    skill_code: str

    mastery_estimate: Decimal
    observation_count: int
    consecutive_errors: int

    last_evidence_at: datetime | None
    last_learning_activity_at: datetime | None

    priority_score: Decimal

    weakness: Decimal
    recurring_errors: Decimal
    review_due: Decimal
    uncertainty: Decimal
    recent_learning: Decimal

def _timedelta_days(delta):
    total_seconds = (
        Decimal(
            delta.days * 86400
            + delta.seconds
        )
        +
        (
            Decimal(
                delta.microseconds
            )
            / _MICROSECONDS_PER_SECOND
        )
    )

    return (
        total_seconds
        / _SECONDS_PER_DAY
    )


def calculate_skill_priority(
    *,
    mastery_estimate,
    observation_count,
    consecutive_errors,
    last_evidence_at=None,
    last_learning_activity_at=None,
    as_of,
):
    """
    Calculate deterministic Skill Priority.

    Formula:

        P =
            0.40 * W
            + 0.20 * E
            + 0.15 * R
            + 0.10 * U
            + 0.15 * L

    Components:

        W = 1 - mastery_estimate

        E = min(
            1,
            consecutive_errors / 3
        )

        R =
            1 - 2 ^ (-days_since_last_evidence / 14)

        U =
            2 / (2 + observation_count)

        L = 1 when:
            - latest learning activity is within 7 days;
            - no later assessment evidence exists.

    A skill with no assessment evidence has R = 0.
    Exploration of unseen skills is represented by U,
    not by the review-due component.
    """

    mastery_estimate = Decimal(
        str(
            mastery_estimate
        )
    )

    if not (
        Decimal("0")
        <= mastery_estimate
        <= Decimal("1")
    ):
        raise ValueError(
            "mastery_estimate must be between "
            "0 and 1."
        )

    if (
        isinstance(
            observation_count,
            bool,
        )
        or not isinstance(
            observation_count,
            int,
        )
        or observation_count < 0
    ):
        raise ValueError(
            "observation_count must be a "
            "non-negative integer."
        )

    if (
        isinstance(
            consecutive_errors,
            bool,
        )
        or not isinstance(
            consecutive_errors,
            int,
        )
        or consecutive_errors < 0
    ):
        raise ValueError(
            "consecutive_errors must be a "
            "non-negative integer."
        )

    if (
        consecutive_errors
        > observation_count
    ):
        raise ValueError(
            "consecutive_errors must not exceed "
            "observation_count."
        )

    if as_of is None:
        raise ValueError(
            "as_of is required."
        )

    if (
        last_evidence_at is not None
        and last_evidence_at > as_of
    ):
        raise ValueError(
            "last_evidence_at must not be later "
            "than as_of."
        )

    if (
        last_learning_activity_at is not None
        and last_learning_activity_at > as_of
    ):
        raise ValueError(
            "last_learning_activity_at must not "
            "be later than as_of."
        )

    with localcontext() as context:
        context.prec = 40

        # -------------------------------------
        # W — weakness
        # -------------------------------------

        weakness = (
            Decimal("1")
            - mastery_estimate
        )

        # -------------------------------------
        # E — recurring errors
        # -------------------------------------

        recurring_errors = min(
            Decimal("1"),
            (
                Decimal(
                    consecutive_errors
                )
                / _ERROR_SATURATION_COUNT
            ),
        )

        # -------------------------------------
        # R — review due
        # -------------------------------------
        #
        # No prior evidence means there is
        # nothing to "review".
        #
        # Cold-start/exploration pressure is
        # represented separately by U.

        review_due = Decimal("0")

        if last_evidence_at is not None:
            age = (
                as_of
                - last_evidence_at
            )

            age_days = (
                _timedelta_days(
                    age
                )
            )

            exponent = -(
                age_days
                / _REVIEW_HALF_LIFE_DAYS
            )

            retained = (
                (
                    Decimal("2").ln()
                    * exponent
                )
                .exp()
            )

            review_due = (
                Decimal("1")
                - retained
            )

        # -------------------------------------
        # U — uncertainty
        # -------------------------------------

        uncertainty = (
            Decimal("2")
            /
            (
                Decimal("2")
                + Decimal(
                    observation_count
                )
            )
        )

        # -------------------------------------
        # L — recent learning
        # -------------------------------------

        recent_learning = Decimal("0")

        if (
            last_learning_activity_at
            is not None
        ):
            activity_age = (
                as_of
                - last_learning_activity_at
            )

            activity_age_days = (
                _timedelta_days(
                    activity_age
                )
            )

            is_recent = (
                activity_age_days
                <= _RECENT_LEARNING_DAYS
            )

            no_later_assessment = (
                last_evidence_at is None
                or last_evidence_at
                <= last_learning_activity_at
            )

            if (
                is_recent
                and no_later_assessment
            ):
                recent_learning = (
                    Decimal("1")
                )

        # -------------------------------------
        # Final priority
        # -------------------------------------

        priority_score = (
            _WEAKNESS_WEIGHT
            * weakness

            + _ERROR_WEIGHT
            * recurring_errors

            + _REVIEW_WEIGHT
            * review_due

            + _UNCERTAINTY_WEIGHT
            * uncertainty

            + _RECENT_LEARNING_WEIGHT
            * recent_learning
        )

        return SkillPriorityResult(
            priority_score=(
                priority_score.quantize(
                    _PRIORITY_QUANTUM
                )
            ),

            weakness=(
                weakness.quantize(
                    _PRIORITY_QUANTUM
                )
            ),

            recurring_errors=(
                recurring_errors.quantize(
                    _PRIORITY_QUANTUM
                )
            ),

            review_due=(
                review_due.quantize(
                    _PRIORITY_QUANTUM
                )
            ),

            uncertainty=(
                uncertainty.quantize(
                    _PRIORITY_QUANTUM
                )
            ),

            recent_learning=(
                recent_learning.quantize(
                    _PRIORITY_QUANTUM
                )
            ),
        )

def build_user_skill_priority(
    *,
    user,
    skill_id,
    as_of=None,
):
    """
    Build current priority for one User x Skill from
    authoritative database history.

    Assessment-derived values come from finalized
    assessment evidence and are recalculated at as_of.

    Learning recency comes from completed lesson /
    guided-practice StudyEvents.

    No SkillMastery.mastery_score fallback is used
    because cold-start learners may have a meaningful
    internal mastery estimate while persisted
    mastery_score is still NULL.
    """

    if user.pk is None:
        raise ValueError(
            "User must be saved before skill "
            "priority can be calculated."
        )

    if as_of is None:
        as_of = timezone.now()

    try:
        skill = Skill.objects.get(
            pk=skill_id
        )
    except Skill.DoesNotExist as exc:
        raise ValueError(
            f"Unknown skill_id: {skill_id}."
        ) from exc

    # -----------------------------------------
    # Assessment-derived learner state
    # -----------------------------------------

    evidence = collect_skill_evidence(
        user=user,
        skill_id=skill.pk,
    )

    mastery = calculate_mastery(
        evidence,
        as_of=as_of,
    )

    # -----------------------------------------
    # Learning recency
    # -----------------------------------------
    #
    # Use authoritative StudyEvent history rather
    # than trusting a potentially stale cached field.

    latest_learning_event = (
        StudyEvent.objects
        .filter(
            user=user,
            event_type__in=[
                StudyEvent.EventType.LESSON_COMPLETED,
                StudyEvent.EventType.PRACTICE_COMPLETED,
            ],
            lesson__lesson_skills__skill_id=(
                skill.pk
            ),
            occurred_at__lte=as_of,
        )
        .order_by(
            "-occurred_at",
            "-study_event_id",
        )
        .first()
    )

    last_learning_activity_at = (
        latest_learning_event.occurred_at
        if latest_learning_event is not None
        else None
    )

    priority = calculate_skill_priority(
        mastery_estimate=(
            mastery.mastery_estimate
        ),

        observation_count=(
            mastery.observation_count
        ),

        consecutive_errors=(
            mastery.consecutive_errors
        ),

        last_evidence_at=(
            mastery.last_evidence_at
        ),

        last_learning_activity_at=(
            last_learning_activity_at
        ),

        as_of=as_of,
    )

    return UserSkillPriorityResult(
        skill_id=skill.pk,
        skill_code=skill.skill_code,

        mastery_estimate=(
            mastery.mastery_estimate
        ),

        observation_count=(
            mastery.observation_count
        ),

        consecutive_errors=(
            mastery.consecutive_errors
        ),

        last_evidence_at=(
            mastery.last_evidence_at
        ),

        last_learning_activity_at=(
            last_learning_activity_at
        ),

        priority_score=(
            priority.priority_score
        ),

        weakness=(
            priority.weakness
        ),

        recurring_errors=(
            priority.recurring_errors
        ),

        review_due=(
            priority.review_due
        ),

        uncertainty=(
            priority.uncertainty
        ),

        recent_learning=(
            priority.recent_learning
        ),
    )

def rank_user_skill_priorities(
    *,
    user,
    skill_ids,
    as_of=None,
):
    """
    Rank candidate Skills for one learner.

    Candidate eligibility is decided by the caller.
    This function only calculates and ranks priority.

    Ranking:
    1. higher priority_score first;
    2. skill_code ascending;
    3. skill_id ascending.
    """

    if user.pk is None:
        raise ValueError(
            "User must be saved before skill "
            "priorities can be ranked."
        )

    if as_of is None:
        as_of = timezone.now()

    skill_ids = list(
        skill_ids
    )

    if len(skill_ids) != len(set(skill_ids)):
        raise ValueError(
            "skill_ids must not contain duplicates."
        )

    results = []

    for skill_id in skill_ids:
        result = build_user_skill_priority(
            user=user,
            skill_id=skill_id,
            as_of=as_of,
        )

        results.append(
            result
        )

    results.sort(
        key=lambda row: (
            -row.priority_score,
            row.skill_code,
            row.skill_id,
        )
    )

    return tuple(results)


def select_top_priority_skill(
    *,
    user,
    skill_ids,
    as_of=None,
):
    """
    Return the highest-priority candidate Skill.

    Returns None when the candidate set is empty.
    """

    ranked = rank_user_skill_priorities(
        user=user,
        skill_ids=skill_ids,
        as_of=as_of,
    )

    if not ranked:
        return None

    return ranked[0]