import json

from django.contrib.auth.decorators import login_required
from django.http import (
    HttpRequest,
    JsonResponse,
)
from django.shortcuts import get_object_or_404
from django.views.decorators.http import (
    require_GET,
    require_POST,
    require_http_methods,
)

from curriculum.models import (
    Lesson,
    LessonProgress,
)

from learning_engine.beginner_path import (
    resolve_beginner_lesson,
)
from learning_engine.cold_start import (
    LearnerEntryRoute,
)
from learning_engine.learner_entry import (
    resolve_learner_entry_state,
)
from learning_engine.services import (
    LearningServiceError,
    abandon_session,
    complete_lesson,
    complete_session,
    mark_session_question_viewed,
    record_session_question_active_time,
    save_option_answer,
    start_or_resume_lesson,
    submit_prior_russian_experience,
    update_lesson_progress,
)
from decimal import (
    Decimal,
    InvalidOperation,
)
from learning_engine.models import (
    AssessmentSession,
    QuestionAttempt,
    SessionQuestion,
)
from learning_engine.assessment_results import (
    AssessmentResultError,
    build_completed_assessment_result,
    build_current_mastery_projection,
)


# Serialization helpers
def _serialize_next_action(
    *,
    user,
):
    """
    Return the learner's authoritative CURRENT
    continuation route.

    This describes what the learner should do now,
    not necessarily what immediately followed the
    historical assessment being viewed.
    """

    state = resolve_learner_entry_state(
        user=user,
    )

    payload = {
        "route": (
            state.route.value
        ),

        "active_assessment": None,

        "beginner_lesson": None,
    }

    if state.route in {
        LearnerEntryRoute.RESUME_DIAGNOSTIC,
        LearnerEntryRoute.RESUME_ADAPTIVE,
    }:
        payload[
            "active_assessment"
        ] = {
            "session_id": (
                state.active_session_id
            ),

            "mode": (
                state.active_assessment_mode
            ),
        }

    if (
        state.route
        == LearnerEntryRoute.BEGINNER_PATH
    ):
        payload[
            "beginner_lesson"
        ] = _serialize_beginner_lesson(
            user=user,
        )

    return payload
def _serialize_finished_assessment(
    session,
):
    """
    Serialize terminal assessment metadata.

    Detailed correctness / result feedback belongs
    to the separate 9E.5 Result API.
    """

    return {
        "session_id": session.pk,
        "mode": session.mode,
        "status": session.status,

        "started_at": (
            session.started_at.isoformat()
        ),

        "ended_at": (
            session.ended_at.isoformat()
            if session.ended_at is not None
            else None
        ),
    }
