
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
    Called once for a newly accepted SRS review,
    inside the SRS transaction.

    Must not create:
      - lesson_completed
      - practice_completed
    """

    seconds = (response_time_ms or 0) // 1000

    session = StudySession.objects.create(
        user=user,
        activity_type=StudySession.ActivityType.VOCABULARY,
        status=StudySession.Status.COMPLETED,

        lesson=None,
        assessment_session=None,

        ended_at=at,
        last_activity_at=at,
        active_seconds=seconds,
    )

    for event_type in (
        StudyEvent.EventType.VOCABULARY_REVIEW_STARTED,
        StudyEvent.EventType.VOCABULARY_REVIEW_COMPLETED,
    ):
        StudyEvent.objects.create(
            session=session,
            user=user,
            event_type=event_type,

            lesson=None,
            assessment_session=None,

            occurred_at=at,
        )

    return session
