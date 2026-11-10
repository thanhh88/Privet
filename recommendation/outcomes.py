"""R8 recommendation outcome tracking.

R7 records the immutable recommendation impression.  R8 records the learner's
response as an append-only lifecycle:

    accepted -> activity_started -> activity_completed
                                  -> activity_abandoned

or an explicit ``dismissed`` before acceptance.

A recommendation that receives no R8 event remains merely *shown*.  True
"ignored" behaviour is therefore derived analytically from an impression with
no response inside a chosen observation window; it is not fabricated as a
server event.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from curriculum.models import LessonProgress
from learning_engine.assessment_results import build_completed_assessment_result
from learning_engine.mastery import ensure_skill_mastery_current
from learning_engine.models import AssessmentSession
from recommendation.candidates import RecommendationCandidateType
from recommendation.models import (
    RecommendationDecision,
    RecommendationOutcomeEvent,
)
from vocabulary.models import VocabularyReviewLog


OUTCOME_VERSION = str(settings.RECOMMENDATION_OUTCOME["version"])
_OUTCOME_QUANTUM = Decimal("0.00001")
_RATE_QUANTUM = Decimal("0.0000")


class RecommendationOutcomeStatus(str, Enum):
    SHOWN = "shown"
    ACCEPTED = "accepted"
    STARTED = "started"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    DISMISSED = "dismissed"


@dataclass(frozen=True)
class RecommendationOutcomeState:
    decision_id: int
    status: RecommendationOutcomeStatus
    accepted_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None


def _decimal_text(value):
    if value is None:
        return None
    return format(Decimal(value), "f")


def _datetime_text(value):
    if value is None:
        return None
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise ValueError("Outcome snapshot datetimes must be timezone-aware.")
    return value.isoformat()


def _validate_user(user):
    if user.pk is None:
        raise ValueError("User must be saved before recommendation outcomes can be recorded.")


def _validate_request_id(request_id):
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id must be a non-empty string.")
    request_id = request_id.strip()
    if len(request_id) > 128:
        raise ValueError("request_id must contain at most 128 characters.")
    return request_id


def _validate_occurred_at(occurred_at):
    if occurred_at is None:
        return timezone.now()
    if not isinstance(occurred_at, datetime):
        raise ValueError("occurred_at must be a datetime.")
    if timezone.is_naive(occurred_at):
        raise ValueError("occurred_at must be timezone-aware.")
    return occurred_at


def _lock_decision(*, decision, user):
    if decision.pk is None:
        raise ValueError("RecommendationDecision must be saved before outcomes are recorded.")

    locked = (
        RecommendationDecision.objects
        .select_for_update()
        .select_related("batch")
        .get(pk=decision.pk)
    )
    if locked.batch.user_id != user.pk:
        raise ValueError("Recommendation decision does not belong to this user.")
    return locked


def _candidate_type(decision):
    try:
        return RecommendationCandidateType(decision.candidate_type)
    except ValueError as exc:
        raise ValueError("Recommendation decision has an unsupported candidate_type.") from exc


def _feature_skill_ids(decision):
    raw = decision.feature_snapshot.get("skill_scope_ids", [])
    if not isinstance(raw, list):
        raise ValueError("Recommendation feature snapshot has invalid skill_scope_ids.")

    values = []
    seen = set()
    for value in raw:
        if isinstance(value, bool):
            raise ValueError("Recommendation feature snapshot contains invalid skill ids.")
        try:
            skill_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Recommendation feature snapshot contains invalid skill ids."
            ) from exc
        if skill_id <= 0:
            raise ValueError("Recommendation feature snapshot contains invalid skill ids.")
        if skill_id not in seen:
            seen.add(skill_id)
            values.append(skill_id)
    return tuple(sorted(values))


def _mastery_snapshot(*, user, decision, as_of):
    rows = []
    for skill_id in _feature_skill_ids(decision):
        state = ensure_skill_mastery_current(
            user=user,
            skill_id=skill_id,
            as_of=as_of,
        )
        rows.append(
            {
                "skill_id": skill_id,
                "mastery_score": _decimal_text(state.mastery_score),
                "observation_count": state.observation_count,
                "correct_attempts": state.correct_attempts,
                "recent_accuracy": _decimal_text(state.recent_accuracy),
                "historical_accuracy": _decimal_text(state.historical_accuracy),
                "consecutive_errors": state.consecutive_errors,
                "algorithm_version": state.algorithm_version,
                "last_evidence_at": _datetime_text(state.last_evidence_at),
            }
        )
    return rows


def _learning_gain(*, before, after):
    before_by_skill = {row["skill_id"]: row for row in before}
    after_by_skill = {row["skill_id"]: row for row in after}

    if set(before_by_skill) != set(after_by_skill):
        raise ValueError("Mastery skill scope changed during recommendation outcome tracking.")

    rows = []
    for skill_id in sorted(before_by_skill):
        before_row = before_by_skill[skill_id]
        after_row = after_by_skill[skill_id]
        before_score = before_row["mastery_score"]
        after_score = after_row["mastery_score"]

        if before_score is None or after_score is None:
            delta = None
        else:
            delta = (
                Decimal(after_score) - Decimal(before_score)
            ).quantize(_OUTCOME_QUANTUM)
            delta = _decimal_text(delta)

        rows.append(
            {
                "skill_id": skill_id,
                "mastery_before": before_score,
                "mastery_after": after_score,
                "mastery_delta": delta,
                "observation_count_before": before_row["observation_count"],
                "observation_count_after": after_row["observation_count"],
                "observation_delta": (
                    after_row["observation_count"]
                    - before_row["observation_count"]
                ),
            }
        )
    return rows


def _lesson_snapshot(progress):
    return {
        "kind": "lesson",
        "lesson_progress_id": progress.pk,
        "lesson_id": progress.lesson_id,
        "status": progress.status,
        "completion_ratio": _decimal_text(progress.completion_ratio),
        "started_at": _datetime_text(progress.started_at),
        "completed_at": _datetime_text(progress.completed_at),
        "last_accessed_at": _datetime_text(progress.last_accessed_at),
    }


def _assessment_snapshot(session):
    return {
        "kind": "assessment",
        "assessment_session_id": session.pk,
        "mode": session.mode,
        "status": session.status,
        "started_at": _datetime_text(session.started_at),
        "ended_at": _datetime_text(session.ended_at),
    }


def _vocabulary_snapshot(decision, *, review_log_ids=None):
    snapshot = {
        "kind": "vocabulary",
        "due_count_at_recommendation": decision.candidate_snapshot.get(
            "vocabulary_due_count", 0
        ),
    }
    if review_log_ids is not None:
        snapshot["review_log_ids"] = list(review_log_ids)
    return snapshot


def _validate_lesson_progress(*, decision, user, progress, require_completed):
    if progress is None:
        raise ValueError("Lesson recommendation outcome requires lesson_progress.")
    if progress.pk is None or progress.user_id != user.pk:
        raise ValueError("lesson_progress does not belong to this user.")

    lesson_id = decision.candidate_snapshot.get("lesson_id")
    if lesson_id is None or progress.lesson_id != lesson_id:
        raise ValueError("lesson_progress does not match the recommended lesson.")

    expected_progress_id = decision.candidate_snapshot.get("lesson_progress_id")
    if expected_progress_id is not None and progress.pk != expected_progress_id:
        raise ValueError("lesson_progress does not match the recommendation snapshot.")

    if require_completed:
        if progress.status != LessonProgress.Status.COMPLETED:
            raise ValueError("Recommended lesson must be completed before completion is recorded.")
    elif progress.status != LessonProgress.Status.IN_PROGRESS:
        raise ValueError("Recommended lesson must be in progress when activity start is recorded.")


def _validate_assessment_session(*, decision, user, session, terminal_status=None):
    if session is None:
        raise ValueError("Assessment recommendation outcome requires assessment_session.")
    if session.pk is None or session.user_id != user.pk:
        raise ValueError("assessment_session does not belong to this user.")

    candidate_type = _candidate_type(decision)
    snapshot_session_id = decision.candidate_snapshot.get("assessment_session_id")
    snapshot_mode = decision.candidate_snapshot.get("assessment_mode")

    if candidate_type == RecommendationCandidateType.RESUME_ASSESSMENT:
        if snapshot_session_id is None or session.pk != snapshot_session_id:
            raise ValueError("assessment_session does not match the recommendation snapshot.")
    elif candidate_type == RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT:
        if session.mode != AssessmentSession.Mode.ADAPTIVE:
            raise ValueError("Started assessment must use adaptive mode.")
    else:
        raise ValueError("Recommendation is not an assessment candidate.")

    if snapshot_mode is not None and session.mode != snapshot_mode:
        raise ValueError("assessment_session mode does not match the recommendation snapshot.")

    if terminal_status is None:
        if session.status != AssessmentSession.Status.ACTIVE:
            raise ValueError("Assessment must be active when activity start is recorded.")
    elif session.status != terminal_status:
        raise ValueError(
            f"Assessment must be {terminal_status} before this outcome is recorded."
        )


def _validate_vocabulary_logs(*, user, logs, started_at, occurred_at):
    logs = tuple(logs)
    if not logs:
        raise ValueError("Vocabulary completion requires at least one review log.")

    seen = set()
    for log in logs:
        if not isinstance(log, VocabularyReviewLog) or log.pk is None:
            raise ValueError("vocabulary_review_logs must contain saved review logs.")
        if log.pk in seen:
            raise ValueError("vocabulary_review_logs must not contain duplicates.")
        seen.add(log.pk)
        if log.user_id != user.pk:
            raise ValueError("Vocabulary review log does not belong to this user.")
        if log.reviewed_at < started_at or log.reviewed_at > occurred_at:
            raise ValueError("Vocabulary review log falls outside the recommendation activity window.")

    return tuple(sorted(logs, key=lambda row: (row.reviewed_at, row.pk)))


def _vocabulary_result(logs):
    count = len(logs)
    remembered = sum(
        1 for row in logs if row.rating == VocabularyReviewLog.Rating.REMEMBERED
    )
    forgot = count - remembered
    remembered_rate = (
        Decimal(remembered) / Decimal(count)
    ).quantize(_RATE_QUANTUM)
    response_times = [row.response_time_ms for row in logs if row.response_time_ms is not None]

    return {
        "kind": "vocabulary",
        "review_count": count,
        "remembered_count": remembered,
        "forgot_count": forgot,
        "remembered_rate": _decimal_text(remembered_rate),
        "response_time_ms_total": sum(response_times) if response_times else None,
        "srs_versions": sorted({row.srs_version for row in logs}),
    }


def _assessment_result(session):
    result = build_completed_assessment_result(session=session)
    return {
        "kind": "assessment",
        "session_id": session.pk,
        "mode": session.mode,
        "summary": result["summary"],
        "affected_skills": result["affected_skills"],
    }


def _lesson_result(progress):
    return {
        "kind": "lesson",
        "lesson_progress_id": progress.pk,
        "lesson_id": progress.lesson_id,
        "status": progress.status,
        "completion_ratio": _decimal_text(progress.completion_ratio),
        "completed_at": _datetime_text(progress.completed_at),
    }


def _canonical_payload(*, event_type, activity_snapshot, result_snapshot, mastery_snapshot, learning_gain_snapshot):
    return {
        "event_type": event_type,
        "outcome_version": OUTCOME_VERSION,
        "activity_snapshot": activity_snapshot,
        "result_snapshot": result_snapshot,
        "mastery_snapshot": mastery_snapshot,
        "learning_gain_snapshot": learning_gain_snapshot,
    }


def _payload_hash(payload):
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _existing_request_event(*, decision, request_id, event_type, target_id=None):
    existing = (
        RecommendationOutcomeEvent.objects
        .filter(decision=decision, request_id=request_id)
        .first()
    )
    if existing is None:
        return None
    if existing.event_type != event_type:
        raise ValueError("request_id was already used for a different outcome event.")

    if target_id is not None:
        stored_target = (
            existing.activity_snapshot.get("lesson_progress_id")
            or existing.activity_snapshot.get("assessment_session_id")
        )
        if stored_target is not None and stored_target != target_id:
            raise ValueError("request_id was already used for a different activity target.")
    return existing


def _assert_event_not_recorded(*, decision, event_type):
    if RecommendationOutcomeEvent.objects.filter(
        decision=decision,
        event_type=event_type,
    ).exists():
        raise ValueError(
            "This outcome event is already recorded; retries must reuse the original request_id."
        )


def _events_by_type(decision):
    return {
        row.event_type: row
        for row in RecommendationOutcomeEvent.objects.filter(decision=decision)
    }


def _persist_event(
    *,
    decision,
    user,
    request_id,
    event_type,
    occurred_at,
    activity_snapshot,
    result_snapshot=None,
    mastery_snapshot=None,
    learning_gain_snapshot=None,
):
    result_snapshot = result_snapshot or {}
    mastery_snapshot = mastery_snapshot or []
    learning_gain_snapshot = learning_gain_snapshot or []
    payload = _canonical_payload(
        event_type=event_type,
        activity_snapshot=activity_snapshot,
        result_snapshot=result_snapshot,
        mastery_snapshot=mastery_snapshot,
        learning_gain_snapshot=learning_gain_snapshot,
    )
    return RecommendationOutcomeEvent.objects.create(
        decision=decision,
        user=user,
        request_id=request_id,
        event_type=event_type,
        outcome_version=OUTCOME_VERSION,
        occurred_at=occurred_at,
        activity_snapshot=activity_snapshot,
        result_snapshot=result_snapshot,
        mastery_snapshot=mastery_snapshot,
        learning_gain_snapshot=learning_gain_snapshot,
        payload_hash=_payload_hash(payload),
    )


def record_recommendation_accepted(*, user, decision, request_id, occurred_at=None):
    """Record an explicit learner acceptance/click of one recommendation."""

    _validate_user(user)
    request_id = _validate_request_id(request_id)
    occurred_at = _validate_occurred_at(occurred_at)

    with transaction.atomic():
        decision = _lock_decision(decision=decision, user=user)
        existing = _existing_request_event(
            decision=decision,
            request_id=request_id,
            event_type=RecommendationOutcomeEvent.EventType.ACCEPTED,
        )
        if existing is not None:
            return existing
        _assert_event_not_recorded(
            decision=decision,
            event_type=RecommendationOutcomeEvent.EventType.ACCEPTED,
        )
        if RecommendationOutcomeEvent.objects.filter(decision=decision).exists():
            raise ValueError("Acceptance must be the first recommendation outcome event.")

        return _persist_event(
            decision=decision,
            user=user,
            request_id=request_id,
            event_type=RecommendationOutcomeEvent.EventType.ACCEPTED,
            occurred_at=occurred_at,
            activity_snapshot={},
        )


def record_recommendation_dismissed(*, user, decision, request_id, occurred_at=None):
    """Record an explicit dismissal before the recommendation is accepted."""

    _validate_user(user)
    request_id = _validate_request_id(request_id)
    occurred_at = _validate_occurred_at(occurred_at)

    with transaction.atomic():
        decision = _lock_decision(decision=decision, user=user)
        existing = _existing_request_event(
            decision=decision,
            request_id=request_id,
            event_type=RecommendationOutcomeEvent.EventType.DISMISSED,
        )
        if existing is not None:
            return existing
        _assert_event_not_recorded(
            decision=decision,
            event_type=RecommendationOutcomeEvent.EventType.DISMISSED,
        )
        if RecommendationOutcomeEvent.objects.filter(decision=decision).exists():
            raise ValueError("Dismissal is allowed only before acceptance or activity start.")

        return _persist_event(
            decision=decision,
            user=user,
            request_id=request_id,
            event_type=RecommendationOutcomeEvent.EventType.DISMISSED,
            occurred_at=occurred_at,
            activity_snapshot={},
        )


def record_recommendation_activity_started(
    *,
    user,
    decision,
    request_id,
    occurred_at=None,
    lesson_progress=None,
    assessment_session=None,
):
    """Record that the accepted recommendation actually started an activity."""

    _validate_user(user)
    request_id = _validate_request_id(request_id)
    occurred_at = _validate_occurred_at(occurred_at)

    target_id = None
    if lesson_progress is not None:
        target_id = lesson_progress.pk
    elif assessment_session is not None:
        target_id = assessment_session.pk

    with transaction.atomic():
        decision = _lock_decision(decision=decision, user=user)
        existing = _existing_request_event(
            decision=decision,
            request_id=request_id,
            event_type=RecommendationOutcomeEvent.EventType.ACTIVITY_STARTED,
            target_id=target_id,
        )
        if existing is not None:
            return existing
        _assert_event_not_recorded(
            decision=decision,
            event_type=RecommendationOutcomeEvent.EventType.ACTIVITY_STARTED,
        )

        events = _events_by_type(decision)
        accepted = events.get(RecommendationOutcomeEvent.EventType.ACCEPTED)
        if accepted is None:
            raise ValueError("Recommendation must be accepted before activity start.")
        if occurred_at < accepted.occurred_at:
            raise ValueError("Activity start cannot occur before recommendation acceptance.")
        if any(
            key in events
            for key in (
                RecommendationOutcomeEvent.EventType.DISMISSED,
                RecommendationOutcomeEvent.EventType.ACTIVITY_COMPLETED,
                RecommendationOutcomeEvent.EventType.ACTIVITY_ABANDONED,
            )
        ):
            raise ValueError("Recommendation is already terminal.")

        candidate_type = _candidate_type(decision)
        if candidate_type in {
            RecommendationCandidateType.START_LESSON,
            RecommendationCandidateType.CONTINUE_LESSON,
        }:
            if assessment_session is not None:
                raise ValueError("Lesson recommendation cannot start an assessment session.")
            _validate_lesson_progress(
                decision=decision,
                user=user,
                progress=lesson_progress,
                require_completed=False,
            )
            activity_snapshot = _lesson_snapshot(lesson_progress)

        elif candidate_type in {
            RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT,
            RecommendationCandidateType.RESUME_ASSESSMENT,
        }:
            if lesson_progress is not None:
                raise ValueError("Assessment recommendation cannot start a lesson progress row.")
            _validate_assessment_session(
                decision=decision,
                user=user,
                session=assessment_session,
            )
            activity_snapshot = _assessment_snapshot(assessment_session)

        elif candidate_type == RecommendationCandidateType.VOCABULARY_REVIEW:
            if lesson_progress is not None or assessment_session is not None:
                raise ValueError("Vocabulary recommendation does not use lesson/assessment targets.")
            activity_snapshot = _vocabulary_snapshot(decision)

        else:  # pragma: no cover - enum validation above is exhaustive
            raise ValueError("Unsupported recommendation candidate type.")

        mastery_snapshot = _mastery_snapshot(
            user=user,
            decision=decision,
            as_of=occurred_at,
        )

        return _persist_event(
            decision=decision,
            user=user,
            request_id=request_id,
            event_type=RecommendationOutcomeEvent.EventType.ACTIVITY_STARTED,
            occurred_at=occurred_at,
            activity_snapshot=activity_snapshot,
            mastery_snapshot=mastery_snapshot,
        )


def record_recommendation_activity_completed(
    *,
    user,
    decision,
    request_id,
    occurred_at=None,
    lesson_progress=None,
    assessment_session=None,
    vocabulary_review_logs=(),
):
    """Record completion and freeze the available learning-result snapshot."""

    _validate_user(user)
    request_id = _validate_request_id(request_id)
    occurred_at = _validate_occurred_at(occurred_at)

    target_id = None
    if lesson_progress is not None:
        target_id = lesson_progress.pk
    elif assessment_session is not None:
        target_id = assessment_session.pk

    with transaction.atomic():
        decision = _lock_decision(decision=decision, user=user)
        existing = _existing_request_event(
            decision=decision,
            request_id=request_id,
            event_type=RecommendationOutcomeEvent.EventType.ACTIVITY_COMPLETED,
            target_id=target_id,
        )
        if existing is not None:
            return existing
        _assert_event_not_recorded(
            decision=decision,
            event_type=RecommendationOutcomeEvent.EventType.ACTIVITY_COMPLETED,
        )

        events = _events_by_type(decision)
        started = events.get(RecommendationOutcomeEvent.EventType.ACTIVITY_STARTED)
        if started is None:
            raise ValueError("Recommendation activity must be started before completion.")
        if occurred_at < started.occurred_at:
            raise ValueError("Activity completion cannot occur before activity start.")
        if RecommendationOutcomeEvent.EventType.ACTIVITY_ABANDONED in events:
            raise ValueError("Abandoned recommendation activity cannot later be completed.")

        candidate_type = _candidate_type(decision)
        if candidate_type in {
            RecommendationCandidateType.START_LESSON,
            RecommendationCandidateType.CONTINUE_LESSON,
        }:
            if assessment_session is not None or tuple(vocabulary_review_logs):
                raise ValueError("Lesson completion received incompatible activity data.")
            _validate_lesson_progress(
                decision=decision,
                user=user,
                progress=lesson_progress,
                require_completed=True,
            )
            if started.activity_snapshot.get("lesson_progress_id") != lesson_progress.pk:
                raise ValueError("Completed lesson differs from the activity that was started.")
            activity_snapshot = _lesson_snapshot(lesson_progress)
            result_snapshot = _lesson_result(lesson_progress)

        elif candidate_type in {
            RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT,
            RecommendationCandidateType.RESUME_ASSESSMENT,
        }:
            if lesson_progress is not None or tuple(vocabulary_review_logs):
                raise ValueError("Assessment completion received incompatible activity data.")
            _validate_assessment_session(
                decision=decision,
                user=user,
                session=assessment_session,
                terminal_status=AssessmentSession.Status.COMPLETED,
            )
            if started.activity_snapshot.get("assessment_session_id") != assessment_session.pk:
                raise ValueError("Completed assessment differs from the activity that was started.")
            activity_snapshot = _assessment_snapshot(assessment_session)
            result_snapshot = _assessment_result(assessment_session)

        elif candidate_type == RecommendationCandidateType.VOCABULARY_REVIEW:
            if lesson_progress is not None or assessment_session is not None:
                raise ValueError("Vocabulary completion received incompatible activity data.")
            logs = _validate_vocabulary_logs(
                user=user,
                logs=vocabulary_review_logs,
                started_at=started.occurred_at,
                occurred_at=occurred_at,
            )
            review_log_ids = tuple(row.pk for row in logs)
            activity_snapshot = _vocabulary_snapshot(
                decision,
                review_log_ids=review_log_ids,
            )
            result_snapshot = _vocabulary_result(logs)

        else:  # pragma: no cover
            raise ValueError("Unsupported recommendation candidate type.")

        mastery_snapshot = _mastery_snapshot(
            user=user,
            decision=decision,
            as_of=occurred_at,
        )
        learning_gain_snapshot = _learning_gain(
            before=started.mastery_snapshot,
            after=mastery_snapshot,
        )

        return _persist_event(
            decision=decision,
            user=user,
            request_id=request_id,
            event_type=RecommendationOutcomeEvent.EventType.ACTIVITY_COMPLETED,
            occurred_at=occurred_at,
            activity_snapshot=activity_snapshot,
            result_snapshot=result_snapshot,
            mastery_snapshot=mastery_snapshot,
            learning_gain_snapshot=learning_gain_snapshot,
        )


def record_recommendation_activity_abandoned(
    *,
    user,
    decision,
    request_id,
    assessment_session,
    occurred_at=None,
):
    """Record a verifiable abandoned assessment recommendation activity."""

    _validate_user(user)
    request_id = _validate_request_id(request_id)
    occurred_at = _validate_occurred_at(occurred_at)

    with transaction.atomic():
        decision = _lock_decision(decision=decision, user=user)
        existing = _existing_request_event(
            decision=decision,
            request_id=request_id,
            event_type=RecommendationOutcomeEvent.EventType.ACTIVITY_ABANDONED,
            target_id=assessment_session.pk if assessment_session is not None else None,
        )
        if existing is not None:
            return existing
        _assert_event_not_recorded(
            decision=decision,
            event_type=RecommendationOutcomeEvent.EventType.ACTIVITY_ABANDONED,
        )

        events = _events_by_type(decision)
        started = events.get(RecommendationOutcomeEvent.EventType.ACTIVITY_STARTED)
        if started is None:
            raise ValueError("Recommendation activity must be started before abandonment.")
        if occurred_at < started.occurred_at:
            raise ValueError("Activity abandonment cannot occur before activity start.")
        if RecommendationOutcomeEvent.EventType.ACTIVITY_COMPLETED in events:
            raise ValueError("Completed recommendation activity cannot later be abandoned.")

        _validate_assessment_session(
            decision=decision,
            user=user,
            session=assessment_session,
            terminal_status=AssessmentSession.Status.ABANDONED,
        )
        if started.activity_snapshot.get("assessment_session_id") != assessment_session.pk:
            raise ValueError("Abandoned assessment differs from the activity that was started.")

        mastery_snapshot = _mastery_snapshot(
            user=user,
            decision=decision,
            as_of=occurred_at,
        )
        return _persist_event(
            decision=decision,
            user=user,
            request_id=request_id,
            event_type=RecommendationOutcomeEvent.EventType.ACTIVITY_ABANDONED,
            occurred_at=occurred_at,
            activity_snapshot=_assessment_snapshot(assessment_session),
            mastery_snapshot=mastery_snapshot,
        )


def get_recommendation_outcome_state(*, decision):
    """Return the current lifecycle projection without mutating any row."""

    if decision.pk is None:
        raise ValueError("RecommendationDecision must be saved.")

    events = _events_by_type(decision)
    accepted = events.get(RecommendationOutcomeEvent.EventType.ACCEPTED)
    started = events.get(RecommendationOutcomeEvent.EventType.ACTIVITY_STARTED)
    completed = events.get(RecommendationOutcomeEvent.EventType.ACTIVITY_COMPLETED)
    abandoned = events.get(RecommendationOutcomeEvent.EventType.ACTIVITY_ABANDONED)
    dismissed = events.get(RecommendationOutcomeEvent.EventType.DISMISSED)

    if completed is not None:
        status = RecommendationOutcomeStatus.COMPLETED
        ended_at = completed.occurred_at
    elif abandoned is not None:
        status = RecommendationOutcomeStatus.ABANDONED
        ended_at = abandoned.occurred_at
    elif dismissed is not None:
        status = RecommendationOutcomeStatus.DISMISSED
        ended_at = dismissed.occurred_at
    elif started is not None:
        status = RecommendationOutcomeStatus.STARTED
        ended_at = None
    elif accepted is not None:
        status = RecommendationOutcomeStatus.ACCEPTED
        ended_at = None
    else:
        status = RecommendationOutcomeStatus.SHOWN
        ended_at = None

    return RecommendationOutcomeState(
        decision_id=decision.pk,
        status=status,
        accepted_at=accepted.occurred_at if accepted is not None else None,
        started_at=started.occurred_at if started is not None else None,
        ended_at=ended_at,
    )