def _serialize_active_assessment(
    *,
    session,
):
    """
    Serialize one ACTIVE assessment for learner UI.

    SECURITY:
    this serializer deliberately uses an explicit
    whitelist of learner-safe fields.

    It must never expose correctness, correct
    answers, explanations, or misconception data.
    """

    session_questions = list(
        SessionQuestion.objects
        .filter(
            session=session,
        )
        .select_related(
            "question",
        )
        .prefetch_related(
            "question__options",
        )
        .order_by(
            "sequence_number",
            "session_question_id",
        )
    )

    session_question_ids = [
        row.pk
        for row in session_questions
    ]

    # Fetch all revisions once.
    #
    # Because rows are ordered by attempt_number
    # ascending, later assignments overwrite older
    # ones and leave the final/latest attempt.
    latest_attempt_by_question = {}

    attempts = (
        QuestionAttempt.objects
        .filter(
            session_question_id__in=(
                session_question_ids
            ),
        )
        .order_by(
            "session_question_id",
            "attempt_number",
            "attempt_id",
        )
    )

    for attempt in attempts:
        latest_attempt_by_question[
            attempt.session_question_id
        ] = attempt

    question_payloads = []

    for session_question in session_questions:
        question = session_question.question

        # Do not rely on QuestionOption model
        # default ordering.
        options = sorted(
            question.options.all(),
            key=lambda option: (
                option.display_order,
                option.option_id,
            ),
        )

        safe_options = [
            {
                "option_key": (
                    option.option_key
                ),
                "option_text": (
                    option.option_text
                ),
                "display_order": (
                    option.display_order
                ),
            }
            for option in options
        ]

        attempt = (
            latest_attempt_by_question.get(
                session_question.pk
            )
        )

        current_answer = None

        if attempt is not None:
            current_answer = {
                "attempt_id": (
                    attempt.pk
                ),
                "attempt_number": (
                    attempt.attempt_number
                ),
                "selected_option_key": (
                    attempt.selected_option_key
                ),
                "response_text": (
                    attempt.response_text
                ),
                "response_time_ms": (
                    attempt.response_time_ms
                ),
                "answered_at": (
                    attempt.answered_at.isoformat()
                ),
            }

        question_payloads.append(
            {
                "session_question_id": (
                    session_question.pk
                ),

                "sequence_number": (
                    session_question.sequence_number
                ),

                "selected_at": (
                    session_question
                    .selected_at
                    .isoformat()
                ),

                "first_viewed_at": (
                    session_question
                    .first_viewed_at
                    .isoformat()
                    if session_question.first_viewed_at
                    is not None
                    else None
                ),

                "active_response_time_ms": (
                    session_question
                    .active_response_time_ms
                ),

                "question": {
                    "question_id": (
                        question.pk
                    ),

                    "source_id": (
                        question.source_id
                    ),

                    "question_type": (
                        question.question_type
                    ),

                    "item_format": (
                        question.item_format
                    ),

                    "question_text": (
                        question.question_text
                    ),

                    "audio_url": (
                        question.audio_url
                    ),

                    "options": (
                        safe_options
                    ),
                },

                "current_answer": (
                    current_answer
                ),
            }
        )

    viewed_question_count = sum(
        1
        for row in session_questions
        if row.first_viewed_at is not None
    )

    answered_question_count = sum(
        1
        for row in session_questions
        if row.pk
        in latest_attempt_by_question
    )

    return {
        "session_id": (
            session.pk
        ),

        "mode": (
            session.mode
        ),

        "status": (
            session.status
        ),

        "started_at": (
            session.started_at.isoformat()
        ),

        "question_count": (
            len(
                question_payloads
            )
        ),

        "viewed_question_count": (
            viewed_question_count
        ),

        "answered_question_count": (
            answered_question_count
        ),

        "questions": (
            question_payloads
        ),
    }

def _parse_completion_ratio(
    value,
):
    """
    Parse API completion_ratio into exact Decimal.

    Returns:
        Decimal | None
    """

    if isinstance(
        value,
        bool,
    ):
        return None

    if not isinstance(
        value,
        (
            int,
            float,
            str,
        ),
    ):
        return None

    try:
        ratio = Decimal(
            str(value)
        )

    except (
        InvalidOperation,
        ValueError,
    ):
        return None

    if not ratio.is_finite():
        return None

    if (
        ratio < Decimal("0")
        or ratio >= Decimal("1")
    ):
        return None

    # API must not silently round values that
    # exceed the DecimalField scale.
    if (
        ratio.quantize(
            Decimal("0.0001")
        )
        != ratio
    ):
        return None

    return ratio

def _serialize_lesson_progress(
    progress,
):
    """
    Serialize one LessonProgress record.

    This helper performs no database writes.
    """

    if progress is None:
        return None

    return {
        "lesson_progress_id": (
            progress.pk
        ),

        "status": (
            progress.status
        ),

        "completion_ratio": str(
            progress.completion_ratio
        ),

        "started_at": (
            progress.started_at.isoformat()
        ),

        "completed_at": (
            progress.completed_at.isoformat()
            if progress.completed_at is not None
            else None
        ),

        "last_accessed_at": (
            progress.last_accessed_at.isoformat()
        ),
    }


def _serialize_beginner_lesson(
    *,
    user,
):
    """
    Resolve and serialize the next beginner Lesson.

    Resolution itself is read-only.
    """

    resolution = resolve_beginner_lesson(
        user=user,
    )

    return {
        "action": (
            resolution.action.value
        ),

        "lesson_id": (
            resolution.lesson_id
        ),

        "lesson_title": (
            resolution.lesson_title
        ),

        "lesson_display_order": (
            resolution.lesson_display_order
        ),

        "lesson_progress_id": (
            resolution.lesson_progress_id
        ),

        "unavailable_reason": (
            resolution.unavailable_reason.value
            if resolution.unavailable_reason
            is not None
            else None
        ),
    }


