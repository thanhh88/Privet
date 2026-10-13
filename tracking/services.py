from dataclasses import dataclass
from datetime import datetime

from django.contrib.auth import get_user_model
from django.db import transaction

from curriculum.models import LessonSkill
from learning_engine.mastery import (
    recalculate_learning_recency,
)
from tracking.models import (
    StudyEvent,
    StudySession,
)


class TrackingServiceError(Exception):
    pass


@dataclass(frozen=True)
class LearningCompletionEventResult:
    study_event_id: int
    study_session_id: int
    user_id: int
    lesson_id: int
    event_type: str
    occurred_at: datetime
    created: bool


@transaction.atomic
def record_learning_completion_event(
    *,
    user,
    study_session_id,
):
    """
    Record the canonical completion milestone for a
    completed lesson/practice StudySession.

    LESSON   -> lesson_completed
    PRACTICE -> practice_completed

    The event timestamp comes from StudySession.ended_at.

    After recording the event, only learning recency
    is rebuilt for Skills mapped to the Lesson.
    """

    if user.pk is None:
        raise TrackingServiceError(
            "User must be saved."
        )

    User = get_user_model()

    try:
        locked_user = (
            User.objects
            .select_for_update()
            .get(
                pk=user.pk
            )
        )
    except User.DoesNotExist as exc:
        raise TrackingServiceError(
            "User does not exist."
        ) from exc

    try:
        study_session = (
            StudySession.objects
            .select_for_update()
            .get(
                pk=study_session_id,
                user=locked_user,
            )
        )
    except StudySession.DoesNotExist as exc:
        raise TrackingServiceError(
            "Study session does not exist "
            "or does not belong to this user."
        ) from exc

    if (
        study_session.status
        != StudySession.Status.COMPLETED
    ):
        raise TrackingServiceError(
            "Learning completion events require "
            "a completed StudySession."
        )

    if study_session.ended_at is None:
        raise TrackingServiceError(
            "Completed StudySession must have ended_at."
        )

    if (
        study_session.activity_type
        == StudySession.ActivityType.LESSON
    ):
        event_type = (
            StudyEvent.EventType.LESSON_COMPLETED
        )

    elif (
        study_session.activity_type
        == StudySession.ActivityType.PRACTICE
    ):
        event_type = (
            StudyEvent.EventType.PRACTICE_COMPLETED
        )

    else:
        raise TrackingServiceError(
            "Only lesson or practice StudySessions "
            "can create learning completion events."
        )

    if study_session.lesson_id is None:
        raise TrackingServiceError(
            "Learning StudySession must have a lesson."
        )

    # Idempotent per StudySession + completion type.
    existing_event = (
        StudyEvent.objects
        .select_for_update()
        .filter(
            session=study_session,
            event_type=event_type,
        )
        .order_by(
            "study_event_id"
        )
        .first()
    )

    if existing_event is not None:
        event = existing_event
        created = False

    else:
        event = StudyEvent(
            session=study_session,
            user=locked_user,
            event_type=event_type,
            lesson_id=study_session.lesson_id,
            assessment_session=None,
            occurred_at=study_session.ended_at,
        )

        event.full_clean()
        event.save()

        created = True

    skill_ids = list(
        LessonSkill.objects
        .filter(
            lesson_id=study_session.lesson_id
        )
        .order_by(
            "skill_id"
        )
        .values_list(
            "skill_id",
            flat=True,
        )
    )

    if skill_ids:
        recalculate_learning_recency(
            user=locked_user,
            skill_ids=skill_ids,
        )

    return LearningCompletionEventResult(
        study_event_id=event.pk,
        study_session_id=study_session.pk,
        user_id=locked_user.pk,
        lesson_id=study_session.lesson_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        created=created,
    )