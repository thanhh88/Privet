from dataclasses import dataclass
from datetime import datetime
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from enum import Enum

from learning_engine.models import (
    AssessmentSession,
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
from learning_engine.mastery import (
    calculate_mastery,
    collect_skill_evidence,
)
from learning_engine.question_selection import (
    build_question_candidates,
    select_top_question_candidate,
)
from learning_engine.adaptive_assembly import (
    ADAPTIVE_ASSEMBLY_RULE_VERSION,
    TARGET_QUESTION_COUNT,
    plan_adaptive_assessment,
)
from learning_engine.adaptive_preparation import (
    prepare_adaptive_assessment_candidates,
)
from learning_engine.diagnostic_blueprint import (
    DIAGNOSTIC_RULE_VERSION,
    DIAGNOSTIC_TARGET_QUESTION_COUNT,
)
from learning_engine.diagnostic_selection import (
    plan_diagnostic_questions,
)
from decimal import Decimal
from django.contrib.auth import get_user_model

from learning_engine.cold_start import (
    LearnerEntryRoute,
)
from learning_engine.learner_entry import (
    resolve_learner_entry_state,
)
from curriculum.models import (
    Lesson,
    LessonProgress,
)

class LearningServiceError(ValueError):
    pass

@dataclass(frozen=True)
class PriorRussianExperienceSubmissionResult:
    user_id: int

    prior_russian_experience: bool

    route: LearnerEntryRoute

    assessment_session_id: int | None

    question_count: int

class LessonProgressOpenAction(str, Enum):
    STARTED = "started"
    RESUMED = "resumed"
    ALREADY_COMPLETED = "already_completed"


@dataclass(frozen=True)
class StartOrResumeLessonResult:
    action: LessonProgressOpenAction
    lesson_id: int
    lesson_progress_id: int

@dataclass(frozen=True)
class SaveAnswerResult:
    attempt_id: int
    session_question_id: int
    session_id: int
    question_id: int
    source_id: str

    sequence_number: int
    attempt_number: int

    selected_option_key: str

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
class AssessmentSessionResult:
    session_id: int
    mode: str
    status: str

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

    return (
        bool(selected_option.is_correct),
        selected_option_key,
    )

def _build_question_skill_snapshot(
    question,
):
    """
    Freeze the Question -> Skill mappings used by one
    assessment item.

    Historical mastery must use this snapshot rather than
    the current QuestionSkill rows from the Question Bank.
    """

    skill_links = list(
        QuestionSkill.objects
        .filter(
            question=question,
        )
        .select_related(
            "skill",
        )
        .order_by(
            "-is_primary",
            "skill_id",
        )
    )

    if not skill_links:
        raise LearningServiceError(
            f"Question {question.source_id!r} "
            "has no skill mappings and cannot be "
            "added to an assessment."
        )

    return [
        {
            "skill_id": link.skill_id,
            "skill_code": link.skill.skill_code,
            "is_primary": bool(
                link.is_primary
            ),
            # Store Decimal as a string so the JSON
            # representation remains exact.
            "weight": str(
                link.weight
            ),
        }
        for link in skill_links
    ]

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
            AssessmentSession.objects
            .select_for_update()
            .get(
                pk=session_id,
                user=user,
            )
        )
    except AssessmentSession.DoesNotExist as exc:
        raise LearningServiceError(
            "Assessment session does not exist "
            "or does not belong to this user."
        ) from exc

    if session.status != (
        AssessmentSession.Status.ACTIVE
    ):
        raise LearningServiceError(
            "Questions can only be added "
            "to an active assessment session."
        )

    if session.mode not in {
        AssessmentSession.Mode.ADAPTIVE,
        AssessmentSession.Mode.DIAGNOSTIC,
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

    skill_snapshot = (
        _build_question_skill_snapshot(
            question
        )
    )

    session_question = SessionQuestion(
        session=session,
        question=question,

        difficulty_score_snapshot=(
            question.difficulty_score
        ),

        skill_snapshot=skill_snapshot,

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
def select_and_add_adaptive_question(
    *,
    user,
    session_id,
    skill_id,
    as_of=None,
):
    """
    Select and add one adaptive Question for a
    previously selected Skill.

    Selection pipeline:
    1. Build DB-eligible candidates.
    2. Calculate current mastery estimate M-hat
       from finalized historical assessment evidence.
    3. Use M-hat as target difficulty.
    4. Rank candidates using the canonical
       question-selection policy.
    5. Add the highest-ranked Question to the
       active adaptive assessment.

    Returns SessionQuestionResult.

    Returns None when no eligible Question remains.
    """

    if as_of is None:
        as_of = timezone.now()

    try:
        candidates = build_question_candidates(
            user=user,
            session_id=session_id,
            skill_id=skill_id,
        )
    except ValueError as exc:
        raise LearningServiceError(
            str(exc)
        ) from exc

    if not candidates:
        return None

    # Only COMPLETED assessment history contributes.
    #
    # Therefore answers saved in the currently active
    # assessment cannot change the target difficulty
    # of later questions in that same assessment.
    evidence = collect_skill_evidence(
        user=user,
        skill_id=skill_id,
    )

    mastery = calculate_mastery(
        evidence,
        as_of=as_of,
    )

    selected = select_top_question_candidate(
        candidates=candidates,
        target_difficulty=(
            mastery.mastery_estimate
        ),
    )

    if selected is None:
        return None

    return add_question_to_session(
        user=user,
        session_id=session_id,
        question_id=selected.question_id,
    )

@transaction.atomic
def assemble_adaptive_assessment(
    *,
    user,
    session_id,
    eligible_skill_ids,
    as_of=None,
):
    """
    Atomically assemble one complete adaptive
    assessment.

    Canonical adaptive assembly v1:
    - session must belong to user;
    - session must be ACTIVE + ADAPTIVE;
    - session must contain zero Questions;
    - one frozen as_of is used for the whole build;
    - Skill Priority is prepared once;
    - Question rankings are prepared once;
    - pure planner produces exactly 10 slots;
    - all 10 SessionQuestions are persisted;
    - any failure rolls the whole assembly back.
    """

    if user.pk is None:
        raise LearningServiceError(
            "User must be saved before an adaptive "
            "assessment can be assembled."
        )

    if as_of is None:
        as_of = timezone.now()

    eligible_skill_ids = list(
        eligible_skill_ids
    )

    # -----------------------------------------
    # Lock the target AssessmentSession.
    #
    # This prevents two concurrent assembly
    # requests from both observing an empty
    # session and trying to populate it.
    # -----------------------------------------

    try:
        session = (
            AssessmentSession.objects
            .select_for_update()
            .get(
                pk=session_id,
                user=user,
            )
        )
    except AssessmentSession.DoesNotExist as exc:
        raise LearningServiceError(
            "Assessment session does not exist "
            "or does not belong to this user."
        ) from exc

    if session.status != AssessmentSession.Status.ACTIVE:
        raise LearningServiceError(
            "Adaptive assessment assembly requires "
            "an active assessment session."
        )

    if session.mode != AssessmentSession.Mode.ADAPTIVE:
        raise LearningServiceError(
            "Adaptive assessment assembly requires "
            "adaptive mode."
        )

    # Canonical 9D.10 policy:
    #
    # assembly starts only from a completely empty
    # assessment. Resume never calls this function;
    # it simply reloads the existing SessionQuestions.
    if session.session_questions.exists():
        raise LearningServiceError(
            "Adaptive assessment assembly requires "
            "an empty assessment session."
        )

    # -----------------------------------------
    # DB preparation + pure planning
    # -----------------------------------------

    try:
        prepared = (
            prepare_adaptive_assessment_candidates(
                user=user,
                session_id=session.pk,
                eligible_skill_ids=(
                    eligible_skill_ids
                ),
                as_of=as_of,
            )
        )

        plan = plan_adaptive_assessment(
            skill_candidates=prepared,
        )

    except ValueError as exc:
        raise LearningServiceError(
            f"Adaptive assessment could not be "
            f"assembled: {exc}"
        ) from exc

    if len(plan) != TARGET_QUESTION_COUNT:
        raise LearningServiceError(
            "Adaptive assessment planner did not "
            "produce the required number of "
            "Questions."
        )

    # -----------------------------------------
    # Persist the complete plan
    # -----------------------------------------

    written_slots = []

    for slot in plan:
        added = add_question_to_session(
            user=user,
            session_id=session.pk,
            question_id=slot.question_id,
        )

        # Because assembly began from an empty,
        # locked session, planner slot numbering
        # and DB sequence numbering must match.
        if (
            added.sequence_number
            != slot.slot_number
        ):
            raise LearningServiceError(
                "Adaptive assessment sequence "
                "integrity check failed."
            )

        written_slots.append(
            AdaptiveAssessmentAssemblySlotResult(
                slot_number=slot.slot_number,

                focus_skill_id=(
                    slot.focus_skill_id
                ),

                focus_skill_code=(
                    slot.focus_skill_code
                ),

                session_question_id=(
                    added.session_question_id
                ),

                question_id=(
                    added.question_id
                ),

                source_id=(
                    added.source_id
                ),
            )
        )

    return AdaptiveAssessmentAssemblyResult(
        session_id=session.pk,

        question_count=len(
            written_slots
        ),

        rule_version=(
            ADAPTIVE_ASSEMBLY_RULE_VERSION
        ),

        slots=tuple(
            written_slots
        ),
    )

@transaction.atomic
def assemble_diagnostic_assessment(
    *,
    user,
    session_id,
):
    """
    Atomically assemble the canonical initial
    diagnostic assessment.

    Diagnostic v1:
    - session belongs to user;
    - session is ACTIVE + DIAGNOSTIC;
    - session must initially be empty;
    - frozen 15-slot diagnostic blueprint;
    - planning is deterministic;
    - all 15 SessionQuestions are persisted;
    - any failure rolls the entire assembly back.
    """

    if user.pk is None:
        raise LearningServiceError(
            "User must be saved before a diagnostic "
            "assessment can be assembled."
        )

    # -----------------------------------------
    # Lock target session.
    #
    # Two concurrent requests must not both see
    # the same diagnostic session as empty.
    # -----------------------------------------

    try:
        session = (
            AssessmentSession.objects
            .select_for_update()
            .get(
                pk=session_id,
                user=user,
            )
        )

    except AssessmentSession.DoesNotExist as exc:
        raise LearningServiceError(
            "Assessment session does not exist "
            "or does not belong to this user."
        ) from exc

    if (
        session.status
        != AssessmentSession.Status.ACTIVE
    ):
        raise LearningServiceError(
            "Diagnostic assessment assembly "
            "requires an active assessment session."
        )

    if (
        session.mode
        != AssessmentSession.Mode.DIAGNOSTIC
    ):
        raise LearningServiceError(
            "Diagnostic assessment assembly "
            "requires diagnostic mode."
        )

    # Diagnostic is assembled once as a complete
    # 15-question unit.
    #
    # Resume must reload existing SessionQuestions,
    # not call assembly again.
    if session.session_questions.exists():
        raise LearningServiceError(
            "Diagnostic assessment assembly "
            "requires an empty assessment session."
        )

    # -----------------------------------------
    # Read-only deterministic planning
    # -----------------------------------------

    try:
        plan = plan_diagnostic_questions(
            user=user,
            session_id=session.pk,
        )

    except ValueError as exc:
        raise LearningServiceError(
            f"Diagnostic assessment could not be "
            f"assembled: {exc}"
        ) from exc

    if (
        len(plan)
        != DIAGNOSTIC_TARGET_QUESTION_COUNT
    ):
        raise LearningServiceError(
            "Diagnostic planner did not produce "
            "the required number of Questions."
        )

    # -----------------------------------------
    # Persist complete diagnostic
    # -----------------------------------------

    written_slots = []

    for slot in plan:
        added = add_question_to_session(
            user=user,
            session_id=session.pk,
            question_id=slot.question_id,
        )

        # Empty locked session means the DB
        # sequence must exactly equal blueprint
        # slot numbering.
        if (
            added.sequence_number
            != slot.slot_number
        ):
            raise LearningServiceError(
                "Diagnostic assessment sequence "
                "integrity check failed."
            )

        written_slots.append(
            DiagnosticAssessmentAssemblySlotResult(
                slot_number=(
                    slot.slot_number
                ),

                round_number=(
                    slot.round_number
                ),

                focus_skill_id=(
                    slot.skill_id
                ),

                focus_skill_code=(
                    slot.skill_code
                ),

                target_difficulty=(
                    slot.target_difficulty
                ),

                question_difficulty=(
                    slot.question_difficulty
                ),

                session_question_id=(
                    added.session_question_id
                ),

                question_id=(
                    added.question_id
                ),

                source_id=(
                    added.source_id
                ),
            )
        )

    if (
        len(written_slots)
        != DIAGNOSTIC_TARGET_QUESTION_COUNT
    ):
        raise LearningServiceError(
            "Diagnostic assessment write did not "
            "produce exactly 15 Questions."
        )

    return DiagnosticAssessmentAssemblyResult(
        session_id=session.pk,

        question_count=len(
            written_slots
        ),

        rule_version=(
            DIAGNOSTIC_RULE_VERSION
        ),

        slots=tuple(
            written_slots
        ),
    )

@transaction.atomic
def submit_prior_russian_experience(
    *,
    user,
    prior_russian_experience,
):
    """
    Persist the learner's onboarding answer and
    immediately continue the canonical cold-start
    flow.

    False
        -> persist beginner status
        -> BEGINNER_PATH
        -> no assessment is created

    True
        -> persist prior-study status
        -> create DIAGNOSTIC assessment
        -> atomically assemble all 15 Questions
        -> RESUME_DIAGNOSTIC

    The onboarding answer may only be submitted
    while the learner is genuinely in
    ASK_PRIOR_EXPERIENCE state.

    Any failure rolls back the entire operation.
    """

    if user.pk is None:
        raise LearningServiceError(
            "User must be saved before onboarding "
            "experience can be submitted."
        )

    # bool must be exact.
    #
    # Reject:
    # 1
    # 0
    # "yes"
    # None
    if type(
        prior_russian_experience
    ) is not bool:
        raise LearningServiceError(
            "prior_russian_experience must be bool."
        )

    UserModel = get_user_model()

    # =========================================
    # Lock learner row.
    #
    # Two onboarding submissions for the same
    # learner must not independently create two
    # diagnostic assessments.
    # =========================================

    try:
        locked_user = (
            UserModel.objects
            .select_for_update()
            .get(
                pk=user.pk,
            )
        )

    except UserModel.DoesNotExist as exc:
        raise LearningServiceError(
            "User does not exist."
        ) from exc

    # =========================================
    # Verify onboarding is still applicable.
    # =========================================

    try:
        current_state = (
            resolve_learner_entry_state(
                user=locked_user,
            )
        )

    except ValueError as exc:
        raise LearningServiceError(
            f"Learner entry state could not be "
            f"resolved: {exc}"
        ) from exc

    if (
        current_state.route
        != LearnerEntryRoute.ASK_PRIOR_EXPERIENCE
    ):
        raise LearningServiceError(
            "Prior Russian experience may only be "
            "submitted during initial onboarding."
        )

    # =========================================
    # Persist authoritative onboarding answer.
    # =========================================

    locked_user.prior_russian_experience = (
        prior_russian_experience
    )

    locked_user.save(
        update_fields=[
            "prior_russian_experience",
        ]
    )

    # =========================================
    # True beginner
    # =========================================

    if not prior_russian_experience:
        return (
            PriorRussianExperienceSubmissionResult(
                user_id=locked_user.pk,

                prior_russian_experience=False,

                route=(
                    LearnerEntryRoute.BEGINNER_PATH
                ),

                assessment_session_id=None,

                question_count=0,
            )
        )

    # =========================================
    # Prior Russian study
    #
    # Start and fully assemble diagnostic.
    # =========================================

    started = start_session(
        user=locked_user,
        mode=AssessmentSession.Mode.DIAGNOSTIC,
    )

    assembled = assemble_diagnostic_assessment(
        user=locked_user,
        session_id=started.session_id,
    )

    # Defensive integration invariants.
    if (
        assembled.session_id
        != started.session_id
    ):
        raise LearningServiceError(
            "Diagnostic assembly returned an "
            "unexpected assessment session."
        )

    if (
        assembled.question_count
        != DIAGNOSTIC_TARGET_QUESTION_COUNT
    ):
        raise LearningServiceError(
            "Diagnostic onboarding must assemble "
            "exactly 15 Questions."
        )

    return (
        PriorRussianExperienceSubmissionResult(
            user_id=locked_user.pk,

            prior_russian_experience=True,

            # The assessment already exists now,
            # therefore the authoritative next
            # route is resume/open that assessment.
            route=(
                LearnerEntryRoute.RESUME_DIAGNOSTIC
            ),

            assessment_session_id=(
                started.session_id
            ),

            question_count=(
                assembled.question_count
            ),
        )
    )

@dataclass(frozen=True)
class DiagnosticAssessmentAssemblySlotResult:
    slot_number: int
    round_number: int

    focus_skill_id: int
    focus_skill_code: str

    target_difficulty: Decimal
    question_difficulty: Decimal

    session_question_id: int

    question_id: int
    source_id: str


@dataclass(frozen=True)
class DiagnosticAssessmentAssemblyResult:
    session_id: int

    question_count: int

    rule_version: str

    slots: tuple[
        DiagnosticAssessmentAssemblySlotResult,
        ...
    ]

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
        AssessmentSession.Status.ACTIVE
    ):
        raise LearningServiceError(
            "Only questions in an active "
            "session can be marked as viewed."
        )

    if session.mode not in {
        AssessmentSession.Mode.ADAPTIVE,
        AssessmentSession.Mode.DIAGNOSTIC,
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
        AssessmentSession.Status.ACTIVE
    ):
        raise LearningServiceError(
            "Active time can only be recorded "
            "for an active assessment session."
        )

    if session.mode not in {
        AssessmentSession.Mode.ADAPTIVE,
        AssessmentSession.Mode.DIAGNOSTIC,
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
):
    """
    Start a new assessment session.

    Assessment sessions support only:
    - adaptive
    - diagnostic

    Lesson and guided-practice activity belongs
    to tracking.StudySession, not to the
    assessment engine.
    """

    if mode not in AssessmentSession.Mode.values:
        raise LearningServiceError(
            f"Unsupported assessment mode: {mode!r}."
        )

    session = AssessmentSession(
        user=user,
        mode=mode,
        status=AssessmentSession.Status.ACTIVE,
        ended_at=None,
    )

    try:
        session.full_clean()
    except ValidationError as exc:
        raise LearningServiceError(
            f"Assessment session validation "
            f"failed: {exc}"
        ) from exc

    session.save()

    return AssessmentSessionResult(
        session_id=session.pk,
        mode=session.mode,
        status=session.status,
    )

def _get_active_session_for_update(
    *,
    user,
    session_id,
):
    try:
        session = (
            AssessmentSession.objects
            .select_for_update()
            .get(
                pk=session_id,
                user=user,
            )
        )
    except AssessmentSession.DoesNotExist as exc:
        raise LearningServiceError(
            "Assessment session does not exist "
            "or does not belong to this user."
        ) from exc

    if session.status != (
        AssessmentSession.Status.ACTIVE
    ):
        raise LearningServiceError(
            "Only an active assessment session "
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

    return AssessmentSessionResult(
        session_id=session.pk,
        mode=session.mode,
        status=session.status,
    )
def _affected_skill_ids_for_session(
    session_id,
):
    """
    Return every historical Skill represented by
    the assessment.

    Skill identity comes exclusively from the
    frozen SessionQuestion.skill_snapshot.

    Do not reconstruct historical assessment
    mappings from current QuestionSkill rows.
    """

    snapshots = (
        SessionQuestion.objects
        .filter(
            session_id=session_id,
        )
        .order_by(
            "sequence_number",
            "session_question_id",
        )
        .values_list(
            "skill_snapshot",
            flat=True,
        )
    )

    skill_ids = set()

    for snapshot in snapshots:
        for item in (
            snapshot
            or []
        ):
            skill_id = item.get(
                "skill_id"
            )

            if (
                isinstance(
                    skill_id,
                    bool,
                )
                or not isinstance(
                    skill_id,
                    int,
                )
            ):
                raise LearningServiceError(
                    "Assessment contains malformed "
                    "Skill snapshot data."
                )

            skill_ids.add(
                skill_id
            )

    return sorted(
        skill_ids
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
            AssessmentSession.Status.COMPLETED
        ),
    )

    # Guided lesson practice is intentionally
    # excluded from assessment mastery.
    if result.mode in {
        AssessmentSession.Mode.ADAPTIVE,
        AssessmentSession.Mode.DIAGNOSTIC,
    }:
        skill_ids = (
            _affected_skill_ids_for_session(
                result.session_id
            )
        )

        if skill_ids:
            completed_session = (
                AssessmentSession.objects
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
            AssessmentSession.Status.ABANDONED
        ),
    )

