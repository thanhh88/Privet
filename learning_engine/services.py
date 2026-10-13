from dataclasses import dataclass
from datetime import datetime
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from curriculum.models import Lesson

from learning_engine.models import (
    PracticeSession,
    QuestionAttempt,
    SessionQuestion,
)
from questionbank.models import (
    Question,
    QuestionSkill,
)
from learning_engine.mastery import (
    recalculate_skill_masteries,
)


class LearningServiceError(ValueError):
    pass


@dataclass(frozen=True)
class SubmitAnswerResult:
    attempt_id: int
    session_question_id: int
    session_id: int
    question_id: int
    source_id: str

    # Position of the Question inside the assessment.
    sequence_number: int

    # 1 = first submit, 2 = first revision, ...
    attempt_number: int

    is_correct: bool
    correct_option_key: str | None

@dataclass(frozen=True)
class SessionQuestionResult:
    session_question_id: int
    session_id: int
    question_id: int
    source_id: str
    sequence_number: int
    first_viewed_at: datetime | None

@dataclass(frozen=True)
class SessionQuestionTimingResult:
    session_question_id: int
    session_id: int
    active_response_time_ms: int
    first_viewed_at: datetime | None

@dataclass(frozen=True)
class PracticeSessionResult:
    session_id: int
    mode: str
    status: str
    lesson_id: int | None

def _normalize_option_key(value):
    if not isinstance(value, str):
        raise LearningServiceError(
            "selected_option_key must be a string."
        )

    value = value.strip()

    if not value:
        raise LearningServiceError(
            "selected_option_key must not be blank."
        )

    return value


def _grade_option_question(
    question,
    *,
    selected_option_key,
):
    selected_option_key = (
        _normalize_option_key(
            selected_option_key
        )
    )

    options = list(
        question.options.all()
    )

    option_by_key = {
        option.option_key: option
        for option in options
    }

    selected_option = option_by_key.get(
        selected_option_key
    )

    if selected_option is None:
        raise LearningServiceError(
            f"Option key "
            f"{selected_option_key!r} "
            "does not belong to this question."
        )

    correct_options = [
        option
        for option in options
        if option.is_correct
    ]

    if len(correct_options) != 1:
        raise LearningServiceError(
            f"Question {question.source_id!r} "
            "does not have exactly one "
            "correct option."
        )

    correct_option = correct_options[0]

    return (
        bool(selected_option.is_correct),
        correct_option.option_key,
        selected_option_key,
    )

def _next_session_question_sequence(
    session,
):
    current_max = (
        SessionQuestion.objects.filter(
            session=session
        )
        .aggregate(
            value=Max(
                "sequence_number"
            )
        )["value"]
    )

    if current_max is None:
        return 1

    return current_max + 1


def _next_attempt_number(
    session_question,
):
    current_max = (
        QuestionAttempt.objects.filter(
            session_question=session_question
        )
        .aggregate(
            value=Max(
                "attempt_number"
            )
        )["value"]
    )

    if current_max is None:
        return 1

    return current_max + 1

@transaction.atomic
def add_question_to_session(
    *,
    user,
    session_id,
    question_id,
):
    """
    Add one Question to an active assessment session.

    A Question may appear only once in a session.
    The session determines sequence_number.
    """

    try:
        session = (
            PracticeSession.objects
            .select_for_update()
            .get(
                pk=session_id,
                user=user,
            )
        )
    except PracticeSession.DoesNotExist as exc:
        raise LearningServiceError(
            "Practice session does not exist "
            "or does not belong to this user."
        ) from exc

    if session.status != (
        PracticeSession.Status.ACTIVE
    ):
        raise LearningServiceError(
            "Questions can only be added "
            "to an active practice session."
        )

    if session.mode not in {
        PracticeSession.Mode.ADAPTIVE,
        PracticeSession.Mode.DIAGNOSTIC,
    }:
        raise LearningServiceError(
            "Assessment questions may only "
            "be added to adaptive or "
            "diagnostic sessions."
        )

    try:
        question = Question.objects.get(
            pk=question_id,
            is_active=True,
        )
    except Question.DoesNotExist as exc:
        raise LearningServiceError(
            "Question does not exist "
            "or is inactive."
        ) from exc

    if SessionQuestion.objects.filter(
        session=session,
        question=question,
    ).exists():
        raise LearningServiceError(
            "This question is already "
            "present in the session."
        )

    sequence_number = (
        _next_session_question_sequence(
            session
        )
    )

    session_question = SessionQuestion(
        session=session,
        question=question,
        sequence_number=sequence_number,
    )

    try:
        session_question.full_clean()
    except ValidationError as exc:
        raise LearningServiceError(
            f"SessionQuestion validation "
            f"failed: {exc}"
        ) from exc

    session_question.save()

    return SessionQuestionResult(
        session_question_id=(
            session_question.pk
        ),
        session_id=session.pk,
        question_id=question.pk,
        source_id=question.source_id,
        sequence_number=(
            session_question.sequence_number
        ),
        first_viewed_at=(
            session_question.first_viewed_at
        ),
    )

