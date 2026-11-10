"""Recommendation feature calculation for eligible candidates.

R4 owns feature extraction only.

It consumes R3-eligible candidates and produces deterministic, explainable
feature vectors.  R4 deliberately does not apply recommendation weights,
compute a final score, diversify candidates, or assign ranks; those belong
to later recommendation phases.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext

from django.conf import settings
from django.utils import timezone

from curriculum.prerequisites import get_prerequisite_closure
from learning_engine.adaptive_eligibility import (
    resolve_adaptive_eligible_skill_ids,
)
from learning_engine.cold_start import LearnerEntryRoute
from learning_engine.learner_entry import resolve_learner_entry_state
from learning_engine.models import (
    AssessmentSession,
    SessionQuestion,
    SkillMastery,
)
from learning_engine.priority import build_user_skill_priority
from recommendation.candidates import (
    RecommendationCandidate,
    RecommendationCandidateType,
)
from recommendation.eligibility import (
    evaluate_recommendation_eligibility,
    filter_eligible_recommendation_candidates,
)


FEATURE_VERSION = "recommendation_features_v1"
_FEATURE_QUANTUM = Decimal("0.00001")
_ZERO = Decimal("0")
_ONE = Decimal("1")
_SECONDS_PER_DAY = Decimal("86400")
_MICROSECONDS_PER_SECOND = Decimal("1000000")


@dataclass(frozen=True)
class RecommendationFeatureVector:
    """Explainable R4 features for one eligible recommendation candidate.

    Skill-derived need features use *peak need* aggregation across the
    candidate's skill scope.  This preserves a strong need signal when an
    activity covers multiple Skills instead of hiding it in a mean.

    ``None`` means that a feature is not supported/applicable for this
    candidate.  R4 intentionally does not manufacture neutral values for
    missing concepts.  R5 must handle applicability explicitly.
    """

    candidate: RecommendationCandidate
    feature_version: str
    skill_scope_ids: tuple[int, ...]

    weakness: Decimal | None
    recurring_errors: Decimal | None
    review_urgency: Decimal | None
    uncertainty: Decimal | None
    recent_learning: Decimal | None

    prerequisite_readiness: Decimal | None
    continuity: Decimal
    novelty: Decimal

    # Current Lesson/activity metadata does not expose a reliable common
    # difficulty value.  Keep this explicit instead of inventing one.
    difficulty_fit: Decimal | None = None


def _validate_user(user):
    if user.pk is None:
        raise ValueError(
            "User must be saved before recommendation features "
            "can be calculated."
        )


def _validate_as_of(as_of):
    if as_of is None:
        return timezone.now()

    if not isinstance(as_of, datetime):
        raise ValueError("as_of must be a datetime.")

    if timezone.is_naive(as_of):
        raise ValueError("as_of must be timezone-aware.")

    return as_of


def _validate_candidates(candidates):
    candidates = tuple(candidates)
    seen_keys = set()

    for candidate in candidates:
        if not isinstance(candidate, RecommendationCandidate):
            raise TypeError(
                "candidates must contain RecommendationCandidate "
                "instances only."
            )

        if candidate.candidate_key in seen_keys:
            raise ValueError(
                "Recommendation candidate keys must be unique."
            )

        seen_keys.add(candidate.candidate_key)

    return candidates


def _quantize(value):
    return Decimal(value).quantize(_FEATURE_QUANTUM)


def _timedelta_days(delta):
    total_seconds = (
        Decimal(delta.days * 86400 + delta.seconds)
        + (
            Decimal(delta.microseconds)
            / _MICROSECONDS_PER_SECOND
        )
    )
    return total_seconds / _SECONDS_PER_DAY


def _resume_assessment_skill_scope(candidate):
    if candidate.assessment_session_id is None:
        return ()

    snapshots = SessionQuestion.objects.filter(
        session_id=candidate.assessment_session_id,
    ).values_list("skill_snapshot", flat=True)

    skill_ids = set()
    for snapshot in snapshots:
        if not isinstance(snapshot, list):
            continue
        for row in snapshot:
            if not isinstance(row, dict):
                continue
            skill_id = row.get("skill_id")
            if isinstance(skill_id, bool):
                continue
            try:
                skill_id = int(skill_id)
            except (TypeError, ValueError):
                continue
            if skill_id > 0:
                skill_ids.add(skill_id)

    return tuple(sorted(skill_ids))


def _candidate_skill_scope(*, user, candidate):
    if candidate.candidate_type in {
        RecommendationCandidateType.START_LESSON,
        RecommendationCandidateType.CONTINUE_LESSON,
    }:
        return tuple(sorted({int(value) for value in candidate.skill_ids}))

    if (
        candidate.candidate_type
        == RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT
    ):
        return tuple(
            sorted(
                {
                    int(value)
                    for value in resolve_adaptive_eligible_skill_ids(
                        user=user,
                    )
                }
            )
        )

    if (
        candidate.candidate_type
        == RecommendationCandidateType.RESUME_ASSESSMENT
    ):
        return _resume_assessment_skill_scope(candidate)

    return ()


def _peak_skill_feature(*, skill_scope_ids, priorities, attribute):
    if not skill_scope_ids:
        return None

    values = [
        getattr(priorities[skill_id], attribute)
        for skill_id in skill_scope_ids
    ]
    if not values:
        return None

    return _quantize(max(values))


def _vocabulary_review_urgency(*, candidate, as_of):
    """Normalize vocabulary backlog urgency to [0, 1].

    Two policy-backed pressures are used:
    - backlog pressure saturates at VOCABULARY_SRS.max_due_per_request;
    - overdue-age pressure saturates at the longest configured Leitner
      interval.

    The stronger pressure wins.  No new arbitrary time threshold is added
    by the recommender.
    """

    if candidate.vocabulary_due_count <= 0:
        raise ValueError(
            "Vocabulary review candidate must have a positive due count."
        )

    oldest_due_at = candidate.vocabulary_oldest_due_at
    if oldest_due_at is None:
        raise ValueError(
            "Vocabulary review candidate requires vocabulary_oldest_due_at."
        )
    if timezone.is_naive(oldest_due_at):
        raise ValueError("vocabulary_oldest_due_at must be timezone-aware.")
    if oldest_due_at > as_of:
        raise ValueError(
            "Vocabulary review candidate cannot be due in the future."
        )

    policy = settings.VOCABULARY_SRS
    max_due = int(policy["max_due_per_request"])
    intervals = tuple(int(value) for value in policy["intervals"].values())
    max_interval_days = max(intervals)

    with localcontext() as context:
        context.prec = 40

        backlog_pressure = min(
            _ONE,
            Decimal(candidate.vocabulary_due_count) / Decimal(max_due),
        )

        overdue_days = _timedelta_days(as_of - oldest_due_at)
        overdue_pressure = min(
            _ONE,
            overdue_days / Decimal(max_interval_days),
        )

        return _quantize(max(backlog_pressure, overdue_pressure))


def _prerequisite_readiness(*, user, candidate, route):
    """Return readiness strength for an adaptive START_LESSON candidate.

    R3 already enforces threshold eligibility.  R4 therefore measures the
    strength of the satisfied prerequisite state as the minimum persisted
    mastery across all active transitive prerequisites.  A Lesson with no
    prerequisites is fully ready (1.0).

    Beginner sequencing intentionally does not use mastery prerequisites, so
    this feature is not applicable there and returns None.
    """

    if (
        route != LearnerEntryRoute.ADAPTIVE
        or candidate.candidate_type
        != RecommendationCandidateType.START_LESSON
    ):
        return None

    prerequisite_ids = set()
    for skill_id in candidate.skill_ids:
        prerequisite_ids.update(
            skill.pk
            for skill in get_prerequisite_closure(
                skill_id,
                active_only=True,
            )
        )

    if not prerequisite_ids:
        return _quantize(_ONE)

    mastery_by_skill_id = {
        row.skill_id: row.mastery_score
        for row in SkillMastery.objects.filter(
            user=user,
            skill_id__in=prerequisite_ids,
        ).only("skill_id", "mastery_score")
    }

    values = []
    for skill_id in sorted(prerequisite_ids):
        value = mastery_by_skill_id.get(skill_id)
        if value is None:
            # This should be unreachable for an R3-eligible adaptive START
            # Lesson, but keeping None is safer than fabricating readiness.
            return None
        values.append(Decimal(value))

    return _quantize(min(values))


def _continuity(candidate):
    if candidate.candidate_type in {
        RecommendationCandidateType.CONTINUE_LESSON,
        RecommendationCandidateType.RESUME_ASSESSMENT,
    }:
        return _quantize(_ONE)
    return _quantize(_ZERO)


def _novelty(*, user, candidate):
    if candidate.candidate_type == RecommendationCandidateType.START_LESSON:
        # R2 only emits START_LESSON when no in-progress/completed progress
        # exists for the learner.
        return _quantize(_ONE)

    if (
        candidate.candidate_type
        == RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT
    ):
        prior_finished = AssessmentSession.objects.filter(
            user=user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status__in=(
                AssessmentSession.Status.COMPLETED,
                AssessmentSession.Status.ABANDONED,
            ),
        ).count()

        with localcontext() as context:
            context.prec = 40
            return _quantize(
                _ONE / (_ONE + Decimal(prior_finished))
            )

    return _quantize(_ZERO)


def build_recommendation_features(*, user, candidates=None, as_of=None):
    """Build deterministic R4 features for R3-eligible candidates.

    When ``candidates`` is omitted, the full R2 -> R3 pipeline is invoked and
    features are calculated only for eligible candidates.  When candidates
    are supplied explicitly, R3 is re-checked and any ineligible candidate
    causes a ValueError.  This prevents later scoring from accidentally
    bypassing learner-entry or prerequisite gates.

    The function performs no writes and preserves candidate order.  It does
    not calculate final recommendation scores or ranks.
    """

    _validate_user(user)
    as_of = _validate_as_of(as_of)

    if candidates is None:
        candidates = filter_eligible_recommendation_candidates(
            user=user,
            as_of=as_of,
        )
    else:
        candidates = _validate_candidates(candidates)
        decisions = evaluate_recommendation_eligibility(
            user=user,
            candidates=candidates,
            as_of=as_of,
        )
        ineligible_keys = tuple(
            decision.candidate.candidate_key
            for decision in decisions
            if not decision.is_eligible
        )
        if ineligible_keys:
            raise ValueError(
                "R4 requires R3-eligible candidates only: "
                + ", ".join(ineligible_keys)
            )

    candidates = _validate_candidates(candidates)
    state = resolve_learner_entry_state(user=user)

    scope_by_key = {
        candidate.candidate_key: _candidate_skill_scope(
            user=user,
            candidate=candidate,
        )
        for candidate in candidates
    }

    all_skill_ids = sorted(
        {
            skill_id
            for skill_scope in scope_by_key.values()
            for skill_id in skill_scope
        }
    )

    priorities = {
        skill_id: build_user_skill_priority(
            user=user,
            skill_id=skill_id,
            as_of=as_of,
        )
        for skill_id in all_skill_ids
    }

    feature_vectors = []

    for candidate in candidates:
        skill_scope_ids = scope_by_key[candidate.candidate_key]

        weakness = _peak_skill_feature(
            skill_scope_ids=skill_scope_ids,
            priorities=priorities,
            attribute="weakness",
        )
        recurring_errors = _peak_skill_feature(
            skill_scope_ids=skill_scope_ids,
            priorities=priorities,
            attribute="recurring_errors",
        )
        uncertainty = _peak_skill_feature(
            skill_scope_ids=skill_scope_ids,
            priorities=priorities,
            attribute="uncertainty",
        )
        recent_learning = _peak_skill_feature(
            skill_scope_ids=skill_scope_ids,
            priorities=priorities,
            attribute="recent_learning",
        )

        if (
            candidate.candidate_type
            == RecommendationCandidateType.VOCABULARY_REVIEW
        ):
            review_urgency = _vocabulary_review_urgency(
                candidate=candidate,
                as_of=as_of,
            )
        else:
            review_urgency = _peak_skill_feature(
                skill_scope_ids=skill_scope_ids,
                priorities=priorities,
                attribute="review_due",
            )

        feature_vectors.append(
            RecommendationFeatureVector(
                candidate=candidate,
                feature_version=FEATURE_VERSION,
                skill_scope_ids=skill_scope_ids,
                weakness=weakness,
                recurring_errors=recurring_errors,
                review_urgency=review_urgency,
                uncertainty=uncertainty,
                recent_learning=recent_learning,
                prerequisite_readiness=_prerequisite_readiness(
                    user=user,
                    candidate=candidate,
                    route=state.route,
                ),
                continuity=_continuity(candidate),
                novelty=_novelty(
                    user=user,
                    candidate=candidate,
                ),
                difficulty_fit=None,
            )
        )

    return tuple(feature_vectors)
