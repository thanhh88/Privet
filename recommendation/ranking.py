"""Weighted recommendation scoring and deterministic ranking.

R5 owns score aggregation and rank assignment only.

It consumes R4 feature vectors, applies one versioned/configurable policy,
and produces deterministic Top-N recommendations.  R5 does not generate
candidates, decide eligibility, mutate learner state, or persist/log
recommendations; those responsibilities belong to other recommendation
phases.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext

from django.conf import settings

from recommendation.candidates import (
    RecommendationCandidate,
    RecommendationCandidateType,
)
from recommendation.features import (
    FEATURE_VERSION,
    RecommendationFeatureVector,
    build_recommendation_features,
)


_SCORE_QUANTUM = Decimal("0.00001")
_ZERO = Decimal("0")
_ONE = Decimal("1")

FEATURE_NAMES = (
    "weakness",
    "recurring_errors",
    "review_urgency",
    "uncertainty",
    "recent_learning",
    "prerequisite_readiness",
    "continuity",
    "novelty",
    "difficulty_fit",
)

# Exact-score ties must never depend on database/query order.  Ongoing work
# wins first, then review, then new learning, then a fresh adaptive test.
# The weighted score remains the primary ordering criterion.
_TIE_BREAK_TYPE_ORDER = {
    RecommendationCandidateType.RESUME_ASSESSMENT: 0,
    RecommendationCandidateType.CONTINUE_LESSON: 1,
    RecommendationCandidateType.VOCABULARY_REVIEW: 2,
    RecommendationCandidateType.START_LESSON: 3,
    RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT: 4,
}


@dataclass(frozen=True)
class RecommendationScoreComponent:
    """One explainable contribution to an R5 score."""

    feature_name: str
    value: Decimal
    configured_weight: Decimal
    effective_weight: Decimal
    contribution: Decimal


@dataclass(frozen=True)
class ScoredRecommendation:
    """One candidate plus its versioned, normalized weighted score."""

    feature_vector: RecommendationFeatureVector
    scoring_version: str
    score: Decimal
    configured_weight_sum: Decimal
    components: tuple[RecommendationScoreComponent, ...]

    @property
    def candidate(self) -> RecommendationCandidate:
        return self.feature_vector.candidate


@dataclass(frozen=True)
class RankedRecommendation:
    """Final deterministic rank assigned by R5."""

    rank: int
    scored: ScoredRecommendation

    @property
    def candidate(self) -> RecommendationCandidate:
        return self.scored.candidate

    @property
    def score(self) -> Decimal:
        return self.scored.score

    @property
    def scoring_version(self) -> str:
        return self.scored.scoring_version

    @property
    def components(self) -> tuple[RecommendationScoreComponent, ...]:
        return self.scored.components


def _quantize(value):
    return Decimal(value).quantize(_SCORE_QUANTUM)


def _decimal_setting(value, *, name):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric.") from exc

    if not result.is_finite():
        raise ValueError(f"{name} must be finite.")

    return result


def _load_policy():
    raw = getattr(settings, "RECOMMENDATION_RANKING", None)
    if not isinstance(raw, dict):
        raise ValueError("RECOMMENDATION_RANKING must be a dictionary.")

    version = raw.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(
            "RECOMMENDATION_RANKING.version must be a non-empty string."
        )

    raw_weights = raw.get("weights")
    if not isinstance(raw_weights, dict):
        raise ValueError(
            "RECOMMENDATION_RANKING.weights must be a dictionary."
        )

    if set(raw_weights) != set(FEATURE_NAMES):
        missing = sorted(set(FEATURE_NAMES) - set(raw_weights))
        extra = sorted(set(raw_weights) - set(FEATURE_NAMES))
        raise ValueError(
            "RECOMMENDATION_RANKING.weights must define exactly the R4 "
            f"feature schema; missing={missing}, extra={extra}."
        )

    weights = {}
    for feature_name in FEATURE_NAMES:
        weight = _decimal_setting(
            raw_weights[feature_name],
            name=f"weight[{feature_name}]",
        )
        if weight < _ZERO:
            raise ValueError(
                f"weight[{feature_name}] must be greater than or equal to 0."
            )
        weights[feature_name] = weight

    if sum(weights.values(), _ZERO) <= _ZERO:
        raise ValueError("At least one recommendation weight must be positive.")

    default_top_n = raw.get("default_top_n")
    if isinstance(default_top_n, bool) or not isinstance(default_top_n, int):
        raise ValueError(
            "RECOMMENDATION_RANKING.default_top_n must be a positive integer."
        )
    if default_top_n <= 0:
        raise ValueError(
            "RECOMMENDATION_RANKING.default_top_n must be a positive integer."
        )

    return version.strip(), weights, default_top_n


def _validate_feature_vectors(feature_vectors):
    feature_vectors = tuple(feature_vectors)
    seen_keys = set()

    for vector in feature_vectors:
        if not isinstance(vector, RecommendationFeatureVector):
            raise TypeError(
                "feature_vectors must contain RecommendationFeatureVector "
                "instances only."
            )

        if vector.feature_version != FEATURE_VERSION:
            raise ValueError(
                "R5 only supports the current R4 feature version "
                f"{FEATURE_VERSION!r}."
            )

        key = vector.candidate.candidate_key
        if key in seen_keys:
            raise ValueError(
                "Recommendation feature candidate keys must be unique."
            )
        seen_keys.add(key)

        for feature_name in FEATURE_NAMES:
            value = getattr(vector, feature_name)
            if value is None:
                continue
            value = Decimal(value)
            if not value.is_finite() or not (_ZERO <= value <= _ONE):
                raise ValueError(
                    f"Feature {feature_name!r} must be in [0, 1] or None."
                )

    return feature_vectors


def score_recommendation_features(*, feature_vectors):
    """Score R4 feature vectors with normalized applicable weights.

    ``None`` features are not applicable and therefore do not enter the
    denominator.  A real value of 0 remains applicable and *does* enter the
    denominator.  This distinction lets heterogeneous lesson, assessment,
    and vocabulary candidates share one explainable score without inventing
    missing feature values.
    """

    feature_vectors = _validate_feature_vectors(feature_vectors)
    version, weights, _default_top_n = _load_policy()

    scored = []

    for vector in feature_vectors:
        applicable = []
        configured_weight_sum = _ZERO

        for feature_name in FEATURE_NAMES:
            value = getattr(vector, feature_name)
            weight = weights[feature_name]

            if value is None or weight == _ZERO:
                continue

            value = Decimal(value)
            applicable.append((feature_name, value, weight))
            configured_weight_sum += weight

        if configured_weight_sum <= _ZERO:
            raise ValueError(
                "Candidate has no applicable feature with a positive weight: "
                f"{vector.candidate.candidate_key}"
            )

        components = []
        weighted_sum = _ZERO

        with localcontext() as context:
            context.prec = 40

            for feature_name, value, weight in applicable:
                effective_weight_raw = weight / configured_weight_sum
                contribution_raw = value * effective_weight_raw
                weighted_sum += contribution_raw

                components.append(
                    RecommendationScoreComponent(
                        feature_name=feature_name,
                        value=_quantize(value),
                        configured_weight=_quantize(weight),
                        effective_weight=_quantize(effective_weight_raw),
                        contribution=_quantize(contribution_raw),
                    )
                )

        scored.append(
            ScoredRecommendation(
                feature_vector=vector,
                scoring_version=version,
                score=_quantize(weighted_sum),
                configured_weight_sum=_quantize(configured_weight_sum),
                components=tuple(components),
            )
        )

    return tuple(scored)


def _tie_break_key(scored):
    candidate = scored.candidate
    lesson_order = (
        candidate.lesson_display_order
        if candidate.lesson_display_order is not None
        else 2**31 - 1
    )
    lesson_id = candidate.lesson_id if candidate.lesson_id is not None else 0
    session_id = (
        candidate.assessment_session_id
        if candidate.assessment_session_id is not None
        else 0
    )

    return (
        -scored.score,
        _TIE_BREAK_TYPE_ORDER[candidate.candidate_type],
        lesson_order,
        lesson_id,
        session_id,
        candidate.candidate_key,
    )


def rank_recommendation_features(*, feature_vectors, limit=None):
    """Score and deterministically rank already-built R4 feature vectors."""

    version, _weights, default_top_n = _load_policy()
    del version  # score_recommendation_features loads/validates same policy.

    if limit is None:
        limit = default_top_n
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer.")

    scored = score_recommendation_features(feature_vectors=feature_vectors)
    ordered = sorted(scored, key=_tie_break_key)

    return tuple(
        RankedRecommendation(rank=index, scored=row)
        for index, row in enumerate(ordered[:limit], start=1)
    )


def rank_recommendations(*, user, candidates=None, as_of=None, limit=None):
    """Run R4 -> R5 and return deterministic Top-N recommendations.

    R4 remains the enforcement boundary for R3 eligibility when explicit
    candidates are supplied.  This function performs no writes.
    """

    feature_vectors = build_recommendation_features(
        user=user,
        candidates=candidates,
        as_of=as_of,
    )
    return rank_recommendation_features(
        feature_vectors=feature_vectors,
        limit=limit,
    )
