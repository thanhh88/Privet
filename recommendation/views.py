"""R9 authenticated recommendation API.

The API exposes the R2-R8 pipeline without leaking internal correctness data.
Creating an impression is a POST because it persists an R7 display log.
"""

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from recommendation.actions import (
    RecommendationActionError,
    RecommendationActivityKind,
    abandon_recommendation_assessment,
    complete_recommendation_activity,
    open_recommendation_activity,
)
from recommendation.logging import generate_logged_recommendations
from recommendation.models import RecommendationDecision
from recommendation.outcomes import (
    get_recommendation_outcome_state,
    record_recommendation_dismissed,
)


def _reply(payload, status=200):
    return JsonResponse(
        payload,
        status=status,
        json_dumps_params={"ensure_ascii": False},
    )


def _error(code, detail, status=400):
    return _reply({"error": code, "detail": detail}, status=status)


def _body_json(request):
    if request.content_type != "application/json" or len(request.body) > 16384:
        raise ValueError("Content-Type must be application/json and body <= 16KB.")
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Request body must contain valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON payload must be an object.")
    return payload


def _owned_decision(request, decision_id):
    return get_object_or_404(
        RecommendationDecision.objects.select_related("batch"),
        pk=decision_id,
        batch__user=request.user,
    )


def _state_json(decision):
    state = get_recommendation_outcome_state(decision=decision)
    return {
        "status": state.status.value,
        "accepted_at": state.accepted_at.isoformat() if state.accepted_at else None,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "ended_at": state.ended_at.isoformat() if state.ended_at else None,
    }


def _decision_json(decision):
    return {
        "decision_id": decision.pk,
        "candidate_key": decision.candidate_key,
        "candidate_type": decision.candidate_type,
        "rank": decision.rank,
        "score": format(decision.score, "f"),
        "headline": decision.headline,
        "candidate": decision.candidate_snapshot,
        "features": decision.feature_snapshot,
        "score_components": decision.score_components,
        "reasons": decision.explanation_reasons,
        "outcome": _state_json(decision),
    }


def _redirect_url(result):
    if result.activity_kind == RecommendationActivityKind.LESSON:
        base = reverse("UI:recommended_lesson", args=[result.lesson_id])
        return f"{base}?decision={result.decision_id}"

    if result.activity_kind == RecommendationActivityKind.ASSESSMENT:
        base = reverse("UI:assessment_session", args=[result.assessment_session_id])
        return f"{base}?decision={result.decision_id}"

    if result.activity_kind == RecommendationActivityKind.VOCABULARY:
        base = reverse("UI:vocabulary_review")
        return f"{base}?decision={result.decision_id}"

    raise ValueError("Unsupported recommendation activity kind.")


@login_required
@require_POST
def create_impression_view(request):
    try:
        payload = _body_json(request)
    except ValueError as exc:
        return _error("invalid_payload", str(exc))

    allowed = {"request_id", "surface", "limit"}
    extra = set(payload) - allowed
    if extra:
        return _error("unexpected_fields", ", ".join(sorted(extra)))

    request_id = payload.get("request_id")
    surface = payload.get("surface", "api")
    limit = payload.get("limit")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= 10)
    ):
        return _error("invalid_limit", "limit must be an integer from 1 to 10.")

    try:
        result = generate_logged_recommendations(
            user=request.user,
            request_id=request_id,
            surface=surface,
            limit=limit,
        )
    except ValueError as exc:
        if str(exc).startswith("No eligible recommendations were produced"):
            return _reply({"batch": None, "recommendations": []})
        return _error("recommendation_conflict", str(exc), status=409)

    decisions = list(result.batch.decisions.order_by("rank", "decision_id"))
    return _reply(
        {
            "batch": {
                "batch_id": result.batch.pk,
                "request_id": result.batch.request_id,
                "surface": result.batch.surface,
                "context_at": result.batch.context_at.isoformat(),
                "feature_version": result.batch.feature_version,
                "scoring_version": result.batch.scoring_version,
                "explanation_version": result.batch.explanation_version,
                "item_count": result.batch.item_count,
            },
            "recommendations": [_decision_json(row) for row in decisions],
        },
        status=201,
    )


@login_required
@require_GET
def decision_state_view(request, decision_id):
    decision = _owned_decision(request, decision_id)
    return _reply(
        {
            "decision_id": decision.pk,
            "outcome": _state_json(decision),
        }
    )


@login_required
@require_POST
def open_decision_view(request, decision_id):
    decision = _owned_decision(request, decision_id)
    try:
        payload = _body_json(request)
        if set(payload) != {"request_id"}:
            raise ValueError("Payload must contain exactly request_id.")
        result = open_recommendation_activity(
            user=request.user,
            decision=decision,
            request_id=payload["request_id"],
        )
    except (ValueError, RecommendationActionError) as exc:
        return _error("recommendation_action_conflict", str(exc), status=409)

    return _reply(
        {
            "decision_id": decision.pk,
            "candidate_type": result.candidate_type,
            "outcome": result.outcome_status.value,
            "activity": {
                "kind": result.activity_kind.value,
                "lesson_id": result.lesson_id,
                "lesson_progress_id": result.lesson_progress_id,
                "assessment_session_id": result.assessment_session_id,
            },
            "redirect_url": _redirect_url(result),
        }
    )


@login_required
@require_POST
def dismiss_decision_view(request, decision_id):
    decision = _owned_decision(request, decision_id)
    try:
        payload = _body_json(request)
        if set(payload) != {"request_id"}:
            raise ValueError("Payload must contain exactly request_id.")
        event = record_recommendation_dismissed(
            user=request.user,
            decision=decision,
            request_id=payload["request_id"],
        )
    except ValueError as exc:
        return _error("recommendation_action_conflict", str(exc), status=409)

    return _reply(
        {
            "decision_id": decision.pk,
            "event_id": event.pk,
            "outcome": _state_json(decision),
        }
    )


@login_required
@require_POST
def complete_decision_view(request, decision_id):
    decision = _owned_decision(request, decision_id)
    try:
        payload = _body_json(request)
        allowed = {"request_id", "vocabulary_review_log_ids"}
        extra = set(payload) - allowed
        if extra or "request_id" not in payload:
            raise ValueError(
                "Payload requires request_id and may include vocabulary_review_log_ids."
            )
        log_ids = payload.get("vocabulary_review_log_ids", [])
        if not isinstance(log_ids, list) or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in log_ids
        ):
            raise ValueError("vocabulary_review_log_ids must be a list of positive integers.")

        result = complete_recommendation_activity(
            user=request.user,
            decision=decision,
            request_id=payload["request_id"],
            vocabulary_review_log_ids=tuple(log_ids),
        )
    except (ValueError, RecommendationActionError) as exc:
        return _error("recommendation_action_conflict", str(exc), status=409)

    return _reply(
        {
            "decision_id": decision.pk,
            "candidate_type": result.candidate_type,
            "outcome": result.outcome_status.value,
        }
    )


@login_required
@require_POST
def abandon_decision_view(request, decision_id):
    decision = _owned_decision(request, decision_id)
    try:
        payload = _body_json(request)
        if set(payload) != {"request_id"}:
            raise ValueError("Payload must contain exactly request_id.")
        result = abandon_recommendation_assessment(
            user=request.user,
            decision=decision,
            request_id=payload["request_id"],
        )
    except (ValueError, RecommendationActionError) as exc:
        return _error("recommendation_action_conflict", str(exc), status=409)

    return _reply(
        {
            "decision_id": decision.pk,
            "candidate_type": result.candidate_type,
            "outcome": result.outcome_status.value,
        }
    )
