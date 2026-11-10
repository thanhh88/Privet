"""Persistent, append-only logging for recommendation decisions.

R7 records what R2-R6 decided to surface.  It deliberately does not record
acceptance, start, completion, or learning outcomes; those belong to R8.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json

from django.db import IntegrityError, transaction
from django.utils import timezone

from curriculum.models import LessonProgress
from learning_engine.models import AssessmentSession
from recommendation.candidates import RecommendationCandidateType
from recommendation.explanations import (
    ExplainedRecommendation,
    explain_recommendations,
)
from recommendation.features import FEATURE_VERSION
from recommendation.models import RecommendationBatch, RecommendationDecision
from recommendation.ranking import FEATURE_NAMES


@dataclass(frozen=True)
class RecommendationLoggingResult:
    """R7 pipeline result used by later API/UI integration."""

    batch: RecommendationBatch
    recommendations: tuple[ExplainedRecommendation, ...]


def _decimal_text(value):
    if value is None:
        return None
    return format(Decimal(value), "f")


def _datetime_text(value):
    if value is None:
        return None
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise ValueError("Recommendation snapshot datetimes must be timezone-aware.")
    return value.isoformat()


def _validate_user(user):
    if user.pk is None:
        raise ValueError("User must be saved before recommendations can be logged.")


def _validate_request_id(request_id):
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id must be a non-empty string.")
    request_id = request_id.strip()
    if len(request_id) > 128:
        raise ValueError("request_id must contain at most 128 characters.")
    return request_id


def _validate_surface(surface):
    if not isinstance(surface, str) or not surface.strip():
        raise ValueError("surface must be a non-empty string.")
    surface = surface.strip()
    if len(surface) > 64:
        raise ValueError("surface must contain at most 64 characters.")
    return surface


def _validate_context_at(context_at):
    if context_at is None:
        return timezone.now()
    if not isinstance(context_at, datetime):
        raise ValueError("context_at must be a datetime.")
    if timezone.is_naive(context_at):
        raise ValueError("context_at must be timezone-aware.")
    return context_at


def _candidate_snapshot(candidate):
    candidate_type = candidate.candidate_type
    if isinstance(candidate_type, RecommendationCandidateType):
        candidate_type = candidate_type.value
    else:
        candidate_type = str(candidate_type)

    return {
        "candidate_key": candidate.candidate_key,
        "candidate_type": candidate_type,
        "lesson_id": candidate.lesson_id,
        "lesson_progress_id": candidate.lesson_progress_id,
        "lesson_title": candidate.lesson_title,
        "lesson_type": candidate.lesson_type,
        "lesson_display_order": candidate.lesson_display_order,
        "assessment_session_id": candidate.assessment_session_id,
        "assessment_mode": candidate.assessment_mode,
        "skill_ids": list(candidate.skill_ids),
        "vocabulary_due_count": candidate.vocabulary_due_count,
        "vocabulary_oldest_due_at": _datetime_text(
            candidate.vocabulary_oldest_due_at
        ),
    }


def _feature_snapshot(explained):
    vector = explained.ranked.scored.feature_vector
    snapshot = {
        "feature_version": vector.feature_version,
        "skill_scope_ids": list(vector.skill_scope_ids),
    }
    for feature_name in FEATURE_NAMES:
        snapshot[feature_name] = _decimal_text(getattr(vector, feature_name))
    return snapshot


def _component_snapshot(explained):
    return [
        {
            "feature_name": component.feature_name,
            "value": _decimal_text(component.value),
            "configured_weight": _decimal_text(component.configured_weight),
            "effective_weight": _decimal_text(component.effective_weight),
            "contribution": _decimal_text(component.contribution),
        }
        for component in explained.ranked.components
    ]


def _reason_snapshot(explained):
    return [
        {
            "code": reason.code,
            "message": reason.message,
            "feature_name": reason.feature_name,
            "value": _decimal_text(reason.value),
            "contribution": _decimal_text(reason.contribution),
        }
        for reason in explained.reasons
    ]


def _validate_candidate_ownership(*, user, explained):
    candidate = explained.candidate

    if candidate.lesson_progress_id is not None:
        owned = LessonProgress.objects.filter(
            pk=candidate.lesson_progress_id,
            user=user,
        ).exists()
        if not owned:
            raise ValueError(
                "Recommendation candidate lesson_progress does not belong "
                "to the logging user."
            )

    if candidate.assessment_session_id is not None:
        owned = AssessmentSession.objects.filter(
            pk=candidate.assessment_session_id,
            user=user,
        ).exists()
        if not owned:
            raise ValueError(
                "Recommendation candidate assessment_session does not belong "
                "to the logging user."
            )


def _validated_rows(*, user, explained_recommendations):
    rows = tuple(explained_recommendations)
    if not rows:
        raise ValueError("R7 only logs non-empty displayed recommendation batches.")

    candidate_keys = set()
    ranks = set()
    feature_versions = set()
    scoring_versions = set()
    explanation_versions = set()

    for row in rows:
        if not isinstance(row, ExplainedRecommendation):
            raise TypeError(
                "explained_recommendations must contain "
                "ExplainedRecommendation instances only."
            )

        if row.candidate.candidate_key in candidate_keys:
            raise ValueError("Recommendation candidate keys must be unique.")
        candidate_keys.add(row.candidate.candidate_key)

        if row.rank in ranks:
            raise ValueError("Recommendation ranks must be unique.")
        ranks.add(row.rank)

        if isinstance(row.rank, bool) or not isinstance(row.rank, int) or row.rank <= 0:
            raise ValueError("Recommendation ranks must be positive integers.")

        score = Decimal(row.score)
        if not score.is_finite() or not (Decimal("0") <= score <= Decimal("1")):
            raise ValueError("Recommendation scores must be finite and in [0, 1].")

        feature_versions.add(row.ranked.scored.feature_vector.feature_version)
        scoring_versions.add(row.scoring_version)
        explanation_versions.add(row.explanation_version)

        _validate_candidate_ownership(user=user, explained=row)

    expected_ranks = list(range(1, len(rows) + 1))
    actual_ranks = sorted(ranks)
    if actual_ranks != expected_ranks:
        raise ValueError("Recommendation ranks must be contiguous starting at 1.")

    if feature_versions != {FEATURE_VERSION}:
        raise ValueError(
            f"R7 only supports current feature version {FEATURE_VERSION!r}."
        )
    if len(scoring_versions) != 1:
        raise ValueError("All logged recommendations must share one scoring version.")
    if len(explanation_versions) != 1:
        raise ValueError(
            "All logged recommendations must share one explanation version."
        )

    return tuple(sorted(rows, key=lambda row: row.rank))


def _decision_payload(explained):
    candidate = explained.candidate
    candidate_type = candidate.candidate_type
    if isinstance(candidate_type, RecommendationCandidateType):
        candidate_type = candidate_type.value
    else:
        candidate_type = str(candidate_type)

    return {
        "candidate_key": candidate.candidate_key,
        "candidate_type": candidate_type,
        "rank": explained.rank,
        "score": _decimal_text(explained.score),
        "headline": explained.headline,
        "candidate_snapshot": _candidate_snapshot(candidate),
        "feature_snapshot": _feature_snapshot(explained),
        "score_components": _component_snapshot(explained),
        "explanation_reasons": _reason_snapshot(explained),
    }


def _payload_hash(*, surface, rows):
    payload = {
        "surface": surface,
        "feature_version": rows[0].ranked.scored.feature_vector.feature_version,
        "scoring_version": rows[0].scoring_version,
        "explanation_version": rows[0].explanation_version,
        "decisions": [_decision_payload(row) for row in rows],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _existing_or_conflict(*, user, request_id, payload_hash):
    existing = RecommendationBatch.objects.filter(
        user=user,
        request_id=request_id,
    ).first()
    if existing is None:
        return None
    if existing.payload_hash != payload_hash:
        raise ValueError(
            "request_id was already used for a different recommendation payload."
        )
    return existing


def log_explained_recommendations(
    *,
    user,
    explained_recommendations,
    request_id,
    surface="recommendation",
    context_at=None,
):
    """Persist one immutable R7 recommendation-impression batch.

    The same ``user + request_id`` can be safely retried with the same
    decisions and returns the original batch.  Reusing that key for a
    different payload is rejected.
    """

    _validate_user(user)
    request_id = _validate_request_id(request_id)
    surface = _validate_surface(surface)
    context_at = _validate_context_at(context_at)
    rows = _validated_rows(
        user=user,
        explained_recommendations=explained_recommendations,
    )
    payload_hash = _payload_hash(surface=surface, rows=rows)

    existing = _existing_or_conflict(
        user=user,
        request_id=request_id,
        payload_hash=payload_hash,
    )
    if existing is not None:
        return existing

    feature_version = rows[0].ranked.scored.feature_vector.feature_version
    scoring_version = rows[0].scoring_version
    explanation_version = rows[0].explanation_version

    try:
        with transaction.atomic():
            batch = RecommendationBatch.objects.create(
                user=user,
                request_id=request_id,
                surface=surface,
                feature_version=feature_version,
                scoring_version=scoring_version,
                explanation_version=explanation_version,
                context_at=context_at,
                item_count=len(rows),
                payload_hash=payload_hash,
            )

            for row in rows:
                payload = _decision_payload(row)
                RecommendationDecision.objects.create(
                    batch=batch,
                    candidate_key=payload["candidate_key"],
                    candidate_type=payload["candidate_type"],
                    rank=payload["rank"],
                    score=Decimal(payload["score"]),
                    headline=payload["headline"],
                    candidate_snapshot=payload["candidate_snapshot"],
                    feature_snapshot=payload["feature_snapshot"],
                    score_components=payload["score_components"],
                    explanation_reasons=payload["explanation_reasons"],
                )

            return batch
    except IntegrityError:
        # A concurrent retry can win the unique(user, request_id) race.
        existing = _existing_or_conflict(
            user=user,
            request_id=request_id,
            payload_hash=payload_hash,
        )
        if existing is not None:
            return existing
        raise


def generate_logged_recommendations(
    *,
    user,
    request_id,
    surface="recommendation",
    candidates=None,
    as_of=None,
    limit=None,
    max_reasons=None,
):
    """Run R2-R6 and persist the displayed Top-N as one R7 batch."""

    context_at = _validate_context_at(as_of)
    recommendations = explain_recommendations(
        user=user,
        candidates=candidates,
        as_of=context_at,
        limit=limit,
        max_reasons=max_reasons,
    )

    if not recommendations:
        raise ValueError(
            "No eligible recommendations were produced; there is no displayed "
            "batch to log."
        )

    batch = log_explained_recommendations(
        user=user,
        explained_recommendations=recommendations,
        request_id=request_id,
        surface=surface,
        context_at=context_at,
    )

    return RecommendationLoggingResult(
        batch=batch,
        recommendations=tuple(recommendations),
    )