@transaction.atomic
def mark_session_question_viewed(
    *,
    user,
    session_question_id,
):
    """
    Mark an assessment item as viewed.

    Idempotent:
    calling this more than once does not change
    the original first_viewed_at timestamp.
    """

    try:
        session_question = (
            SessionQuestion.objects
            .select_for_update()
            .select_related(
                "session",
                "question",
            )
            .get(
                pk=session_question_id,
                session__user=user,
            )
        )
    except SessionQuestion.DoesNotExist as exc:
        raise LearningServiceError(
            "Session question does not exist "
            "or does not belong to this user."
        ) from exc

    session = session_question.session

    if session.status != (
        PracticeSession.Status.ACTIVE
    ):
        raise LearningServiceError(
            "Only questions in an active "
            "session can be marked as viewed."
        )

    if session.mode not in {
        PracticeSession.Mode.ADAPTIVE,
        PracticeSession.Mode.DIAGNOSTIC,
    }:
        raise LearningServiceError(
            "Lesson practice is not "
            "assessment evidence."
        )

    if (
        session_question.first_viewed_at
        is None
    ):
        session_question.first_viewed_at = (
            timezone.now()
        )

        session_question.save(
            update_fields=[
                "first_viewed_at",
            ]
        )

    return SessionQuestionResult(
        session_question_id=(
            session_question.pk
        ),
        session_id=session.pk,
        question_id=(
            session_question.question_id
        ),
        source_id=(
            session_question.question.source_id
        ),
        sequence_number=(
            session_question.sequence_number
        ),
        first_viewed_at=(
            session_question.first_viewed_at
        ),
    )

@transaction.atomic
def record_session_question_active_time(
    *,
    user,
    session_question_id,
    delta_ms,
):
    """
    Add active viewing time to one assessment question.

    active_response_time_ms is cumulative across
    repeated visits to the same SessionQuestion.

    Recording active time also guarantees that the
    question is considered viewed.
    """

    if (
        isinstance(
            delta_ms,
            bool,
        )
        or not isinstance(
            delta_ms,
            int,
        )
        or delta_ms < 0
    ):
        raise LearningServiceError(
            "delta_ms must be a "
            "non-negative integer."
        )

    try:
        session_question = (
            SessionQuestion.objects
            .select_for_update()
            .select_related(
                "session",
            )
            .get(
                pk=session_question_id,
                session__user=user,
            )
        )

    except SessionQuestion.DoesNotExist as exc:
        raise LearningServiceError(
            "Session question does not exist "
            "or does not belong to this user."
        ) from exc

    session = session_question.session

    if session.status != (
        PracticeSession.Status.ACTIVE
    ):
        raise LearningServiceError(
            "Active time can only be recorded "
            "for an active practice session."
        )

    if session.mode not in {
        PracticeSession.Mode.ADAPTIVE,
        PracticeSession.Mode.DIAGNOSTIC,
    }:
        raise LearningServiceError(
            "Assessment question timing is only "
            "available for adaptive or "
            "diagnostic sessions."
        )

    # A timing event means the learner actually
    # had this question open.
    if (
        session_question.first_viewed_at
        is None
    ):
        session_question.first_viewed_at = (
            timezone.now()
        )

    session_question.active_response_time_ms += (
        delta_ms
    )

    session_question.save(
        update_fields=[
            "first_viewed_at",
            "active_response_time_ms",
        ]
    )

    return SessionQuestionTimingResult(
        session_question_id=(
            session_question.pk
        ),
        session_id=(
            session.pk
        ),
        active_response_time_ms=(
            session_question.active_response_time_ms
        ),
        first_viewed_at=(
            session_question.first_viewed_at
        ),
    )