def _serialize_lesson_detail(
    *,
    lesson,
    user,
):
    """
    Serialize one Lesson together with the
    authenticated learner's existing progress.

    This helper performs no database writes.
    """

    progress = (
        LessonProgress.objects
        .filter(
            user=user,
            lesson=lesson,
        )
        .first()
    )

    skill_rows = (
        lesson.lesson_skills
        .select_related(
            "skill",
        )
        .order_by(
            "skill__display_order",
            "skill__skill_code",
            "skill__skill_id",
        )
    )

    skills = [
        {
            "skill_id": (
                row.skill_id
            ),

            "skill_code": (
                row.skill.skill_code
            ),

            "skill_name": (
                row.skill.skill_name
            ),

            "cefr_level": (
                row.skill.cefr_level
            ),

            "is_extension": (
                row.skill.is_extension
            ),
        }
        for row in skill_rows
    ]

    return {
        "lesson_id": (
            lesson.pk
        ),

        "title": (
            lesson.title
        ),

        "lesson_type": (
            lesson.lesson_type
        ),

        "display_order": (
            lesson.display_order
        ),

        "content": (
            lesson.content
        ),

        "audio_url": (
            lesson.audio_url
        ),

        "skills": (
            skills
        ),

        "progress": (
            _serialize_lesson_progress(
                progress
            )
        ),
    }


# ============================================================
# 9E.1
# Learner Entry API
# ============================================================


@login_required
@require_GET
def learner_entry_view(
    request: HttpRequest,
):
    """
    Return the authoritative learner entry state
    for the currently authenticated user.

    This endpoint performs no assessment or
    LessonProgress writes.
    """

    try:
        state = resolve_learner_entry_state(
            user=request.user,
        )

    except ValueError as exc:
        return JsonResponse(
            {
                "error": (
                    "learner_state_conflict"
                ),
                "detail": (
                    str(exc)
                ),
            },
            status=409,
        )

    payload = {
        "route": (
            state.route.value
        ),

        "prior_russian_experience": (
            state.prior_russian_experience
        ),

        "has_finalized_assessment_evidence": (
            state
            .has_finalized_assessment_evidence
        ),

        "active_assessment": None,

        "beginner_lesson": None,
    }

    # --------------------------------------------------------
    # Resume existing assessment
    # --------------------------------------------------------

    if state.route in {
        LearnerEntryRoute.RESUME_DIAGNOSTIC,
        LearnerEntryRoute.RESUME_ADAPTIVE,
    }:
        payload[
            "active_assessment"
        ] = {
            "session_id": (
                state.active_session_id
            ),

            "mode": (
                state.active_assessment_mode
            ),
        }

    # --------------------------------------------------------
    # True beginner
    # --------------------------------------------------------

    if (
        state.route
        == LearnerEntryRoute.BEGINNER_PATH
    ):
        payload[
            "beginner_lesson"
        ] = _serialize_beginner_lesson(
            user=request.user,
        )

    return JsonResponse(
        payload,
        status=200,
    )


# ============================================================
# 9E.2
# Prior Russian Experience API
# ============================================================


