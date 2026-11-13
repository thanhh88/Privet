"""R9 orchestration between recommendation lifecycle and real activities.

The recommendation engine owns the decision.  Existing learning/vocabulary
services still own the underlying activity.  R9 coordinates both sides so a
UI/API action cannot start an activity without recording the corresponding
R8 lifecycle event (or vice versa).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from django.db import transaction
from django.utils import timezone

from curriculum.models import LessonProgress
from learning_engine.models import AssessmentSession
from learning_engine.services import (
    LearningServiceError,
    abandon_session,
    complete_lesson,
    complete_session,
    start_or_resume_adaptive_assessment,
    start_or_resume_lesson,
)
from recommendation.candidates import RecommendationCandidateType
from recommendation.models import (
    RecommendationDecision,
    RecommendationOutcomeEvent,
)
from recommendation.outcomes import (
    RecommendationOutcomeStatus,
    get_recommendation_outcome_state,
    record_recommendation_accepted,
    record_recommendation_activity_abandoned,
    record_recommendation_activity_completed,
    record_recommendation_activity_started,
)
from vocabulary.models import VocabularyReviewLog


class RecommendationActionError(ValueError):
    """Raised when an R9 recommendation action cannot be executed."""


class RecommendationActivityKind(str, Enum):
    LESSON = "lesson"
    ASSESSMENT = "assessment"
    VOCABULARY = "vocabulary"


@dataclass(frozen=True)
class RecommendationActivityResult:
    decision_id: int
    candidate_type: str
    activity_kind: RecommendationActivityKind
    outcome_status: RecommendationOutcomeStatus
    lesson_id: int | None = None
    lesson_progress_id: int | None = None
    assessment_session_id: int | None = None


def _validate_request_id(request_id):
    if not isinstance(request_id, str) or not request_id.strip():
        raise RecommendationActionError("request_id must be a non-empty string.")
    request_id = request_id.strip()
    # R8 request ids allow 128 chars.  R9 appends a deterministic phase suffix.
    if len(request_id) > 96:
        raise RecommendationActionError("request_id must contain at most 96 characters.")
    return request_id


def _validate_occurred_at(occurred_at):
    if occurred_at is None:
        return timezone.now()
    if not isinstance(occurred_at, datetime) or timezone.is_naive(occurred_at):
        raise RecommendationActionError("occurred_at must be timezone-aware.")
    return occurred_at


def _owned_decision(*, user, decision):
    if user.pk is None:
        raise RecommendationActionError("User must be saved.")
    if decision.pk is None:
        raise RecommendationActionError("RecommendationDecision must be saved.")
    try:
        return (
            RecommendationDecision.objects
            .select_for_update()
            .select_related("batch")
            .get(pk=decision.pk, batch__user=user)
        )
    except RecommendationDecision.DoesNotExist as exc:
        raise RecommendationActionError(
            "Recommendation decision does not belong to this user."
        ) from exc


def _candidate_type(decision):
    try:
        return RecommendationCandidateType(decision.candidate_type)
    except ValueError as exc:
        raise RecommendationActionError(
            "Recommendation decision has an unsupported candidate_type."
        ) from exc


def _phase_request_id(base, phase):
    return f"{base}:{phase}"


def _started_event(decision):
    return decision.outcome_events.filter(
        event_type=RecommendationOutcomeEvent.EventType.ACTIVITY_STARTED,
    ).first()


def _result_from_started(decision, started):
    candidate_type = _candidate_type(decision)
    snapshot = started.activity_snapshot or {}

    if candidate_type in {
        RecommendationCandidateType.START_LESSON,
        RecommendationCandidateType.CONTINUE_LESSON,
    }:
        return RecommendationActivityResult(
            decision_id=decision.pk,
            candidate_type=candidate_type.value,
            activity_kind=RecommendationActivityKind.LESSON,
            outcome_status=RecommendationOutcomeStatus.STARTED,
            lesson_id=snapshot.get("lesson_id"),
            lesson_progress_id=snapshot.get("lesson_progress_id"),
        )

    if candidate_type in {
        RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT,
        RecommendationCandidateType.RESUME_ASSESSMENT,
    }:
        return RecommendationActivityResult(
            decision_id=decision.pk,
            candidate_type=candidate_type.value,
            activity_kind=RecommendationActivityKind.ASSESSMENT,
            outcome_status=RecommendationOutcomeStatus.STARTED,
            assessment_session_id=snapshot.get("assessment_session_id"),
        )

    if candidate_type == RecommendationCandidateType.VOCABULARY_REVIEW:
        return RecommendationActivityResult(
            decision_id=decision.pk,
            candidate_type=candidate_type.value,
            activity_kind=RecommendationActivityKind.VOCABULARY,
            outcome_status=RecommendationOutcomeStatus.STARTED,
        )

    raise RecommendationActionError("Unsupported recommendation candidate type.")


def _terminal_result(decision, state):
    started = _started_event(decision)
    if started is None:
        raise RecommendationActionError("Terminal recommendation has no started activity.")
    base = _result_from_started(decision, started)
    return RecommendationActivityResult(
        decision_id=base.decision_id,
        candidate_type=base.candidate_type,
        activity_kind=base.activity_kind,
        outcome_status=state.status,
        lesson_id=base.lesson_id,
        lesson_progress_id=base.lesson_progress_id,
        assessment_session_id=base.assessment_session_id,
    )


@transaction.atomic
def open_recommendation_activity(
    *,
    user,
    decision,
    request_id,
    occurred_at=None,
):
    """Accept and start one displayed recommendation atomically.

    Repeated calls after the activity has started return the existing activity
    target and never create a second lesson progress/assessment/session event.
    """

    request_id = _validate_request_id(request_id)
    occurred_at = _validate_occurred_at(occurred_at)
    decision = _owned_decision(user=user, decision=decision)
    state = get_recommendation_outcome_state(decision=decision)

    if state.status == RecommendationOutcomeStatus.DISMISSED:
        raise RecommendationActionError("Dismissed recommendation cannot be opened.")
    if state.status in {
        RecommendationOutcomeStatus.COMPLETED,
        RecommendationOutcomeStatus.ABANDONED,
    }:
        return _terminal_result(decision, state)
    if state.status == RecommendationOutcomeStatus.STARTED:
        started = _started_event(decision)
        return _result_from_started(decision, started)

    if state.status == RecommendationOutcomeStatus.SHOWN:
        record_recommendation_accepted(
            user=user,
            decision=decision,
            request_id=_phase_request_id(request_id, "accepted"),
            occurred_at=occurred_at,
        )

    candidate_type = _candidate_type(decision)
    snapshot = decision.candidate_snapshot or {}

    try:
        if candidate_type in {
            RecommendationCandidateType.START_LESSON,
            RecommendationCandidateType.CONTINUE_LESSON,
        }:
            lesson_id = snapshot.get("lesson_id")
            if not isinstance(lesson_id, int) or isinstance(lesson_id, bool):
                raise RecommendationActionError(
                    "Lesson recommendation snapshot has invalid lesson_id."
                )

            result = start_or_resume_lesson(user=user, lesson_id=lesson_id)
            progress = LessonProgress.objects.get(
                pk=result.lesson_progress_id,
                user=user,
                lesson_id=lesson_id,
            )
            record_recommendation_activity_started(
                user=user,
                decision=decision,
                request_id=_phase_request_id(request_id, "started"),
                occurred_at=occurred_at,
                lesson_progress=progress,
            )
            return RecommendationActivityResult(
                decision_id=decision.pk,
                candidate_type=candidate_type.value,
                activity_kind=RecommendationActivityKind.LESSON,
                outcome_status=RecommendationOutcomeStatus.STARTED,
                lesson_id=lesson_id,
                lesson_progress_id=progress.pk,
            )

        if candidate_type == RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT:
            result = start_or_resume_adaptive_assessment(user=user)
            session = AssessmentSession.objects.get(
                pk=result.session_id,
                user=user,
                status=AssessmentSession.Status.ACTIVE,
            )
            record_recommendation_activity_started(
                user=user,
                decision=decision,
                request_id=_phase_request_id(request_id, "started"),
                occurred_at=occurred_at,
                assessment_session=session,
            )
            return RecommendationActivityResult(
                decision_id=decision.pk,
                candidate_type=candidate_type.value,
                activity_kind=RecommendationActivityKind.ASSESSMENT,
                outcome_status=RecommendationOutcomeStatus.STARTED,
                assessment_session_id=session.pk,
            )

        if candidate_type == RecommendationCandidateType.RESUME_ASSESSMENT:
            session_id = snapshot.get("assessment_session_id")
            try:
                session = AssessmentSession.objects.get(
                    pk=session_id,
                    user=user,
                    status=AssessmentSession.Status.ACTIVE,
                )
            except AssessmentSession.DoesNotExist as exc:
                raise RecommendationActionError(
                    "Recommended assessment is no longer active."
                ) from exc

            record_recommendation_activity_started(
                user=user,
                decision=decision,
                request_id=_phase_request_id(request_id, "started"),
                occurred_at=occurred_at,
                assessment_session=session,
            )
            return RecommendationActivityResult(
                decision_id=decision.pk,
                candidate_type=candidate_type.value,
                activity_kind=RecommendationActivityKind.ASSESSMENT,
                outcome_status=RecommendationOutcomeStatus.STARTED,
                assessment_session_id=session.pk,
            )

        if candidate_type == RecommendationCandidateType.VOCABULARY_REVIEW:
            record_recommendation_activity_started(
                user=user,
                decision=decision,
                request_id=_phase_request_id(request_id, "started"),
                occurred_at=occurred_at,
            )
            return RecommendationActivityResult(
                decision_id=decision.pk,
                candidate_type=candidate_type.value,
                activity_kind=RecommendationActivityKind.VOCABULARY,
                outcome_status=RecommendationOutcomeStatus.STARTED,
            )

    except LearningServiceError as exc:
        raise RecommendationActionError(str(exc)) from exc

    raise RecommendationActionError("Unsupported recommendation candidate type.")


def _existing_completed_result(decision):
    state = get_recommendation_outcome_state(decision=decision)
    if state.status != RecommendationOutcomeStatus.COMPLETED:
        return None
    return _terminal_result(decision, state)


@transaction.atomic
def complete_recommendation_activity(
    *,
    user,
    decision,
    request_id,
    occurred_at=None,
    vocabulary_review_log_ids=(),
):
    """Complete the real activity and freeze the corresponding R8 outcome."""

    request_id = _validate_request_id(request_id)
    occurred_at = _validate_occurred_at(occurred_at)
    decision = _owned_decision(user=user, decision=decision)

    existing = _existing_completed_result(decision)
    if existing is not None:
        return existing

    state = get_recommendation_outcome_state(decision=decision)
    if state.status != RecommendationOutcomeStatus.STARTED:
        raise RecommendationActionError(
            "Recommendation activity must be started before completion."
        )

    started = _started_event(decision)
    candidate_type = _candidate_type(decision)

    try:
        if candidate_type in {
            RecommendationCandidateType.START_LESSON,
            RecommendationCandidateType.CONTINUE_LESSON,
        }:
            lesson_id = started.activity_snapshot.get("lesson_id")
            complete_lesson(user=user, lesson_id=lesson_id)
            progress = LessonProgress.objects.get(
                pk=started.activity_snapshot.get("lesson_progress_id"),
                user=user,
            )
            record_recommendation_activity_completed(
                user=user,
                decision=decision,
                request_id=_phase_request_id(request_id, "completed"),
                occurred_at=occurred_at,
                lesson_progress=progress,
            )
            return RecommendationActivityResult(
                decision_id=decision.pk,
                candidate_type=candidate_type.value,
                activity_kind=RecommendationActivityKind.LESSON,
                outcome_status=RecommendationOutcomeStatus.COMPLETED,
                lesson_id=progress.lesson_id,
                lesson_progress_id=progress.pk,
            )

        if candidate_type in {
            RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT,
            RecommendationCandidateType.RESUME_ASSESSMENT,
        }:
            session_id = started.activity_snapshot.get("assessment_session_id")
            complete_session(user=user, session_id=session_id)
            session = AssessmentSession.objects.get(pk=session_id, user=user)
            record_recommendation_activity_completed(
                user=user,
                decision=decision,
                request_id=_phase_request_id(request_id, "completed"),
                occurred_at=occurred_at,
                assessment_session=session,
            )
            return RecommendationActivityResult(
                decision_id=decision.pk,
                candidate_type=candidate_type.value,
                activity_kind=RecommendationActivityKind.ASSESSMENT,
                outcome_status=RecommendationOutcomeStatus.COMPLETED,
                assessment_session_id=session.pk,
            )

        if candidate_type == RecommendationCandidateType.VOCABULARY_REVIEW:
            ids = tuple(vocabulary_review_log_ids)
            if not ids:
                raise RecommendationActionError(
                    "Vocabulary completion requires review log ids."
                )
            if len(ids) != len(set(ids)):
                raise RecommendationActionError(
                    "Vocabulary review log ids must not contain duplicates."
                )
            logs = tuple(
                VocabularyReviewLog.objects.filter(
                    pk__in=ids,
                    user=user,
                ).order_by("reviewed_at", "pk")
            )
            if len(logs) != len(ids):
                raise RecommendationActionError(
                    "Vocabulary review log ids are invalid or not owned by this user."
                )
            record_recommendation_activity_completed(
                user=user,
                decision=decision,
                request_id=_phase_request_id(request_id, "completed"),
                occurred_at=occurred_at,
                vocabulary_review_logs=logs,
            )
            return RecommendationActivityResult(
                decision_id=decision.pk,
                candidate_type=candidate_type.value,
                activity_kind=RecommendationActivityKind.VOCABULARY,
                outcome_status=RecommendationOutcomeStatus.COMPLETED,
            )

    except LearningServiceError as exc:
        raise RecommendationActionError(str(exc)) from exc

    raise RecommendationActionError("Unsupported recommendation candidate type.")


@transaction.atomic
def abandon_recommendation_assessment(
    *,
    user,
    decision,
    request_id,
    occurred_at=None,
):
    """Abandon a started assessment and record the matching R8 event."""

    request_id = _validate_request_id(request_id)
    occurred_at = _validate_occurred_at(occurred_at)
    decision = _owned_decision(user=user, decision=decision)
    state = get_recommendation_outcome_state(decision=decision)

    if state.status == RecommendationOutcomeStatus.ABANDONED:
        return _terminal_result(decision, state)
    if state.status != RecommendationOutcomeStatus.STARTED:
        raise RecommendationActionError(
            "Only a started recommendation assessment can be abandoned."
        )

    candidate_type = _candidate_type(decision)
    if candidate_type not in {
        RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT,
        RecommendationCandidateType.RESUME_ASSESSMENT,
    }:
        raise RecommendationActionError(
            "Only assessment recommendations can be abandoned."
        )

    started = _started_event(decision)
    session_id = started.activity_snapshot.get("assessment_session_id")

    try:
        abandon_session(user=user, session_id=session_id)
    except LearningServiceError as exc:
        raise RecommendationActionError(str(exc)) from exc

    session = AssessmentSession.objects.get(pk=session_id, user=user)
    record_recommendation_activity_abandoned(
        user=user,
        decision=decision,
        request_id=_phase_request_id(request_id, "abandoned"),
        occurred_at=occurred_at,
        assessment_session=session,
    )

    return RecommendationActivityResult(
        decision_id=decision.pk,
        candidate_type=candidate_type.value,
        activity_kind=RecommendationActivityKind.ASSESSMENT,
        outcome_status=RecommendationOutcomeStatus.ABANDONED,
        assessment_session_id=session.pk,
    )