@transaction.atomic
def start_session(
    *,
    user,
    mode,
    lesson_id=None,
):
    """
    Start a new practice session.

    Rules:
    - lesson mode requires lesson_id
    - adaptive / diagnostic must not have lesson_id
    - status is always ACTIVE
    - started_at is generated by the database/model
    """

    if mode not in PracticeSession.Mode.values:
        raise LearningServiceError(
            f"Unsupported practice mode: {mode!r}."
        )

    lesson = None

    if mode == PracticeSession.Mode.LESSON:
        if lesson_id is None:
            raise LearningServiceError(
                "Lesson mode requires lesson_id."
            )

        try:
            lesson = Lesson.objects.get(
                pk=lesson_id,
                is_active=True,
            )
        except Lesson.DoesNotExist as exc:
            raise LearningServiceError(
                "Lesson does not exist or is inactive."
            ) from exc

    else:
        if lesson_id is not None:
            raise LearningServiceError(
                f"{mode} mode must not "
                "include lesson_id."
            )

    session = PracticeSession(
        user=user,
        lesson=lesson,
        mode=mode,
        status=(
            PracticeSession.Status.ACTIVE
        ),
        ended_at=None,
    )

    try:
        session.full_clean()
    except ValidationError as exc:
        raise LearningServiceError(
            f"Session validation failed: {exc}"
        ) from exc

    session.save()

    return PracticeSessionResult(
        session_id=session.pk,
        mode=session.mode,
        status=session.status,
        lesson_id=session.lesson_id,
    )

def _get_active_session_for_update(
    *,
    user,
    session_id,
):
    try:
        session = (
            PracticeSession.objects
            .select_for_update()
            .get(
                pk=session_id,
                user=user,
            )
        )
    except PracticeSession.DoesNotExist as exc:
        raise LearningServiceError(
            "Practice session does not exist "
            "or does not belong to this user."
        ) from exc

    if session.status != (
        PracticeSession.Status.ACTIVE
    ):
        raise LearningServiceError(
            "Only an active practice session "
            "can be finished."
        )

    return session

def _finish_session(
    *,
    user,
    session_id,
    target_status,
):
    session = (
        _get_active_session_for_update(
            user=user,
            session_id=session_id,
        )
    )

    session.status = target_status
    session.ended_at = timezone.now()

    try:
        session.full_clean()
    except ValidationError as exc:
        raise LearningServiceError(
            f"Session validation failed: {exc}"
        ) from exc

    session.save(
        update_fields=[
            "status",
            "ended_at",
        ]
    )

    return PracticeSessionResult(
        session_id=session.pk,
        mode=session.mode,
        status=session.status,
        lesson_id=session.lesson_id,
    )

def _affected_skill_ids_for_session(
    session_id,
):
    """
    Return every Skill represented by Questions
    selected into the assessment.

    Viewed state does not matter here:
    even a never-viewed Question still makes its
    Skills part of the recalculation set.
    """

    question_ids = (
        SessionQuestion.objects
        .filter(
            session_id=session_id
        )
        .values_list(
            "question_id",
            flat=True,
        )
    )

    return list(
        QuestionSkill.objects
        .filter(
            question_id__in=question_ids
        )
        .order_by(
            "skill_id"
        )
        .values_list(
            "skill_id",
            flat=True,
        )
        .distinct()
    )

