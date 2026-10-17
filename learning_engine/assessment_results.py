from decimal import Decimal

from learning_engine.models import (
    AssessmentSession,
    MASTERY_MASTERED_MIN_OBSERVATIONS,
    MASTERY_MASTERED_THRESHOLD,
    QuestionAttempt,
    SessionQuestion,
)
from learning_engine.mastery import (
    ensure_skill_mastery_current,
)

from learning_engine.models import (
    MASTERY_MASTERED_MIN_OBSERVATIONS,
    MASTERY_MASTERED_THRESHOLD,
)

class AssessmentResultError(ValueError):
    pass


def _collect_final_attempts(
    *,
    session_question_ids,
):
    """
    Return only the highest-numbered attempt for
    every SessionQuestion.

    Historical revisions remain stored in DB,
    but assessment result uses final-answer
    semantics.
    """

    final_attempt_by_question = {}

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
        final_attempt_by_question[
            attempt.session_question_id
        ] = attempt

    return final_attempt_by_question


def _collect_affected_skills(
    *,
    session_questions,
):
    """
    Build historical Skill metadata only from
    SessionQuestion.skill_snapshot.

    Do not reconstruct historical assessment
    Skill mappings from current QuestionSkill rows.
    """

    skill_rows = {}

    for session_question in session_questions:
        seen_in_question = set()

        for snapshot in (
            session_question.skill_snapshot
            or []
        ):
            skill_id = snapshot.get(
                "skill_id"
            )

            skill_code = snapshot.get(
                "skill_code"
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
                or not skill_code
            ):
                raise AssessmentResultError(
                    "Assessment contains malformed "
                    "Skill snapshot data."
                )

            existing = skill_rows.get(
                skill_id
            )

            if existing is None:
                existing = {
                    "skill_id": skill_id,
                    "skill_code": skill_code,
                    "question_count": 0,
                    "primary_question_count": 0,
                }

                skill_rows[
                    skill_id
                ] = existing

            elif (
                existing["skill_code"]
                != skill_code
            ):
                raise AssessmentResultError(
                    "Assessment contains inconsistent "
                    "Skill snapshot data."
                )

            # One Skill should count at most once
            # for a single SessionQuestion.
            if (
                skill_id
                not in seen_in_question
            ):
                existing[
                    "question_count"
                ] += 1

                if snapshot.get(
                    "is_primary"
                ):
                    existing[
                        "primary_question_count"
                    ] += 1

                seen_in_question.add(
                    skill_id
                )

    return sorted(
        skill_rows.values(),
        key=lambda row: (
            row["skill_code"],
            row["skill_id"],
        ),
    )