@login_required
@require_POST
def submit_prior_experience_view(
    request: HttpRequest,
):
    """
    Persist the authenticated learner's answer to
    the initial Russian-experience question and
    immediately continue the cold-start flow.

    Expected JSON:

        {
            "prior_russian_experience": true
        }

    or:

        {
            "prior_russian_experience": false
        }
    """

    # --------------------------------------------------------
    # Content-Type
    # --------------------------------------------------------

    if (
        request.content_type
        != "application/json"
    ):
        return JsonResponse(
            {
                "error": (
                    "unsupported_media_type"
                ),
                "detail": (
                    "Content-Type must be "
                    "application/json."
                ),
            },
            status=415,
        )

    # --------------------------------------------------------
    # Parse JSON
    # --------------------------------------------------------

    try:
        payload = json.loads(
            request.body.decode(
                "utf-8"
            )
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return JsonResponse(
            {
                "error": (
                    "invalid_json"
                ),
                "detail": (
                    "Request body must contain "
                    "valid JSON."
                ),
            },
            status=400,
        )

    # --------------------------------------------------------
    # JSON object required
    # --------------------------------------------------------

    if not isinstance(
        payload,
        dict,
    ):
        return JsonResponse(
            {
                "error": (
                    "invalid_payload"
                ),
                "detail": (
                    "JSON payload must be an object."
                ),
            },
            status=400,
        )

    # --------------------------------------------------------
    # Required field
    # --------------------------------------------------------

    if (
        "prior_russian_experience"
        not in payload
    ):
        return JsonResponse(
            {
                "error": (
                    "missing_field"
                ),
                "detail": (
                    "prior_russian_experience "
                    "is required."
                ),
            },
            status=400,
        )

    prior_russian_experience = (
        payload[
            "prior_russian_experience"
        ]
    )

    # --------------------------------------------------------
    # Exact bool validation
    #
    # bool is a subclass of int in Python,
    # therefore use exact type comparison.
    # --------------------------------------------------------

    if (
        type(
            prior_russian_experience
        )
        is not bool
    ):
        return JsonResponse(
            {
                "error": (
                    "invalid_field"
                ),
                "detail": (
                    "prior_russian_experience "
                    "must be true or false."
                ),
            },
            status=400,
        )

    # --------------------------------------------------------
    # Reject undeclared fields
    # --------------------------------------------------------

    extra_fields = (
        set(payload)
        - {
            "prior_russian_experience",
        }
    )

    if extra_fields:
        return JsonResponse(
            {
                "error": (
                    "unexpected_fields"
                ),
                "detail": (
                    "Unexpected field(s): "
                    + ", ".join(
                        sorted(
                            extra_fields
                        )
                    )
                ),
            },
            status=400,
        )

    # --------------------------------------------------------
    # Canonical application service
    # --------------------------------------------------------

    try:
        result = (
            submit_prior_russian_experience(
                user=request.user,
                prior_russian_experience=(
                    prior_russian_experience
                ),
            )
        )

    except LearningServiceError as exc:
        return JsonResponse(
            {
                "error": (
                    "onboarding_conflict"
                ),
                "detail": (
                    str(exc)
                ),
            },
            status=409,
        )

    response_payload = {
        "route": (
            result.route.value
        ),

        "prior_russian_experience": (
            result.prior_russian_experience
        ),

        "active_assessment": None,

        "beginner_lesson": None,
    }

    # --------------------------------------------------------
    # Experienced learner
    #
    # Diagnostic has already been created and fully
    # assembled by the application service.
    # --------------------------------------------------------

    if (
        result.assessment_session_id
        is not None
    ):
        response_payload[
            "active_assessment"
        ] = {
            "session_id": (
                result.assessment_session_id
            ),

            "mode": (
                "diagnostic"
            ),

            "question_count": (
                result.question_count
            ),
        }

    # --------------------------------------------------------
    # True beginner
    # --------------------------------------------------------

    if (
        result.route
        == LearnerEntryRoute.BEGINNER_PATH
    ):
        response_payload[
            "beginner_lesson"
        ] = _serialize_beginner_lesson(
            user=request.user,
        )

    return JsonResponse(
        response_payload,
        status=200,
    )


# ============================================================
# 9E.3A
# Lesson Detail API
# ============================================================


@login_required
@require_GET
def lesson_detail_view(
    request: HttpRequest,
    lesson_id: int,
):
    """
    Return one active Lesson and the authenticated
    learner's existing LessonProgress, if any.

    Read-only:
    this endpoint never creates or updates
    LessonProgress.
    """

    lesson = get_object_or_404(
        Lesson.objects.prefetch_related(
            "lesson_skills__skill",
        ),
        pk=lesson_id,
        is_active=True,
    )

    return JsonResponse(
        _serialize_lesson_detail(
            lesson=lesson,
            user=request.user,
        ),
        status=200,
    )


# ============================================================
# 9E.3B
# Start / Resume Lesson API
# ============================================================


@login_required
@require_POST
def start_or_resume_lesson_view(
    request: HttpRequest,
    lesson_id: int,
):
    """
    Record a real learner opening of an active
    Lesson.

    First open:
        create IN_PROGRESS LessonProgress.

    Reopen IN_PROGRESS:
        reuse the same LessonProgress and update
        last_accessed_at.

    Reopen COMPLETED:
        keep COMPLETED state unchanged and update
        last_accessed_at only.
    """

    # Keep inactive / nonexistent Lesson behavior
    # consistent with GET lesson detail:
    #
    # both return HTTP 404.
    lesson = get_object_or_404(
        Lesson,
        pk=lesson_id,
        is_active=True,
    )

    try:
        result = start_or_resume_lesson(
            user=request.user,
            lesson_id=lesson.pk,
        )

    except LearningServiceError as exc:
        return JsonResponse(
            {
                "error": (
                    "lesson_progress_conflict"
                ),
                "detail": (
                    str(exc)
                ),
            },
            status=409,
        )

    progress = (
        LessonProgress.objects
        .get(
            pk=result.lesson_progress_id,
            user=request.user,
            lesson=lesson,
        )
    )

    return JsonResponse(
        {
            "action": (
                result.action.value
            ),

            "lesson_id": (
                lesson.pk
            ),

            "progress": (
                _serialize_lesson_progress(
                    progress
                )
            ),
        },
        status=200,
    )

@login_required
@require_http_methods(
    [
        "PATCH",
    ]
)
def update_lesson_progress_view(
    request: HttpRequest,
    lesson_id: int,
):
    """
    Update completion_ratio for an active,
    already-started Lesson.

    This endpoint never completes a Lesson.
    """

    lesson = get_object_or_404(
        Lesson,
        pk=lesson_id,
        is_active=True,
    )

    if (
        request.content_type
        != "application/json"
    ):
        return JsonResponse(
            {
                "error": (
                    "unsupported_media_type"
                ),
                "detail": (
                    "Content-Type must be "
                    "application/json."
                ),
            },
            status=415,
        )

    try:
        payload = json.loads(
            request.body.decode(
                "utf-8"
            )
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return JsonResponse(
            {
                "error": "invalid_json",
                "detail": (
                    "Request body must contain "
                    "valid JSON."
                ),
            },
            status=400,
        )

    if not isinstance(
        payload,
        dict,
    ):
        return JsonResponse(
            {
                "error": "invalid_payload",
                "detail": (
                    "JSON payload must be an object."
                ),
            },
            status=400,
        )

    if (
        set(payload)
        != {
            "completion_ratio",
        }
    ):
        return JsonResponse(
            {
                "error": "invalid_fields",
                "detail": (
                    "Payload must contain exactly "
                    "completion_ratio."
                ),
            },
            status=400,
        )

    completion_ratio = (
        _parse_completion_ratio(
            payload[
                "completion_ratio"
            ]
        )
    )

    if completion_ratio is None:
        return JsonResponse(
            {
                "error": "invalid_completion_ratio",
                "detail": (
                    "completion_ratio must be a "
                    "finite number from 0 inclusive "
                    "to 1 exclusive with at most "
                    "four decimal places."
                ),
            },
            status=400,
        )

    try:
        result = update_lesson_progress(
            user=request.user,
            lesson_id=lesson.pk,
            completion_ratio=(
                completion_ratio
            ),
        )

    except LearningServiceError as exc:
        return JsonResponse(
            {
                "error": (
                    "lesson_progress_conflict"
                ),
                "detail": str(exc),
            },
            status=409,
        )

    progress = (
        LessonProgress.objects
        .get(
            pk=result.lesson_progress_id,
            user=request.user,
            lesson=lesson,
        )
    )

    return JsonResponse(
        {
            "action": result.action.value,

            "lesson_id": lesson.pk,

            "progress": (
                _serialize_lesson_progress(
                    progress
                )
            ),
        },
        status=200,
    )

@login_required
@require_POST
def complete_lesson_view(
    request: HttpRequest,
    lesson_id: int,
):
    """
    Complete an already-started active Lesson.

    Repeated calls are idempotent.
    """

    lesson = get_object_or_404(
        Lesson,
        pk=lesson_id,
        is_active=True,
    )

    try:
        result = complete_lesson(
            user=request.user,
            lesson_id=lesson.pk,
        )

    except LearningServiceError as exc:
        return JsonResponse(
            {
                "error": (
                    "lesson_progress_conflict"
                ),
                "detail": str(exc),
            },
            status=409,
        )

    progress = (
        LessonProgress.objects
        .get(
            pk=result.lesson_progress_id,
            user=request.user,
            lesson=lesson,
        )
    )

    return JsonResponse(
        {
            "action": result.action.value,

            "lesson_id": lesson.pk,

            "progress": (
                _serialize_lesson_progress(
                    progress
                )
            ),
        },
        status=200,
    )

# 9E.4A : Active Assessment Detail API
@login_required
@require_GET
def active_assessment_detail_view(
    request: HttpRequest,
    session_id: int,
):
    """
    Return one ACTIVE assessment belonging to the
    authenticated learner.

    This endpoint is strictly read-only.

    Completed assessment results belong to the
    separate result API and are not exposed here.
    """

    session = get_object_or_404(
        AssessmentSession,
        pk=session_id,
        user=request.user,
        status=(
            AssessmentSession.Status.ACTIVE
        ),
    )

    return JsonResponse(
        _serialize_active_assessment(
            session=session,
        ),
        status=200,
    )

# ============================================================
# 9E.4B
# Assessment Question Viewed API
# ============================================================


@login_required
@require_POST
def assessment_question_viewed_view(
    request: HttpRequest,
    session_id: int,
    session_question_id: int,
):
    """
    Mark one assessment question as viewed.

    Idempotent:
    repeated calls preserve the original
    first_viewed_at timestamp.
    """

    # --------------------------------------------------------
    # Resource ownership / route consistency
    #
    # A SessionQuestion from another session or another
    # learner must behave as not found.
    # --------------------------------------------------------

    get_object_or_404(
        SessionQuestion,
        pk=session_question_id,
        session_id=session_id,
        session__user=request.user,
    )

    try:
        result = mark_session_question_viewed(
            user=request.user,
            session_question_id=(
                session_question_id
            ),
        )

    except LearningServiceError as exc:
        return JsonResponse(
            {
                "error": (
                    "assessment_state_conflict"
                ),
                "detail": (
                    str(exc)
                ),
            },
            status=409,
        )

    return JsonResponse(
        {
            "session_id": (
                result.session_id
            ),

            "session_question_id": (
                result.session_question_id
            ),

            "first_viewed_at": (
                result.first_viewed_at.isoformat()
                if result.first_viewed_at
                is not None
                else None
            ),
        },
        status=200,
    )

@login_required
@require_POST
def assessment_question_active_time_view(
    request: HttpRequest,
    session_id: int,
    session_question_id: int,
):
    """
    Add active learner viewing time to one
    assessment question.

    Expected JSON:

        {
            "delta_ms": 3500
        }

    active_response_time_ms is cumulative.
    Recording active time also guarantees that
    the question is considered viewed.
    """

    get_object_or_404(
        SessionQuestion,
        pk=session_question_id,
        session_id=session_id,
        session__user=request.user,
    )

    # --------------------------------------------------------
    # Content-Type
    # --------------------------------------------------------

    if (
        request.content_type
        != "application/json"
    ):
        return JsonResponse(
            {
                "error": (
                    "unsupported_media_type"
                ),
                "detail": (
                    "Content-Type must be "
                    "application/json."
                ),
            },
            status=415,
        )

    # --------------------------------------------------------
    # Parse JSON
    # --------------------------------------------------------

    try:
        payload = json.loads(
            request.body.decode(
                "utf-8"
            )
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return JsonResponse(
            {
                "error": (
                    "invalid_json"
                ),
                "detail": (
                    "Request body must contain "
                    "valid JSON."
                ),
            },
            status=400,
        )

    if not isinstance(
        payload,
        dict,
    ):
        return JsonResponse(
            {
                "error": (
                    "invalid_payload"
                ),
                "detail": (
                    "JSON payload must be an object."
                ),
            },
            status=400,
        )

    # Exactly one field in v1.
    if (
        set(payload)
        != {
            "delta_ms",
        }
    ):
        return JsonResponse(
            {
                "error": (
                    "invalid_fields"
                ),
                "detail": (
                    "Payload must contain exactly "
                    "delta_ms."
                ),
            },
            status=400,
        )

    delta_ms = payload[
        "delta_ms"
    ]

    # bool is a subclass of int in Python,
    # therefore reject it explicitly.
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
        return JsonResponse(
            {
                "error": (
                    "invalid_delta_ms"
                ),
                "detail": (
                    "delta_ms must be a "
                    "non-negative integer."
                ),
            },
            status=400,
        )

    try:
        result = (
            record_session_question_active_time(
                user=request.user,
                session_question_id=(
                    session_question_id
                ),
                delta_ms=delta_ms,
            )
        )

    except LearningServiceError as exc:
        return JsonResponse(
            {
                "error": (
                    "assessment_state_conflict"
                ),
                "detail": (
                    str(exc)
                ),
            },
            status=409,
        )

    return JsonResponse(
        {
            "session_id": (
                result.session_id
            ),

            "session_question_id": (
                result.session_question_id
            ),

            "active_response_time_ms": (
                result.active_response_time_ms
            ),

            "first_viewed_at": (
                result.first_viewed_at.isoformat()
                if result.first_viewed_at
                is not None
                else None
            ),
        },
        status=200,
    )

# ============================================================
# 9E.4C
# Save / Revise Assessment Answer API
# ============================================================


@login_required
@require_POST
def assessment_question_answer_view(
    request: HttpRequest,
    session_id: int,
    session_question_id: int,
):
    """
    Save one answer revision for an ACTIVE
    assessment question.

    Every successful POST creates a new
    QuestionAttempt.

    SECURITY:
    correctness is stored internally by the
    service but is never exposed while the
    assessment remains ACTIVE.
    """

    # --------------------------------------------------------
    # Ownership + URL consistency
    # --------------------------------------------------------

    session_question = get_object_or_404(
        SessionQuestion.objects.select_related(
            "session",
            "question",
        ),
        pk=session_question_id,
        session_id=session_id,
        session__user=request.user,
    )

    # --------------------------------------------------------
    # Content-Type
    # --------------------------------------------------------

    if (
        request.content_type
        != "application/json"
    ):
        return JsonResponse(
            {
                "error": (
                    "unsupported_media_type"
                ),
                "detail": (
                    "Content-Type must be "
                    "application/json."
                ),
            },
            status=415,
        )

    # --------------------------------------------------------
    # Parse JSON
    # --------------------------------------------------------

    try:
        payload = json.loads(
            request.body.decode(
                "utf-8"
            )
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return JsonResponse(
            {
                "error": "invalid_json",
                "detail": (
                    "Request body must contain "
                    "valid JSON."
                ),
            },
            status=400,
        )

    if not isinstance(
        payload,
        dict,
    ):
        return JsonResponse(
            {
                "error": "invalid_payload",
                "detail": (
                    "JSON payload must be an object."
                ),
            },
            status=400,
        )

    # --------------------------------------------------------
    # Allowed fields
    # --------------------------------------------------------

    allowed_fields = {
        "selected_option_key",
        "response_time_ms",
    }

    extra_fields = (
        set(payload)
        - allowed_fields
    )

    if extra_fields:
        return JsonResponse(
            {
                "error": "unexpected_fields",
                "detail": (
                    "Unexpected field(s): "
                    + ", ".join(
                        sorted(
                            extra_fields
                        )
                    )
                ),
            },
            status=400,
        )

    # --------------------------------------------------------
    # selected_option_key
    # --------------------------------------------------------

    if (
        "selected_option_key"
        not in payload
    ):
        return JsonResponse(
            {
                "error": "missing_field",
                "detail": (
                    "selected_option_key "
                    "is required."
                ),
            },
            status=400,
        )

    selected_option_key = (
        payload[
            "selected_option_key"
        ]
    )

    if (
        not isinstance(
            selected_option_key,
            str,
        )
        or not selected_option_key.strip()
    ):
        return JsonResponse(
            {
                "error": (
                    "invalid_selected_option_key"
                ),
                "detail": (
                    "selected_option_key must be "
                    "a non-empty string."
                ),
            },
            status=400,
        )

    # Keep the normalized value deterministic.
    selected_option_key = (
        selected_option_key.strip()
    )

    # --------------------------------------------------------
    # response_time_ms
    # --------------------------------------------------------

    response_time_ms = payload.get(
        "response_time_ms"
    )

    if (
        response_time_ms is not None
        and (
            isinstance(
                response_time_ms,
                bool,
            )
            or not isinstance(
                response_time_ms,
                int,
            )
            or response_time_ms < 0
        )
    ):
        return JsonResponse(
            {
                "error": (
                    "invalid_response_time_ms"
                ),
                "detail": (
                    "response_time_ms must be "
                    "a non-negative integer or null."
                ),
            },
            status=400,
        )

    # --------------------------------------------------------
    # Canonical service
    #
    # The service:
    # - checks ACTIVE assessment;
    # - validates option membership;
    # - grades internally;
    # - marks question viewed if needed;
    # - creates a new QuestionAttempt.
    # --------------------------------------------------------

    try:
        result = save_option_answer(
            user=request.user,
            session_question_id=(
                session_question.pk
            ),
            selected_option_key=(
                selected_option_key
            ),
            response_time_ms=(
                response_time_ms
            ),
        )

    except LearningServiceError as exc:
        return JsonResponse(
            {
                "error": (
                    "answer_submission_conflict"
                ),
                "detail": str(exc),
            },
            status=409,
        )

    # --------------------------------------------------------
    # Explicit safe response whitelist.
    #
    # DO NOT add:
    # - is_correct
    # - correct_answer
    # - accepted_answers
    # - explanation
    # --------------------------------------------------------

    return JsonResponse(
        {
            "session_id": (
                result.session_id
            ),

            "session_question_id": (
                result.session_question_id
            ),

            "question_id": (
                result.question_id
            ),

            "source_id": (
                result.source_id
            ),

            "sequence_number": (
                result.sequence_number
            ),

            "attempt": {
                "attempt_id": (
                    result.attempt_id
                ),

                "attempt_number": (
                    result.attempt_number
                ),

                "selected_option_key": (
                    result.selected_option_key
                ),
            },
        },
        status=200,
    )

# ============================================================
# 9E.4D
# Complete / Abandon Assessment API
# ============================================================


@login_required
@require_POST
def complete_assessment_view(
    request: HttpRequest,
    session_id: int,
):
    """
    Complete one assessment belonging to the
    authenticated learner.

    Completing:
    - closes the ACTIVE AssessmentSession;
    - sets ended_at;
    - triggers canonical mastery recalculation
      through complete_session().

    Result feedback is deliberately NOT exposed
    by this endpoint.
    """

    # Ownership check.
    #
    # Do not filter by ACTIVE here:
    # an owned but already-finished assessment
    # should become a state conflict (409),
    # not a resource-not-found (404).
    get_object_or_404(
        AssessmentSession,
        pk=session_id,
        user=request.user,
    )

    try:
        result = complete_session(
            user=request.user,
            session_id=session_id,
        )

    except LearningServiceError as exc:
        return JsonResponse(
            {
                "error": (
                    "assessment_state_conflict"
                ),
                "detail": str(exc),
            },
            status=409,
        )

    session = AssessmentSession.objects.get(
        pk=result.session_id,
        user=request.user,
    )

    return JsonResponse(
        {
            "action": "completed",
            "assessment": (
                _serialize_finished_assessment(
                    session
                )
            ),
        },
        status=200,
    )

@login_required
@require_POST
def abandon_assessment_view(
    request: HttpRequest,
    session_id: int,
):
    """
    Abandon one assessment belonging to the
    authenticated learner.

    Abandoned assessments are terminal but do
    not become finalized mastery evidence.
    """

    get_object_or_404(
        AssessmentSession,
        pk=session_id,
        user=request.user,
    )

    try:
        result = abandon_session(
            user=request.user,
            session_id=session_id,
        )

    except LearningServiceError as exc:
        return JsonResponse(
            {
                "error": (
                    "assessment_state_conflict"
                ),
                "detail": str(exc),
            },
            status=409,
        )

    session = AssessmentSession.objects.get(
        pk=result.session_id,
        user=request.user,
    )

    return JsonResponse(
        {
            "action": "abandoned",
            "assessment": (
                _serialize_finished_assessment(
                    session
                )
            ),
        },
        status=200,
    )

# ============================================================
# 9E.5A
# Completed Assessment Result API
# ============================================================


@login_required
@require_GET
def assessment_result_view(
    request: HttpRequest,
    session_id: int,
):
    """
    Return the historical result of one COMPLETED
    assessment belonging to the authenticated user.

    ACTIVE and ABANDONED sessions do not have
    completed assessment results.
    """

    # Ownership / existence:
    # other user's assessment behaves as 404.
    session = get_object_or_404(
        AssessmentSession,
        pk=session_id,
        user=request.user,
    )

    try:
        payload = (
            build_completed_assessment_result(
                session=session,
            )
        )

    except AssessmentResultError as exc:
        return JsonResponse(
            {
                "error": (
                    "assessment_result_unavailable"
                ),
                "detail": str(exc),
            },
            status=409,
        )

    payload[
        "current_mastery"
    ] = build_current_mastery_projection(
        user=request.user,
        affected_skills=(
            payload[
                "affected_skills"
            ]
        ),
    )

    payload[
        "next_action"
    ] = _serialize_next_action(
        user=request.user,
    )

    return JsonResponse(
        payload,
        status=200,
    )