@transaction.atomic
def complete_session(
    *,
    user,
    session_id,
):
    result = _finish_session(
        user=user,
        session_id=session_id,
        target_status=(
            PracticeSession.Status.COMPLETED
        ),
    )

    # Guided lesson practice is intentionally
    # excluded from assessment mastery.
    if result.mode in {
        PracticeSession.Mode.ADAPTIVE,
        PracticeSession.Mode.DIAGNOSTIC,
    }:
        skill_ids = (
            _affected_skill_ids_for_session(
                result.session_id
            )
        )

        if skill_ids:
            completed_session = (
                PracticeSession.objects
                .only(
                    "ended_at"
                )
                .get(
                    pk=result.session_id
                )
            )

            recalculate_skill_masteries(
                user=user,
                skill_ids=skill_ids,
                as_of=(
                    completed_session.ended_at
                ),
            )

    return result

@transaction.atomic
def abandon_session(
    *,
    user,
    session_id,
):
    return _finish_session(
        user=user,
        session_id=session_id,
        target_status=(
            PracticeSession.Status.ABANDONED
        ),
    )

@transaction.atomic
def submit_option_answer(
    *,
    user,
    session_question_id,
    selected_option_key,
    response_time_ms=None,
):
    """
    Submit one answer event for an already-selected
    SessionQuestion.

    Every submit creates a new QuestionAttempt.

    If the learner changes the answer:
        attempt_number 1 -> first answer
        attempt_number 2 -> revision
        attempt_number 3 -> another revision

    Correctness is always calculated server-side.
    """

    try:
        session_question = (
            SessionQuestion.objects
            .select_for_update()
            .select_related(
                "session",
                "question",
            )
            .get(
                pk=session_question_id,
                session__user=user,
            )
        )
    except SessionQuestion.DoesNotExist as exc:
        raise LearningServiceError(
            "Session question does not exist "
            "or does not belong to this user."
        ) from exc

    session = session_question.session
    question = session_question.question

    if session.status != (
        PracticeSession.Status.ACTIVE
    ):
        raise LearningServiceError(
            "Answers can only be submitted "
            "to an active practice session."
        )

    if session.mode not in {
        PracticeSession.Mode.ADAPTIVE,
        PracticeSession.Mode.DIAGNOSTIC,
    }:
        raise LearningServiceError(
            "Lesson practice does not create "
            "assessment QuestionAttempt records."
        )

    if not question.is_active:
        raise LearningServiceError(
            "Question is inactive."
        )

    if question.question_type not in {
        "multiple_choice",
        "fill_choice",
    }:
        raise LearningServiceError(
            "submit_option_answer supports only "
            "multiple_choice and fill_choice "
            "questions."
        )

    (
        is_correct,
        correct_option_key,
        normalized_option_key,
    ) = _grade_option_question(
        question,
        selected_option_key=(
            selected_option_key
        ),
    )

    if response_time_ms is not None:
        if (
            isinstance(
                response_time_ms,
                bool,
            )
            or not isinstance(
                response_time_ms,
                int,
            )
            or response_time_ms < 0
        ):
            raise LearningServiceError(
                "response_time_ms must be "
                "a non-negative integer or null."
            )

    # Submitting implies that the question
    # has been viewed.
    if (
        session_question.first_viewed_at
        is None
    ):
        session_question.first_viewed_at = (
            timezone.now()
        )

        session_question.save(
            update_fields=[
                "first_viewed_at",
            ]
        )

    attempt_number = (
        _next_attempt_number(
            session_question
        )
    )

    attempt = QuestionAttempt(
        session_question=session_question,
        attempt_number=attempt_number,
        selected_option_key=(
            normalized_option_key
        ),
        response_text=None,
        is_correct=is_correct,
        response_time_ms=(
            response_time_ms
        ),
    )

    try:
        attempt.full_clean()
    except ValidationError as exc:
        raise LearningServiceError(
            f"Attempt validation failed: {exc}"
        ) from exc

    attempt.save()

    return SubmitAnswerResult(
        attempt_id=attempt.pk,
        session_question_id=(
            session_question.pk
        ),
        session_id=session.pk,
        question_id=question.pk,
        source_id=question.source_id,
        sequence_number=(
            session_question.sequence_number
        ),
        attempt_number=(
            attempt.attempt_number
        ),
        is_correct=attempt.is_correct,
        correct_option_key=(
            correct_option_key
        ),
    )