@transaction.atomic
def save_option_answer(
    *,
    user,
    session_question_id,
    selected_option_key,
    response_time_ms=None,
):
    """
       Save one option-answer revision for an active
       assessment question.

       Every save creates a new QuestionAttempt:

           attempt 1 -> first selected answer
           attempt 2 -> first revision
           attempt 3 -> another revision

       Correctness is calculated and stored internally
       as a server-side snapshot, but it is deliberately
       NOT exposed to the learner while the assessment
       is still active.

       Only the final attempt of each SessionQuestion in
       a COMPLETED assessment becomes mastery evidence.
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
        AssessmentSession.Status.ACTIVE
    ):
        raise LearningServiceError(
            "Answers can only be submitted "
            "to an active assessment session."
        )

    if session.mode not in {
        AssessmentSession.Mode.ADAPTIVE,
        AssessmentSession.Mode.DIAGNOSTIC,
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

    return SaveAnswerResult(
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
        selected_option_key=(
            attempt.selected_option_key
        ),
    )

@dataclass(frozen=True)
class AdaptiveAssessmentAssemblySlotResult:
    slot_number: int

    focus_skill_id: int
    focus_skill_code: str

    session_question_id: int

    question_id: int
    source_id: str


@dataclass(frozen=True)
class AdaptiveAssessmentAssemblyResult:
    session_id: int

    question_count: int

    rule_version: str

    slots: tuple[
        AdaptiveAssessmentAssemblySlotResult,
        ...
    ]

@transaction.atomic
def start_or_resume_lesson(
    *,
    user,
    lesson_id,
):
    """
    Start or reopen an active Lesson for a learner.

    Semantics:
    - no progress       -> create IN_PROGRESS;
    - IN_PROGRESS       -> resume same record;
    - COMPLETED         -> never reopen/reset it.

    Opening a Lesson updates last_accessed_at.

    The function never changes:
    - completion_ratio of an existing record;
    - completed_at;
    - COMPLETED back to IN_PROGRESS.
    """

    if user.pk is None:
        raise LearningServiceError(
            "User must be saved before starting "
            "a Lesson."
        )

    UserModel = get_user_model()

    # Serialize lesson-open writes for this user.
    #
    # This also prevents two simultaneous requests
    # from both observing "no LessonProgress" and
    # attempting to create separate records.
    try:
        persisted_user = (
            UserModel.objects
            .select_for_update()
            .get(
                pk=user.pk,
            )
        )
    except UserModel.DoesNotExist as exc:
        raise LearningServiceError(
            "User does not exist."
        ) from exc

    try:
        lesson = (
            Lesson.objects
            .select_for_update()
            .get(
                pk=lesson_id,
                is_active=True,
            )
        )
    except Lesson.DoesNotExist as exc:
        raise LearningServiceError(
            "Active Lesson does not exist."
        ) from exc

    now = timezone.now()

    progress = (
        LessonProgress.objects
        .select_for_update()
        .filter(
            user_id=persisted_user.pk,
            lesson_id=lesson.pk,
        )
        .first()
    )

    # =========================================
    # First real opening of this Lesson
    # =========================================

    if progress is None:
        progress = LessonProgress.objects.create(
            user_id=persisted_user.pk,
            lesson_id=lesson.pk,
            status=(
                LessonProgress.Status.IN_PROGRESS
            ),
            last_accessed_at=now,
        )

        action = (
            LessonProgressOpenAction.STARTED
        )

    # =========================================
    # Resume unfinished Lesson
    # =========================================

    elif (
        progress.status
        == LessonProgress.Status.IN_PROGRESS
    ):
        progress.last_accessed_at = now

        progress.save(
            update_fields=[
                "last_accessed_at",
            ]
        )

        action = (
            LessonProgressOpenAction.RESUMED
        )

    # =========================================
    # Completed Lesson can be opened for review,
    # but completion state must not be destroyed.
    # =========================================

    elif (
        progress.status
        == LessonProgress.Status.COMPLETED
    ):
        progress.last_accessed_at = now

        progress.save(
            update_fields=[
                "last_accessed_at",
            ]
        )

        action = (
            LessonProgressOpenAction
            .ALREADY_COMPLETED
        )

    else:
        # Defensive guard against unexpected DB
        # state even though the model CHECK
        # currently allows only two statuses.
        raise LearningServiceError(
            "Unsupported LessonProgress status."
        )

    return StartOrResumeLessonResult(
        action=action,
        lesson_id=lesson.pk,
        lesson_progress_id=progress.pk,
    )

class LessonProgressMutationAction(str, Enum):
    UPDATED = "updated"
    COMPLETED = "completed"
    ALREADY_COMPLETED = "already_completed"


@dataclass(frozen=True)
class LessonProgressMutationResult:
    action: LessonProgressMutationAction
    lesson_id: int
    lesson_progress_id: int


@transaction.atomic
def update_lesson_progress(
    *,
    user,
    lesson_id,
    completion_ratio,
):
    """
    Update progress for an already-started Lesson.

    Policy:
    - LessonProgress must already exist;
    - Lesson must still be active;
    - only IN_PROGRESS records may be updated;
    - progress is monotonic;
    - 1.0000 is reserved for complete_lesson();
    - last_accessed_at is refreshed.
    """

    if user.pk is None:
        raise LearningServiceError(
            "User must be saved before updating "
            "Lesson progress."
        )

    if not isinstance(
        completion_ratio,
        Decimal,
    ):
        raise LearningServiceError(
            "completion_ratio must be Decimal."
        )

    if not completion_ratio.is_finite():
        raise LearningServiceError(
            "completion_ratio must be finite."
        )

    if (
        completion_ratio < Decimal("0")
        or completion_ratio >= Decimal("1")
    ):
        raise LearningServiceError(
            "completion_ratio must be greater than "
            "or equal to 0 and less than 1. "
            "Use complete_lesson() to finish "
            "a Lesson."
        )

    if (
        completion_ratio.quantize(
            Decimal("0.0001")
        )
        != completion_ratio
    ):
        raise LearningServiceError(
            "completion_ratio may have at most "
            "four decimal places."
        )

    try:
        progress = (
            LessonProgress.objects
            .select_for_update()
            .select_related(
                "lesson",
            )
            .get(
                user=user,
                lesson_id=lesson_id,
                lesson__is_active=True,
            )
        )

    except LessonProgress.DoesNotExist as exc:
        raise LearningServiceError(
            "Active Lesson progress does not exist. "
            "Start the Lesson first."
        ) from exc

    if (
        progress.status
        != LessonProgress.Status.IN_PROGRESS
    ):
        raise LearningServiceError(
            "Completed Lesson progress cannot "
            "be updated."
        )

    if (
        completion_ratio
        < progress.completion_ratio
    ):
        raise LearningServiceError(
            "Lesson progress cannot decrease."
        )

    progress.completion_ratio = (
        completion_ratio
    )

    progress.last_accessed_at = (
        timezone.now()
    )

    try:
        progress.full_clean()

    except ValidationError as exc:
        raise LearningServiceError(
            f"LessonProgress validation failed: "
            f"{exc}"
        ) from exc

    progress.save(
        update_fields=[
            "completion_ratio",
            "last_accessed_at",
        ]
    )

    return LessonProgressMutationResult(
        action=(
            LessonProgressMutationAction.UPDATED
        ),
        lesson_id=progress.lesson_id,
        lesson_progress_id=progress.pk,
    )


@transaction.atomic
def complete_lesson(
    *,
    user,
    lesson_id,
):
    """
    Complete an already-started active Lesson.

    First completion:
    - status -> COMPLETED
    - completion_ratio -> 1.0000
    - completed_at -> now
    - last_accessed_at -> now

    Repeated completion:
    - idempotent;
    - completed_at is not changed.
    """

    if user.pk is None:
        raise LearningServiceError(
            "User must be saved before completing "
            "a Lesson."
        )

    try:
        progress = (
            LessonProgress.objects
            .select_for_update()
            .select_related(
                "lesson",
            )
            .get(
                user=user,
                lesson_id=lesson_id,
                lesson__is_active=True,
            )
        )

    except LessonProgress.DoesNotExist as exc:
        raise LearningServiceError(
            "Active Lesson progress does not exist. "
            "Start the Lesson first."
        ) from exc

    # Idempotent completion.
    if (
        progress.status
        == LessonProgress.Status.COMPLETED
    ):
        return LessonProgressMutationResult(
            action=(
                LessonProgressMutationAction
                .ALREADY_COMPLETED
            ),
            lesson_id=progress.lesson_id,
            lesson_progress_id=progress.pk,
        )

    if (
        progress.status
        != LessonProgress.Status.IN_PROGRESS
    ):
        raise LearningServiceError(
            "Unsupported LessonProgress status."
        )

    now = timezone.now()

    progress.status = (
        LessonProgress.Status.COMPLETED
    )

    progress.completion_ratio = (
        Decimal("1.0000")
    )

    progress.completed_at = now
    progress.last_accessed_at = now

    try:
        progress.full_clean()

    except ValidationError as exc:
        raise LearningServiceError(
            f"LessonProgress validation failed: "
            f"{exc}"
        ) from exc

    progress.save(
        update_fields=[
            "status",
            "completion_ratio",
            "completed_at",
            "last_accessed_at",
        ]
    )

    return LessonProgressMutationResult(
        action=(
            LessonProgressMutationAction.COMPLETED
        ),
        lesson_id=progress.lesson_id,
        lesson_progress_id=progress.pk,
    )