def build_completed_assessment_result(
    *,
    session,
):
    """
    Build one historical assessment result.

    Allowed only for COMPLETED assessments.

    Result semantics:
    - final QuestionAttempt only;
    - viewed + unanswered counts as incorrect
      assessment evidence;
    - never viewed is excluded from evidence;
    - correctness comes from the immutable
      QuestionAttempt.is_correct snapshot;
    - Skill impact comes from skill_snapshot.

    No mastery delta is reported because current
    schema does not persist per-assessment mastery
    snapshots.
    """

    if (
        session.status
        != AssessmentSession.Status.COMPLETED
    ):
        raise AssessmentResultError(
            "Assessment result is available only "
            "for completed assessments."
        )

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

    final_attempt_by_question = (
        _collect_final_attempts(
            session_question_ids=(
                session_question_ids
            ),
        )
    )

    question_results = []

    viewed_count = 0
    answered_count = 0
    correct_count = 0
    incorrect_answer_count = 0
    viewed_unanswered_count = 0
    never_viewed_count = 0

    for session_question in session_questions:
        question = session_question.question

        attempt = (
            final_attempt_by_question.get(
                session_question.pk
            )
        )

        was_viewed = (
            session_question.first_viewed_at
            is not None
        )

        if was_viewed:
            viewed_count += 1

        if attempt is not None:
            answered_count += 1

            if attempt.is_correct:
                correct_count += 1

                result_state = (
                    "correct"
                )

                evidence_outcome = (
                    "correct"
                )

            else:
                incorrect_answer_count += 1

                result_state = (
                    "incorrect"
                )

                evidence_outcome = (
                    "incorrect"
                )

            final_attempt = {
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

                # Safe now because assessment is
                # already COMPLETED.
                "is_correct": (
                    attempt.is_correct
                ),

                "response_time_ms": (
                    attempt.response_time_ms
                ),

                "answered_at": (
                    attempt.answered_at.isoformat()
                ),
            }

        elif was_viewed:
            # Canonical mastery semantics:
            # viewed + no answer = incorrect evidence.
            viewed_unanswered_count += 1

            result_state = (
                "viewed_unanswered"
            )

            evidence_outcome = (
                "incorrect"
            )

            final_attempt = None

        else:
            never_viewed_count += 1

            result_state = (
                "never_viewed"
            )

            evidence_outcome = (
                "excluded"
            )

            final_attempt = None

        # Options are useful for reconstructing the
        # learner-facing question.
        #
        # Do NOT expose option.is_correct here:
        # correct-option state itself was not
        # snapshotted historically.
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

        question_results.append(
            {
                "session_question_id": (
                    session_question.pk
                ),

                "sequence_number": (
                    session_question
                    .sequence_number
                ),

                "first_viewed_at": (
                    session_question
                    .first_viewed_at
                    .isoformat()
                    if (
                        session_question
                        .first_viewed_at
                        is not None
                    )
                    else None
                ),

                "active_response_time_ms": (
                    session_question
                    .active_response_time_ms
                ),

                "result_state": (
                    result_state
                ),

                "evidence_outcome": (
                    evidence_outcome
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

                "final_attempt": (
                    final_attempt
                ),
            }
        )

    evidence_question_count = (
        viewed_count
    )

    evidence_incorrect_count = (
        incorrect_answer_count
        + viewed_unanswered_count
    )

    if evidence_question_count:
        evidence_accuracy = (
            Decimal(
                correct_count
            )
            / Decimal(
                evidence_question_count
            )
        ).quantize(
            Decimal("0.0000")
        )

        evidence_accuracy = str(
            evidence_accuracy
        )

    else:
        evidence_accuracy = None

    affected_skills = (
        _collect_affected_skills(
            session_questions=(
                session_questions
            ),
        )
    )

    return {
        "assessment": {
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

            "ended_at": (
                session.ended_at.isoformat()
                if session.ended_at
                is not None
                else None
            ),
        },

        "summary": {
            "question_count": (
                len(
                    session_questions
                )
            ),

            "viewed_count": (
                viewed_count
            ),

            "answered_count": (
                answered_count
            ),

            "correct_count": (
                correct_count
            ),

            "incorrect_answer_count": (
                incorrect_answer_count
            ),

            "viewed_unanswered_count": (
                viewed_unanswered_count
            ),

            "never_viewed_count": (
                never_viewed_count
            ),

            "evidence_question_count": (
                evidence_question_count
            ),

            "evidence_incorrect_count": (
                evidence_incorrect_count
            ),

            "evidence_accuracy": (
                evidence_accuracy
            ),
        },

        "questions": (
            question_results
        ),

        # Historical impact metadata.
        #
        # Current SkillMastery is intentionally
        # NOT presented as if it were a frozen
        # result of this assessment.
        "affected_skills": (
            affected_skills
        ),
    }

def build_current_mastery_projection(
    *,
    user,
    affected_skills,
):
    """
    Return the learner's CURRENT mastery state for
    Skills represented by a historical assessment.

    IMPORTANT:
    this is not a mastery snapshot captured at the
    time of that assessment.

    It may include evidence from later completed
    assessments and normal lazy-refresh decay.
    """

    skills = []

    for affected_skill in affected_skills:
        skill_id = affected_skill[
            "skill_id"
        ]

        mastery = (
            ensure_skill_mastery_current(
                user=user,
                skill_id=skill_id,
            )
        )

        is_mastered = (
            mastery.mastery_score
            is not None
            and mastery.observation_count
            >= MASTERY_MASTERED_MIN_OBSERVATIONS
            and mastery.mastery_score
            >= MASTERY_MASTERED_THRESHOLD
        )

        skills.append(
            {
                # Historical identity from the
                # assessment snapshot.
                "skill_id": skill_id,

                "skill_code": (
                    affected_skill[
                        "skill_code"
                    ]
                ),

                # CURRENT derived mastery state.
                "mastery_score": (
                    str(
                        mastery.mastery_score
                    )
                    if (
                        mastery.mastery_score
                        is not None
                    )
                    else None
                ),

                "observation_count": (
                    mastery.observation_count
                ),

                "correct_attempts": (
                    mastery.correct_attempts
                ),

                "recent_accuracy": (
                    str(
                        mastery.recent_accuracy
                    )
                    if (
                        mastery.recent_accuracy
                        is not None
                    )
                    else None
                ),

                "historical_accuracy": (
                    str(
                        mastery
                        .historical_accuracy
                    )
                    if (
                        mastery
                        .historical_accuracy
                        is not None
                    )
                    else None
                ),

                "avg_response_time_ms": (
                    str(
                        mastery
                        .avg_response_time_ms
                    )
                    if (
                        mastery
                        .avg_response_time_ms
                        is not None
                    )
                    else None
                ),

                "consecutive_errors": (
                    mastery.consecutive_errors
                ),

                "last_evidence_at": (
                    mastery
                    .last_evidence_at
                    .isoformat()
                    if (
                        mastery.last_evidence_at
                        is not None
                    )
                    else None
                ),

                "calculated_at": (
                    mastery
                    .calculated_at
                    .isoformat()
                    if (
                        mastery.calculated_at
                        is not None
                    )
                    else None
                ),

                "algorithm_version": (
                    mastery.algorithm_version
                ),

                "has_sufficient_data": (
                    mastery.has_sufficient_data
                ),

                "is_mastered": (
                    is_mastered
                ),
            }
        )

    return {
        "scope": "current",

        # Explicitly prevent frontend / thesis text
        # from interpreting this as historical
        # before/after assessment data.
        "is_historical_assessment_snapshot": False,

        "skills": skills,
    }