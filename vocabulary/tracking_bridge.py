"""Vocabulary-specific tracking only."""

from tracking.models import (
    StudyEvent,
    StudySession,
)


def record_vocabulary_review(
    *,
    user,
    at,
    response_time_ms,
):
    """
    Record one accepted vocabulary SRS review.

    This function is called inside the SRS transaction.
    Vocabulary tracking is intentionally isolated from
    lesson/practice completion and SkillMastery updates.
    """

    seconds = (response_time_ms or 0) // 1000

    session = StudySession(
        user=user,
        activity_type=StudySession.ActivityType.VOCABULARY,
        status=StudySession.Status.COMPLETED,
        lesson=None,
        assessment_session=None,
        ended_at=at,
        last_activity_at=at,
        active_seconds=seconds,
    )
    session.full_clean()
    session.save()

    for event_type in (
        StudyEvent.EventType.VOCABULARY_REVIEW_STARTED,
        StudyEvent.EventType.VOCABULARY_REVIEW_COMPLETED,
    ):
        event = StudyEvent(
            session=session,
            user=user,
            event_type=event_type,
            lesson=None,
            assessment_session=None,
            occurred_at=at,
        )
        event.full_clean()
        event.save()

    return session
