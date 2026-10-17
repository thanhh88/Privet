import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from django.conf import settings

from curriculum.models import (
    Lesson,
    LessonProgress,
    LessonSkill,
    Skill,
    Topic,
)
from learning_engine.models import (
    AssessmentSession,
    QuestionAttempt,
    SessionQuestion,
    SkillMastery,
)
from learning_engine.services import (
    LearningServiceError,
    abandon_session,
    add_question_to_session,
    complete_session,
    mark_session_question_viewed,
    record_session_question_active_time,
    save_option_answer,
    select_and_add_adaptive_question,
    start_session,
    assemble_adaptive_assessment,
    assemble_diagnostic_assessment,
    submit_prior_russian_experience,
)
from questionbank.models import (
    Question,
    QuestionOption,
    QuestionSkill,
)
from learning_engine.mastery import (
    SkillEvidence,
    calculate_mastery,
    collect_skill_evidence,
    recalculate_skill_masteries,
    recalculate_skill_mastery,
    ensure_skill_mastery_current,
)
from learning_engine.priority import (
    build_user_skill_priority,
    calculate_skill_priority,
    rank_user_skill_priorities,
    select_top_priority_skill,
)
from datetime import timedelta
from decimal import Decimal
from tracking.models import StudySession
from tracking.services import (
    record_learning_completion_event,
)
from learning_engine.question_selection import (
    QuestionCandidate,
    build_question_candidates,
    rank_question_candidates,
    select_top_question_candidate,
)
from learning_engine.adaptive_assembly import (
    AssemblySkillCandidate,
    plan_adaptive_assessment,
)
from learning_engine.adaptive_preparation import (
    prepare_adaptive_assessment_candidates,
)
from learning_engine.diagnostic_blueprint import (
    DIAGNOSTIC_BLUEPRINT_V1,
    DiagnosticBlueprintSlot,
    validate_diagnostic_blueprint,
)
from learning_engine.diagnostic_selection import (
    plan_diagnostic_questions,
)
from learning_engine.cold_start import (
    LearnerEntryRoute,
    determine_learner_entry_route,
)
from learning_engine.learner_entry import (
    resolve_learner_entry_state,
)
from learning_engine.beginner_path import (
    BeginnerLessonAction,
    BeginnerLessonUnavailableReason,
    resolve_beginner_lesson,
)
from django.urls import reverse

User = get_user_model()


class SaveOptionAnswerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # -----------------------------------------
        # Users
        # -----------------------------------------

        cls.user = User.objects.create_user(
            email="learner@example.com",
            password="test-password-123",
        )

        cls.other_user = User.objects.create_user(
            email="other@example.com",
            password="test-password-123",
        )

        # -----------------------------------------
        # Minimal curriculum taxonomy
        # -----------------------------------------

        cls.topic = Topic.objects.create(
            topic_name="Test Grammar Topic",
            category="grammar",
            display_order=999,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="TEST-01",
            skill_name="Test Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        # -----------------------------------------
        # Question 1
        # Correct answer = B
        # -----------------------------------------

        cls.question = Question.objects.create(
            source_id="TEST-Q-001",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text="Выберите правильный ответ.",
            cefr_level="A1",
            is_extension=False,
            difficulty_score="0.5000",
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=cls.question,
            skill=cls.skill,
            is_primary=True,
            weight="1.00000",
        )

        cls.option_a = QuestionOption.objects.create(
            question=cls.question,
            option_key="A",
            option_text="Первый",
            misconception_code="test_wrong",
            is_correct=False,
            display_order=1,
        )

        cls.option_b = QuestionOption.objects.create(
            question=cls.question,
            option_key="B",
            option_text="Второй",
            misconception_code=None,
            is_correct=True,
            display_order=2,
        )

        cls.option_c = QuestionOption.objects.create(
            question=cls.question,
            option_key="C",
            option_text="Третий",
            misconception_code="test_wrong",
            is_correct=False,
            display_order=3,
        )

        # -----------------------------------------
        # Question 2
        # Correct answer = A
        # -----------------------------------------

        cls.question_2 = Question.objects.create(
            source_id="TEST-Q-002",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text="Выберите второй тестовый ответ.",
            cefr_level="A1",
            is_extension=False,
            difficulty_score="0.5000",
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=cls.question_2,
            skill=cls.skill,
            is_primary=True,
            weight="1.00000",
        )

        QuestionOption.objects.create(
            question=cls.question_2,
            option_key="A",
            option_text="Один",
            misconception_code=None,
            is_correct=True,
            display_order=1,
        )

        QuestionOption.objects.create(
            question=cls.question_2,
            option_key="B",
            option_text="Два",
            misconception_code="test_wrong",
            is_correct=False,
            display_order=2,
        )

        QuestionOption.objects.create(
            question=cls.question_2,
            option_key="C",
            option_text="Три",
            misconception_code="test_wrong",
            is_correct=False,
            display_order=3,
        )

    def setUp(self):
        self.session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

        result = add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.question.pk,
        )

        self.session_question = (
            SessionQuestion.objects.get(
                pk=result.session_question_id
            )
        )

    # =================================================
    # Save / internal grading
    # =================================================

    def test_correct_answer_is_graded_internally_but_not_exposed(
        self,
    ):
        result = save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
            response_time_ms=1500,
        )

        self.assertEqual(
            result.selected_option_key,
            "B",
        )
        self.assertFalse(
            hasattr(
                result,
                "is_correct",
            )
        )
        self.assertFalse(
            hasattr(
                result,
                "correct_option_key",
            )
        )
        self.assertEqual(
            result.sequence_number,
            1,
        )
        self.assertEqual(
            result.attempt_number,
            1,
        )
        self.assertEqual(
            result.session_question_id,
            self.session_question.pk,
        )

        attempt = QuestionAttempt.objects.get(
            pk=result.attempt_id
        )

        self.assertEqual(
            attempt.session_question,
            self.session_question,
        )
        self.assertEqual(
            attempt.attempt_number,
            1,
        )
        self.assertEqual(
            attempt.selected_option_key,
            "B",
        )

        # Correctness remains an internal
        # server-side grading snapshot.
        self.assertTrue(
            attempt.is_correct
        )
        self.assertEqual(
            attempt.response_time_ms,
            1500,
        )

    def test_wrong_answer_is_graded_internally_but_not_exposed(
        self,
    ):
        result = save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="A",
        )

        self.assertEqual(
            result.selected_option_key,
            "A",
        )
        self.assertFalse(
            hasattr(
                result,
                "is_correct",
            )
        )
        self.assertFalse(
            hasattr(
                result,
                "correct_option_key",
            )
        )
        self.assertEqual(
            result.attempt_number,
            1,
        )

        attempt = QuestionAttempt.objects.get(
            pk=result.attempt_id
        )

        self.assertFalse(
            attempt.is_correct
        )
        self.assertEqual(
            attempt.selected_option_key,
            "A",
        )

    def test_active_assessment_does_not_expose_grading_feedback(
        self,
    ):
        result = save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="A",
        )

        # The learner-facing save contract exposes
        # only saved-response metadata.
        self.assertEqual(
            result.selected_option_key,
            "A",
        )
        self.assertFalse(
            hasattr(
                result,
                "is_correct",
            )
        )
        self.assertFalse(
            hasattr(
                result,
                "correct_option_key",
            )
        )

        # The server may still grade internally.
        attempt = QuestionAttempt.objects.get(
            pk=result.attempt_id
        )
        self.assertFalse(
            attempt.is_correct
        )

        self.assertEqual(
            self.session.status,
            AssessmentSession.Status.ACTIVE,
        )

    def test_attempt_number_increments_on_revision(self):
        first = save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="A",
        )

        second = save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        # Still the same Question position in the test.
        self.assertEqual(
            first.sequence_number,
            1,
        )
        self.assertEqual(
            second.sequence_number,
            1,
        )

        # But two answer events were recorded.
        self.assertEqual(
            first.attempt_number,
            1,
        )
        self.assertEqual(
            second.attempt_number,
            2,
        )

        # Save results expose the learner's current
        # selection, but not grading feedback.
        self.assertEqual(
            first.selected_option_key,
            "A",
        )
        self.assertEqual(
            second.selected_option_key,
            "B",
        )
        self.assertFalse(
            hasattr(
                first,
                "is_correct",
            )
        )
        self.assertFalse(
            hasattr(
                second,
                "is_correct",
            )
        )

        attempts = (
            QuestionAttempt.objects.filter(
                session_question=(
                    self.session_question
                )
            )
            .order_by(
                "attempt_number"
            )
        )

        self.assertEqual(
            list(
                attempts.values_list(
                    "attempt_number",
                    flat=True,
                )
            ),
            [1, 2],
        )

        # Internal behavioral/grading history is kept.
        self.assertFalse(
            attempts[0].is_correct
        )
        self.assertTrue(
            attempts[1].is_correct
        )

        final_attempt = (
            attempts
            .order_by(
                "-attempt_number"
            )
            .first()
        )

        self.assertEqual(
            final_attempt.selected_option_key,
            "B",
        )
        self.assertTrue(
            final_attempt.is_correct
        )

    def test_option_key_must_belong_to_question(self):
        with self.assertRaises(
            LearningServiceError
        ):
            save_option_answer(
                user=self.user,
                session_question_id=(
                    self.session_question.pk
                ),
                selected_option_key="Z",
            )

        self.assertEqual(
            QuestionAttempt.objects.count(),
            0,
        )

    def test_session_question_must_belong_to_user(self):
        with self.assertRaises(
            LearningServiceError
        ):
            save_option_answer(
                user=self.other_user,
                session_question_id=(
                    self.session_question.pk
                ),
                selected_option_key="B",
            )

        self.assertEqual(
            QuestionAttempt.objects.count(),
            0,
        )

    def test_completed_session_rejects_answer(self):
        self.session.status = (
            AssessmentSession.Status.COMPLETED
        )
        self.session.ended_at = timezone.now()

        self.session.save(
            update_fields=[
                "status",
                "ended_at",
            ]
        )

        with self.assertRaises(
            LearningServiceError
        ):
            save_option_answer(
                user=self.user,
                session_question_id=(
                    self.session_question.pk
                ),
                selected_option_key="B",
            )

        self.assertEqual(
            QuestionAttempt.objects.count(),
            0,
        )

    def test_inactive_question_is_rejected(self):
        self.question.is_active = False

        self.question.save(
            update_fields=[
                "is_active",
            ]
        )

        with self.assertRaises(
            LearningServiceError
        ):
            save_option_answer(
                user=self.user,
                session_question_id=(
                    self.session_question.pk
                ),
                selected_option_key="B",
            )

        self.assertEqual(
            QuestionAttempt.objects.count(),
            0,
        )

    def test_invalid_response_time_is_rejected(self):
        invalid_values = [
            -1,
            1.5,
            True,
        ]

        for value in invalid_values:
            with self.subTest(
                response_time_ms=value
            ):
                with self.assertRaises(
                    LearningServiceError
                ):
                    save_option_answer(
                        user=self.user,
                        session_question_id=(
                            self.session_question.pk
                        ),
                        selected_option_key="B",
                        response_time_ms=value,
                    )

        self.assertEqual(
            QuestionAttempt.objects.count(),
            0,
        )

        self.session_question.refresh_from_db()

        self.assertIsNone(
            self.session_question.first_viewed_at
        )

    def test_option_key_is_normalized(self):
        result = save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="  B  ",
        )

        attempt = QuestionAttempt.objects.get(
            pk=result.attempt_id
        )

        self.assertEqual(
            attempt.selected_option_key,
            "B",
        )
        self.assertTrue(
            attempt.is_correct
        )

    def test_client_cannot_supply_is_correct(self):
        with self.assertRaises(
            TypeError
        ):
            save_option_answer(
                user=self.user,
                session_question_id=(
                    self.session_question.pk
                ),
                selected_option_key="A",
                is_correct=True,
            )

        self.assertEqual(
            QuestionAttempt.objects.count(),
            0,
        )

    def test_attempt_validation_failure_rolls_back(self):
        with patch(
            "learning_engine.services."
            "QuestionAttempt.full_clean",
            side_effect=ValidationError(
                "Forced validation failure."
            ),
        ):
            with self.assertRaises(
                LearningServiceError
            ):
                save_option_answer(
                    user=self.user,
                    session_question_id=(
                        self.session_question.pk
                    ),
                    selected_option_key="B",
                )

        self.assertEqual(
            QuestionAttempt.objects.count(),
            0,
        )

        self.session_question.refresh_from_db()

        # save_option_answer() sets viewed before
        # creating the Attempt, but the whole transaction
        # must roll back if Attempt validation fails.
        self.assertIsNone(
            self.session_question.first_viewed_at
        )

    # =================================================
    # SessionQuestion selection
    # =================================================

    def test_add_question_to_session_assigns_next_sequence(self):
        result = add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.question_2.pk,
        )

        self.assertEqual(
            result.sequence_number,
            2,
        )
        self.assertEqual(
            result.question_id,
            self.question_2.pk,
        )
        self.assertIsNone(
            result.first_viewed_at
        )

        session_question = (
            SessionQuestion.objects.get(
                pk=result.session_question_id
            )
        )

        self.assertEqual(
            session_question.session,
            self.session,
        )
        self.assertEqual(
            session_question.question,
            self.question_2,
        )
        self.assertEqual(
            session_question.sequence_number,
            2,
        )
        self.assertIsNone(
            session_question.first_viewed_at
        )

        self.assertEqual(
            session_question.difficulty_score_snapshot,
            Decimal("0.5000"),
        )

        self.assertEqual(
            session_question.skill_snapshot,
            [
                {
                    "skill_id": self.skill.pk,
                    "skill_code": self.skill.skill_code,
                    "is_primary": True,
                    "weight": "1.00000",
                }
            ],
        )

    def test_duplicate_question_in_session_is_rejected(self):
        with self.assertRaises(
            LearningServiceError
        ):
            add_question_to_session(
                user=self.user,
                session_id=self.session.pk,
                question_id=self.question.pk,
            )

        self.assertEqual(
            SessionQuestion.objects.filter(
                session=self.session
            ).count(),
            1,
        )

    def test_other_user_cannot_add_question_to_session(self):
        with self.assertRaises(
            LearningServiceError
        ):
            add_question_to_session(
                user=self.other_user,
                session_id=self.session.pk,
                question_id=self.question_2.pk,
            )

        self.assertEqual(
            SessionQuestion.objects.filter(
                session=self.session
            ).count(),
            1,
        )

    def test_inactive_question_cannot_be_added_to_session(self):
        self.question_2.is_active = False

        self.question_2.save(
            update_fields=[
                "is_active",
            ]
        )

        with self.assertRaises(
            LearningServiceError
        ):
            add_question_to_session(
                user=self.user,
                session_id=self.session.pk,
                question_id=self.question_2.pk,
            )

        self.assertEqual(
            SessionQuestion.objects.filter(
                session=self.session
            ).count(),
            1,
        )

    # =================================================
    # Viewed state
    # =================================================

    def test_mark_viewed_is_idempotent(self):
        first = mark_session_question_viewed(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
        )

        self.assertIsNotNone(
            first.first_viewed_at
        )

        first_timestamp = (
            first.first_viewed_at
        )

        # Viewed but unanswered:
        # no QuestionAttempt is created.
        self.assertEqual(
            QuestionAttempt.objects.count(),
            0,
        )

        second = mark_session_question_viewed(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
        )

        self.assertEqual(
            second.first_viewed_at,
            first_timestamp,
        )

        self.assertEqual(
            QuestionAttempt.objects.count(),
            0,
        )

    def test_save_marks_question_as_viewed(self):
        self.assertIsNone(
            self.session_question.first_viewed_at
        )

        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        self.session_question.refresh_from_db()

        self.assertIsNotNone(
            self.session_question.first_viewed_at
        )

    def test_other_user_cannot_mark_question_viewed(self):
        with self.assertRaises(
            LearningServiceError
        ):
            mark_session_question_viewed(
                user=self.other_user,
                session_question_id=(
                    self.session_question.pk
                ),
            )

        self.session_question.refresh_from_db()

        self.assertIsNone(
            self.session_question.first_viewed_at
        )

    def test_completed_session_question_cannot_be_marked_viewed(self):
        self.session.status = (
            AssessmentSession.Status.COMPLETED
        )
        self.session.ended_at = timezone.now()

        self.session.save(
            update_fields=[
                "status",
                "ended_at",
            ]
        )

        with self.assertRaises(
            LearningServiceError
        ):
            mark_session_question_viewed(
                user=self.user,
                session_question_id=(
                    self.session_question.pk
                ),
            )

        self.session_question.refresh_from_db()

        self.assertIsNone(
            self.session_question.first_viewed_at
        )

    def test_mastery_evidence_uses_final_attempt_only(self):
        first = save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="A",
        )

        second = save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        evidence = collect_skill_evidence(
            user=self.user
        )

        self.assertEqual(
            len(evidence),
            1,
        )

        row = evidence[0]

        self.assertEqual(
            row.session_question_id,
            self.session_question.pk,
        )

        self.assertEqual(
            row.sequence_number,
            1,
        )

        self.assertTrue(
            row.is_primary
        )

        self.assertEqual(
            row.final_attempt_id,
            second.attempt_id,
        )

        self.assertEqual(
            row.final_attempt_number,
            2,
        )

        self.assertTrue(
            row.is_correct
        )

        self.assertFalse(
            row.was_unanswered
        )

        self.assertNotEqual(
            row.final_attempt_id,
            first.attempt_id,
        )

    def test_viewed_unanswered_is_incorrect_evidence(self):
        mark_session_question_viewed(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        evidence = collect_skill_evidence(
            user=self.user
        )

        self.assertEqual(
            len(evidence),
            1,
        )

        row = evidence[0]

        self.assertFalse(
            row.is_correct
        )

        self.assertTrue(
            row.was_unanswered
        )

        self.assertIsNone(
            row.final_attempt_id
        )

        self.assertIsNone(
            row.final_attempt_number
        )

        self.assertIsNotNone(
            row.evidence_at
        )

    def test_never_viewed_question_is_not_mastery_evidence(self):
        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        evidence = collect_skill_evidence(
            user=self.user
        )

        self.assertEqual(
            evidence,
            [],
        )

    def test_active_session_is_not_mastery_evidence(self):
        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        evidence = collect_skill_evidence(
            user=self.user
        )

        self.assertEqual(
            evidence,
            [],
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        evidence = collect_skill_evidence(
            user=self.user
        )

        self.assertEqual(
            len(evidence),
            1,
        )

        self.assertTrue(
            evidence[0].is_correct
        )

    def test_abandoned_session_is_not_mastery_evidence(self):
        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        abandon_session(
            user=self.user,
            session_id=self.session.pk,
        )

        evidence = collect_skill_evidence(
            user=self.user
        )

        self.assertEqual(
            evidence,
            [],
        )

    def test_mastery_evidence_uses_session_completion_time(self):
        result = save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        attempt = QuestionAttempt.objects.get(
            pk=result.attempt_id
        )

        self.assertIsNotNone(
            attempt.answered_at
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        self.session.refresh_from_db()

        evidence = collect_skill_evidence(
            user=self.user
        )

        self.assertEqual(
            len(evidence),
            1,
        )

        self.assertEqual(
            evidence[0].evidence_at,
            self.session.ended_at,
        )


    def test_historical_evidence_uses_selection_snapshot_after_bank_changes(
            self,
    ):
        # -----------------------------------------
        # Assessment uses the original bank state.
        # -----------------------------------------

        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        evidence_before = collect_skill_evidence(
            user=self.user
        )

        self.assertEqual(
            len(evidence_before),
            1,
        )

        original = evidence_before[0]

        self.assertEqual(
            original.skill_id,
            self.skill.pk,
        )

        self.assertEqual(
            original.skill_code,
            self.skill.skill_code,
        )

        self.assertTrue(
            original.is_primary
        )

        self.assertEqual(
            original.skill_weight,
            Decimal("1.00000"),
        )

        self.assertEqual(
            original.difficulty_score,
            Decimal("0.5000"),
        )

        # -----------------------------------------
        # Change the CURRENT Question Bank.
        #
        # Historical assessment evidence must not
        # change after this point.
        # -----------------------------------------

        replacement_skill = Skill.objects.create(
            topic=self.topic,
            skill_code="TEST-02",
            skill_name="Changed Bank Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=2,
            is_active=True,
        )

        self.question.difficulty_score = (
            Decimal("0.9000")
        )

        self.question.save(
            update_fields=[
                "difficulty_score",
            ]
        )

        original_link = (
            QuestionSkill.objects.get(
                question=self.question,
                skill=self.skill,
            )
        )

        with transaction.atomic():
            original_link.weight = (
                Decimal("0.70000")
            )

            original_link.save(
                update_fields=[
                    "weight",
                ]
            )

            QuestionSkill.objects.create(
                question=self.question,
                skill=replacement_skill,
                is_primary=False,
                weight=Decimal("0.30000"),
            )

        # Sanity check:
        # the live Question Bank really changed.
        self.question.refresh_from_db()

        self.assertEqual(
            self.question.difficulty_score,
            Decimal("0.9000"),
        )

        original_link.refresh_from_db()

        self.assertEqual(
            original_link.weight,
            Decimal("0.70000"),
        )

        self.assertTrue(
            QuestionSkill.objects.filter(
                question=self.question,
                skill=replacement_skill,
            ).exists()
        )

        # -----------------------------------------
        # Historical evidence must remain frozen.
        # -----------------------------------------

        evidence_after = collect_skill_evidence(
            user=self.user
        )

        self.assertEqual(
            len(evidence_after),
            1,
        )

        historical = evidence_after[0]

        self.assertEqual(
            historical.skill_id,
            self.skill.pk,
        )

        self.assertEqual(
            historical.skill_code,
            self.skill.skill_code,
        )

        self.assertTrue(
            historical.is_primary
        )

        self.assertEqual(
            historical.skill_weight,
            Decimal("1.00000"),
        )

        self.assertEqual(
            historical.difficulty_score,
            Decimal("0.5000"),
        )

        # Filtering must also use the historical
        # snapshot rather than live QuestionSkill rows.
        original_skill_evidence = (
            collect_skill_evidence(
                user=self.user,
                skill_id=self.skill.pk,
            )
        )

        self.assertEqual(
            len(original_skill_evidence),
            1,
        )

        self.assertEqual(
            original_skill_evidence[0].skill_weight,
            Decimal("1.00000"),
        )

        replacement_skill_evidence = (
            collect_skill_evidence(
                user=self.user,
                skill_id=replacement_skill.pk,
            )
        )

        self.assertEqual(
            replacement_skill_evidence,
            [],
        )

    def test_recalculate_mastery_creates_cold_start_state(self):
        result = recalculate_skill_mastery(
            user=self.user,
            skill_id=self.skill.pk,
        )

        self.assertTrue(
            result.created
        )

        self.assertEqual(
            result.observation_count,
            0,
        )

        self.assertEqual(
            result.mastery_estimate,
            Decimal("0.50"),
        )

        self.assertIsNone(
            result.mastery_score
        )

        self.assertIsNone(
            result.last_evidence_at
        )

        state = SkillMastery.objects.get(
            user=self.user,
            skill=self.skill,
        )

        self.assertEqual(
            state.observation_count,
            0,
        )

        self.assertIsNone(
            state.mastery_score
        )

    def test_recalculate_mastery_with_one_observation(self):
        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        self.session.refresh_from_db()

        result = recalculate_skill_mastery(
            user=self.user,
            skill_id=self.skill.pk,
            as_of=self.session.ended_at,
        )

        self.assertEqual(
            result.observation_count,
            1,
        )

        self.assertIsNone(
            result.mastery_score
        )

        self.assertEqual(
            result.last_evidence_at,
            self.session.ended_at,
        )

        state = SkillMastery.objects.get(
            user=self.user,
            skill=self.skill,
        )

        self.assertEqual(
            state.observation_count,
            1,
        )

        self.assertIsNone(
            state.mastery_score
        )

    def test_recalculate_mastery_persists_score_after_three_observations(
            self,
    ):
        sessions = []

        # First observation.
        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        sessions.append(
            self.session
        )

        # Two more completed assessments.
        for _ in range(2):
            session = AssessmentSession.objects.create(
                user=self.user,
                mode=AssessmentSession.Mode.ADAPTIVE,
                status=AssessmentSession.Status.ACTIVE,
            )

            session_question_result = (
                add_question_to_session(
                    user=self.user,
                    session_id=session.pk,
                    question_id=self.question.pk,
                )
            )

            session_question = (
                SessionQuestion.objects.get(
                    pk=(
                        session_question_result
                        .session_question_id
                    )
                )
            )

            save_option_answer(
                user=self.user,
                session_question_id=(
                    session_question.pk
                ),
                selected_option_key="B",
            )

            complete_session(
                user=self.user,
                session_id=session.pk,
            )

            sessions.append(
                session
            )

        # Give all three observations the same canonical
        # timestamp so this test isolates Bayesian smoothing
        # from time decay.
        fixed_time = timezone.now()

        AssessmentSession.objects.filter(
            pk__in=[
                session.pk
                for session in sessions
            ]
        ).update(
            ended_at=fixed_time
        )

        result = recalculate_skill_mastery(
            user=self.user,
            skill_id=self.skill.pk,
            as_of=fixed_time,
        )

        self.assertEqual(
            result.observation_count,
            3,
        )

        # alpha=2, m0=.5, three correct observations:
        #
        # (1 + 3) / (2 + 3) = .8
        self.assertEqual(
            result.mastery_score,
            Decimal("0.80000"),
        )

        state = SkillMastery.objects.get(
            user=self.user,
            skill=self.skill,
        )

        self.assertEqual(
            state.mastery_score,
            Decimal("0.80000"),
        )

    def test_recalculate_mastery_updates_same_snapshot(self):
        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        self.session.refresh_from_db()

        as_of = self.session.ended_at

        state_before = SkillMastery.objects.get(
            user=self.user,
            skill=self.skill,
        )

        first = recalculate_skill_mastery(
            user=self.user,
            skill_id=self.skill.pk,
            as_of=as_of,
        )

        second = recalculate_skill_mastery(
            user=self.user,
            skill_id=self.skill.pk,
            as_of=as_of,
        )

        # complete_session() already created
        # the SkillMastery snapshot.
        self.assertFalse(
            first.created
        )

        self.assertFalse(
            second.created
        )

        self.assertEqual(
            first.mastery_id,
            state_before.pk,
        )

        self.assertEqual(
            first.mastery_id,
            second.mastery_id,
        )

        self.assertEqual(
            first.observation_count,
            1,
        )

        self.assertEqual(
            second.observation_count,
            1,
        )

        self.assertEqual(
            SkillMastery.objects.filter(
                user=self.user,
                skill=self.skill,
            ).count(),
            1,
        )

    def test_complete_assessment_recalculates_mastery(self):
        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        self.assertFalse(
            SkillMastery.objects.filter(
                user=self.user,
                skill=self.skill,
            ).exists()
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        self.session.refresh_from_db()

        state = SkillMastery.objects.get(
            user=self.user,
            skill=self.skill,
        )

        self.assertEqual(
            state.observation_count,
            1,
        )

        # n < 3 -> mastery_score must remain NULL.
        self.assertIsNone(
            state.mastery_score
        )

        self.assertEqual(
            state.last_evidence_at,
            self.session.ended_at,
        )

    def test_complete_recalculates_skill_from_never_viewed_question(
            self,
    ):
        self.assertIsNone(
            self.session_question.first_viewed_at
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        state = SkillMastery.objects.get(
            user=self.user,
            skill=self.skill,
        )

        # Never viewed -> no valid observation,
        # but the Skill is still recalculated.
        self.assertEqual(
            state.observation_count,
            0,
        )

        self.assertIsNone(
            state.mastery_score
        )

        self.assertIsNone(
            state.last_evidence_at
        )

    def test_abandon_assessment_does_not_recalculate_mastery(
            self,
    ):
        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        abandon_session(
            user=self.user,
            session_id=self.session.pk,
        )

        self.assertFalse(
            SkillMastery.objects.filter(
                user=self.user,
                skill=self.skill,
            ).exists()
        )

    def test_mastery_failure_rolls_back_session_completion(
            self,
    ):
        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        with patch(
                "learning_engine.services."
                "recalculate_skill_masteries",
                side_effect=RuntimeError(
                    "Forced mastery failure."
                ),
        ):
            with self.assertRaises(
                    RuntimeError
            ):
                complete_session(
                    user=self.user,
                    session_id=self.session.pk,
                )

        self.session.refresh_from_db()

        self.assertEqual(
            self.session.status,
            AssessmentSession.Status.ACTIVE,
        )

        self.assertIsNone(
            self.session.ended_at
        )

        self.assertFalse(
            SkillMastery.objects.filter(
                user=self.user,
                skill=self.skill,
            ).exists()
        )


    def test_complete_assessment_persists_learner_state_metrics(
        self,
    ):
        # Wrong answer.
        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="A",
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        state = SkillMastery.objects.get(
            user=self.user,
            skill=self.skill,
        )

        self.assertEqual(
            state.observation_count,
            1,
        )

        self.assertEqual(
            state.correct_attempts,
            0,
        )

        self.assertEqual(
            state.recent_accuracy,
            Decimal("0.00000"),
        )

        self.assertEqual(
            state.historical_accuracy,
            Decimal("0.00000"),
        )

        self.assertEqual(
            state.consecutive_errors,
            1,
        )

    def test_active_time_accumulates_across_visits(self):
        first = record_session_question_active_time(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            delta_ms=1200,
        )

        second = record_session_question_active_time(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            delta_ms=800,
        )

        self.session_question.refresh_from_db()

        self.assertEqual(
            first.active_response_time_ms,
            1200,
        )

        self.assertEqual(
            second.active_response_time_ms,
            2000,
        )

        self.assertEqual(
            self.session_question.active_response_time_ms,
            2000,
        )

    def test_active_time_marks_question_as_viewed(self):
        self.assertIsNone(
            self.session_question.first_viewed_at
        )

        result = record_session_question_active_time(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            delta_ms=500,
        )

        self.session_question.refresh_from_db()

        self.assertIsNotNone(
            result.first_viewed_at
        )

        self.assertIsNotNone(
            self.session_question.first_viewed_at
        )

    def test_invalid_active_time_delta_is_rejected(self):
        invalid_values = [
            -1,
            1.5,
            True,
            None,
        ]

        for value in invalid_values:
            with self.subTest(
                    delta_ms=value
            ):
                with self.assertRaises(
                        LearningServiceError
                ):
                    record_session_question_active_time(
                        user=self.user,
                        session_question_id=(
                            self.session_question.pk
                        ),
                        delta_ms=value,
                    )

        self.session_question.refresh_from_db()

        self.assertEqual(
            self.session_question.active_response_time_ms,
            0,
        )

    def test_other_user_cannot_record_active_time(self):
        with self.assertRaises(
                LearningServiceError
        ):
            record_session_question_active_time(
                user=self.other_user,
                session_question_id=(
                    self.session_question.pk
                ),
                delta_ms=500,
            )

        self.session_question.refresh_from_db()

        self.assertEqual(
            self.session_question.active_response_time_ms,
            0,
        )

    def test_completed_session_rejects_active_time(self):
        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        with self.assertRaises(
                LearningServiceError
        ):
            record_session_question_active_time(
                user=self.user,
                session_question_id=(
                    self.session_question.pk
                ),
                delta_ms=500,
            )

        self.session_question.refresh_from_db()

        self.assertEqual(
            self.session_question.active_response_time_ms,
            0,
        )

    def test_complete_assessment_persists_average_response_time(
            self,
    ):
        record_session_question_active_time(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            delta_ms=1200,
        )

        record_session_question_active_time(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            delta_ms=800,
        )

        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        state = SkillMastery.objects.get(
            user=self.user,
            skill=self.skill,
        )

        self.assertEqual(
            state.avg_response_time_ms,
            Decimal("2000.00"),
        )


class AssessmentSessionServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="session-user@example.com",
            password="test-password-123",
        )

        cls.other_user = User.objects.create_user(
            email="session-other@example.com",
            password="test-password-123",
        )

    # =================================================
    # Start
    # =================================================

    def test_start_adaptive_session(self):
        result = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        self.assertEqual(
            result.mode,
            AssessmentSession.Mode.ADAPTIVE,
        )
        self.assertEqual(
            result.status,
            AssessmentSession.Status.ACTIVE,
        )

        session = AssessmentSession.objects.get(
            pk=result.session_id
        )

        self.assertEqual(
            session.user,
            self.user,
        )
        self.assertEqual(
            session.mode,
            AssessmentSession.Mode.ADAPTIVE,
        )
        self.assertEqual(
            session.status,
            AssessmentSession.Status.ACTIVE,
        )
        self.assertIsNone(
            session.ended_at
        )

    def test_start_diagnostic_session(self):
        result = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.DIAGNOSTIC,
        )

        self.assertEqual(
            result.mode,
            AssessmentSession.Mode.DIAGNOSTIC,
        )
        self.assertEqual(
            result.status,
            AssessmentSession.Status.ACTIVE,
        )

        session = AssessmentSession.objects.get(
            pk=result.session_id
        )

        self.assertEqual(
            session.user,
            self.user,
        )
        self.assertEqual(
            session.mode,
            AssessmentSession.Mode.DIAGNOSTIC,
        )
        self.assertEqual(
            session.status,
            AssessmentSession.Status.ACTIVE,
        )
        self.assertIsNone(
            session.ended_at
        )

    def test_lesson_mode_is_not_supported(self):
        with self.assertRaises(
            LearningServiceError
        ):
            start_session(
                user=self.user,
                mode="lesson",
            )

        self.assertEqual(
            AssessmentSession.objects.count(),
            0,
        )

    def test_unsupported_mode_is_rejected(self):
        with self.assertRaises(
            LearningServiceError
        ):
            start_session(
                user=self.user,
                mode="bad_mode",
            )

        self.assertEqual(
            AssessmentSession.objects.count(),
            0,
        )

    # =================================================
    # Finish
    # =================================================

    def test_complete_active_session(self):
        started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        result = complete_session(
            user=self.user,
            session_id=started.session_id,
        )

        self.assertEqual(
            result.status,
            AssessmentSession.Status.COMPLETED,
        )

        session = AssessmentSession.objects.get(
            pk=started.session_id
        )

        self.assertEqual(
            session.status,
            AssessmentSession.Status.COMPLETED,
        )
        self.assertIsNotNone(
            session.ended_at
        )

    def test_abandon_active_session(self):
        started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        result = abandon_session(
            user=self.user,
            session_id=started.session_id,
        )

        self.assertEqual(
            result.status,
            AssessmentSession.Status.ABANDONED,
        )

        session = AssessmentSession.objects.get(
            pk=started.session_id
        )

        self.assertEqual(
            session.status,
            AssessmentSession.Status.ABANDONED,
        )
        self.assertIsNotNone(
            session.ended_at
        )

    def test_other_user_cannot_finish_session(self):
        started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        with self.assertRaises(
            LearningServiceError
        ):
            complete_session(
                user=self.other_user,
                session_id=started.session_id,
            )

        session = AssessmentSession.objects.get(
            pk=started.session_id
        )

        self.assertEqual(
            session.status,
            AssessmentSession.Status.ACTIVE,
        )
        self.assertIsNone(
            session.ended_at
        )

    def test_finished_session_cannot_transition_again(self):
        started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        complete_session(
            user=self.user,
            session_id=started.session_id,
        )

        with self.assertRaises(
            LearningServiceError
        ):
            abandon_session(
                user=self.user,
                session_id=started.session_id,
            )

        session = AssessmentSession.objects.get(
            pk=started.session_id
        )

        self.assertEqual(
            session.status,
            AssessmentSession.Status.COMPLETED,
        )
        self.assertIsNotNone(
            session.ended_at
        )


class MasteryCalculationTests(TestCase):
    def setUp(self):
        self.as_of = timezone.now()

    def make_evidence(
        self,
        *,
        index,
        is_correct,
        weight="1.0",
        difficulty="0.50",
        days_ago=0,
        is_primary=True,
        active_response_time_ms=0,
    ):
        return SkillEvidence(
            session_question_id=index,
            session_id=index,
            sequence_number=index,

            question_id=index,
            source_id=f"CALC-{index}",

            skill_id=1,
            skill_code="TEST-01",
            is_primary=is_primary,
            skill_weight=Decimal(
                weight
            ),

            difficulty_score=Decimal(
                difficulty
            ),

            is_correct=is_correct,
            was_unanswered=False,

            active_response_time_ms=(
                active_response_time_ms
            ),

            evidence_at=(
                self.as_of
                - timedelta(
                    days=days_ago
                )
            ),

            final_attempt_id=index,
            final_attempt_number=1,
        )

    def test_cold_start_uses_prior_but_stores_null(self):
        result = calculate_mastery(
            [],
            as_of=self.as_of,
        )

        self.assertEqual(
            result.mastery_estimate,
            Decimal("0.50"),
        )

        self.assertIsNone(
            result.mastery_score
        )

        self.assertEqual(
            result.observation_count,
            0,
        )

        self.assertIsNone(
            result.last_evidence_at
        )

    def test_one_correct_observation_is_smoothed(self):
        evidence = [
            self.make_evidence(
                index=1,
                is_correct=True,
                difficulty="0.50",
            ),
        ]

        result = calculate_mastery(
            evidence,
            as_of=self.as_of,
        )

        self.assertAlmostEqual(
            result.mastery_estimate,
            Decimal("0.6666666667"),
            places=9,
        )

        # Fewer than 3 observations:
        # do not persist mastery_score.
        self.assertIsNone(
            result.mastery_score
        )

    def test_three_correct_observations_store_mastery(self):
        evidence = [
            self.make_evidence(
                index=1,
                is_correct=True,
            ),
            self.make_evidence(
                index=2,
                is_correct=True,
            ),
            self.make_evidence(
                index=3,
                is_correct=True,
            ),
        ]

        result = calculate_mastery(
            evidence,
            as_of=self.as_of,
        )

        # (2*0.5 + 3) / (2 + 3) = 0.8
        self.assertEqual(
            result.mastery_score,
            Decimal("0.80000"),
        )

        self.assertEqual(
            result.observation_count,
            3,
        )

    def test_no_inactivity_preserves_mastery_at_last_evidence(
            self,
    ):
        evidence = [
            self.make_evidence(
                index=1,
                is_correct=True,
            ),
            self.make_evidence(
                index=2,
                is_correct=True,
            ),
            self.make_evidence(
                index=3,
                is_correct=True,
            ),
        ]

        result = calculate_mastery(
            evidence,
            as_of=self.as_of,
        )

        # No inactivity:
        #
        # alpha=2, prior=.5, three correct
        #
        # (1 + 3) / (2 + 3) = .8
        self.assertEqual(
            result.mastery_score,
            Decimal("0.80000"),
        )

        self.assertEqual(
            result.last_evidence_at,
            self.as_of,
        )

    def test_strong_mastery_decays_from_last_evidence(
            self,
    ):
        evidence = [
            self.make_evidence(
                index=1,
                is_correct=True,
            ),
            self.make_evidence(
                index=2,
                is_correct=True,
            ),
            self.make_evidence(
                index=3,
                is_correct=True,
            ),
        ]

        at_last_evidence = calculate_mastery(
            evidence,
            as_of=self.as_of,
        )

        after_one_half_life = calculate_mastery(
            evidence,
            as_of=(
                    self.as_of
                    + timedelta(days=21)
            ),
        )

        after_two_half_lives = calculate_mastery(
            evidence,
            as_of=(
                    self.as_of
                    + timedelta(days=42)
            ),
        )

        self.assertEqual(
            at_last_evidence.mastery_score,
            Decimal("0.80000"),
        )

        # One half-life applies only to the amount
        # above the prior:
        #
        # .5 + (.8 - .5) * .5 = .65
        self.assertEqual(
            after_one_half_life.mastery_score,
            Decimal("0.65000"),
        )

        # Two half-lives:
        #
        # .5 + (.8 - .5) * .25 = .575
        self.assertEqual(
            after_two_half_lives.mastery_score,
            Decimal("0.57500"),
        )

        self.assertGreater(
            at_last_evidence.mastery_estimate,
            after_one_half_life.mastery_estimate,
        )

        self.assertGreater(
            after_one_half_life.mastery_estimate,
            after_two_half_lives.mastery_estimate,
        )

    def test_weak_mastery_does_not_improve_during_inactivity(
            self,
    ):
        evidence = [
            self.make_evidence(
                index=1,
                is_correct=False,
            ),
            self.make_evidence(
                index=2,
                is_correct=False,
            ),
            self.make_evidence(
                index=3,
                is_correct=False,
            ),
        ]

        at_last_evidence = calculate_mastery(
            evidence,
            as_of=self.as_of,
        )

        after_four_half_lives = calculate_mastery(
            evidence,
            as_of=(
                    self.as_of
                    + timedelta(days=84)
            ),
        )

        # alpha=2, prior=.5, three wrong:
        #
        # (1 + 0) / (2 + 3) = .2
        self.assertEqual(
            at_last_evidence.mastery_score,
            Decimal("0.20000"),
        )

        # Inactivity must never make a weak learner
        # look better merely by pulling the score
        # upward toward the prior.
        self.assertEqual(
            after_four_half_lives.mastery_score,
            Decimal("0.20000"),
        )

        self.assertEqual(
            after_four_half_lives.mastery_estimate,
            at_last_evidence.mastery_estimate,
        )

    def test_inactivity_decay_is_anchored_to_newest_evidence(
            self,
    ):
        evidence = [
            self.make_evidence(
                index=1,
                is_correct=True,
                days_ago=42,
            ),
            self.make_evidence(
                index=2,
                is_correct=True,
                days_ago=21,
            ),
        ]

        newest_evidence_at = (
                self.as_of
                - timedelta(days=21)
        )

        at_anchor = calculate_mastery(
            evidence,
            as_of=newest_evidence_at,
        )

        after_one_half_life = calculate_mastery(
            evidence,
            as_of=self.as_of,
        )

        self.assertEqual(
            at_anchor.last_evidence_at,
            newest_evidence_at,
        )

        self.assertEqual(
            after_one_half_life.last_evidence_at,
            newest_evidence_at,
        )

        # Exactly one inactivity half-life passed
        # after the newest evidence.
        #
        # Therefore only the distance above the
        # prior is halved.
        expected = (
                Decimal("0.50")
                +
                (
                        at_anchor.mastery_estimate
                        - Decimal("0.50")
                )
                / Decimal("2")
        )

        self.assertAlmostEqual(
            after_one_half_life.mastery_estimate,
            expected,
            places=9,
        )

    def test_new_evidence_resets_inactivity_anchor(
            self,
    ):
        stale_evidence = [
            self.make_evidence(
                index=1,
                is_correct=True,
                days_ago=42,
            ),
            self.make_evidence(
                index=2,
                is_correct=True,
                days_ago=42,
            ),
            self.make_evidence(
                index=3,
                is_correct=True,
                days_ago=42,
            ),
        ]

        stale_result = calculate_mastery(
            stale_evidence,
            as_of=self.as_of,
        )

        self.assertEqual(
            stale_result.mastery_score,
            Decimal("0.57500"),
        )

        refreshed_evidence = [
            *stale_evidence,
            self.make_evidence(
                index=4,
                is_correct=True,
                days_ago=0,
            ),
        ]

        refreshed_result = calculate_mastery(
            refreshed_evidence,
            as_of=self.as_of,
        )

        # The fresh observation becomes the new
        # inactivity anchor.
        self.assertEqual(
            refreshed_result.last_evidence_at,
            self.as_of,
        )

        self.assertGreater(
            refreshed_result.mastery_estimate,
            stale_result.mastery_estimate,
        )

        self.assertEqual(
            refreshed_result.algorithm_version,
            "rule_v2",
        )

    def test_difficulty_adjustment_has_correct_direction(self):
        hard_correct = calculate_mastery(
            [
                self.make_evidence(
                    index=1,
                    is_correct=True,
                    difficulty="0.90",
                )
            ],
            as_of=self.as_of,
        )

        easy_correct = calculate_mastery(
            [
                self.make_evidence(
                    index=2,
                    is_correct=True,
                    difficulty="0.10",
                )
            ],
            as_of=self.as_of,
        )

        self.assertGreater(
            hard_correct.mastery_estimate,
            easy_correct.mastery_estimate,
        )

        easy_wrong = calculate_mastery(
            [
                self.make_evidence(
                    index=3,
                    is_correct=False,
                    difficulty="0.10",
                )
            ],
            as_of=self.as_of,
        )

        hard_wrong = calculate_mastery(
            [
                self.make_evidence(
                    index=4,
                    is_correct=False,
                    difficulty="0.90",
                )
            ],
            as_of=self.as_of,
        )

        self.assertLess(
            easy_wrong.mastery_estimate,
            hard_wrong.mastery_estimate,
        )

    def test_documented_mastery_example_is_about_057(self):
        evidence = [
            self.make_evidence(
                index=1,
                is_correct=False,
                weight="1.0",
                difficulty="0.40",
                days_ago=20,
            ),
            self.make_evidence(
                index=2,
                is_correct=True,
                weight="1.0",
                difficulty="0.50",
                days_ago=13,
            ),
            self.make_evidence(
                index=3,
                is_correct=False,
                weight="0.6",
                difficulty="0.30",
                days_ago=6,
            ),
            self.make_evidence(
                index=4,
                is_correct=True,
                weight="1.0",
                difficulty="0.70",
                days_ago=0,
            ),
        ]

        result = calculate_mastery(
            evidence,
            as_of=self.as_of,
        )

        self.assertEqual(
            result.mastery_score,
            Decimal("0.56898"),
        )

    def test_weighted_accuracy_uses_skill_weights(self):
        evidence = [
            self.make_evidence(
                index=1,
                is_correct=True,
                weight="1.0",
            ),
            self.make_evidence(
                index=2,
                is_correct=False,
                weight="0.5",
            ),
        ]

        result = calculate_mastery(
            evidence,
            as_of=self.as_of,
        )

        self.assertEqual(
            result.historical_accuracy,
            Decimal("0.66667"),
        )

        self.assertEqual(
            result.recent_accuracy,
            Decimal("0.66667"),
        )

        self.assertEqual(
            result.correct_attempts,
            1,
        )

    def test_recent_accuracy_uses_last_ten_observations(self):
        evidence = [
            self.make_evidence(
                index=1,
                is_correct=True,
            )
        ]

        for index in range(2, 12):
            evidence.append(
                self.make_evidence(
                    index=index,
                    is_correct=False,
                )
            )

        result = calculate_mastery(
            evidence,
            as_of=self.as_of,
        )

        # All 11 observations: 1 correct / 11.
        self.assertEqual(
            result.historical_accuracy,
            Decimal("0.09091"),
        )

        # The 10 most recent observations
        # are all incorrect.
        self.assertEqual(
            result.recent_accuracy,
            Decimal("0.00000"),
        )

    def test_consecutive_errors_ignore_low_weight_secondary_evidence(
        self,
    ):
        evidence = [
            self.make_evidence(
                index=1,
                is_correct=True,
                is_primary=True,
            ),
            self.make_evidence(
                index=2,
                is_correct=False,
                is_primary=True,
            ),
            self.make_evidence(
                index=3,
                is_correct=False,
                is_primary=False,
                weight="0.3",
            ),
            self.make_evidence(
                index=4,
                is_correct=False,
                is_primary=True,
            ),
        ]

        result = calculate_mastery(
            evidence,
            as_of=self.as_of,
        )

        self.assertEqual(
            result.consecutive_errors,
            2,
        )

    def test_average_response_time_uses_viewed_observations(
            self,
    ):
        evidence = [
            self.make_evidence(
                index=1,
                is_correct=True,
                active_response_time_ms=1000,
            ),
            self.make_evidence(
                index=2,
                is_correct=False,
                active_response_time_ms=3000,
            ),
        ]

        result = calculate_mastery(
            evidence,
            as_of=self.as_of,
        )

        self.assertEqual(
            result.avg_response_time_ms,
            Decimal("2000.00"),
        )

class SkillMasteryLazyRefreshTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="lazy-mastery@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Lazy Refresh Topic",
            category="grammar",
            display_order=9999,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="LAZY-01",
            skill_name="Lazy Refresh Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

    def setUp(self):
        self.as_of = timezone.now()

        self.current_version = str(
            settings.MASTERY_RULE_V1[
                "algorithm_version"
            ]
        )

    def create_existing_state(
        self,
        *,
        calculated_at,
        algorithm_version=None,
    ):
        if algorithm_version is None:
            algorithm_version = (
                self.current_version
            )

        return SkillMastery.objects.create(
            user=self.user,
            skill=self.skill,

            mastery_score=Decimal("0.80000"),

            observation_count=3,
            correct_attempts=3,

            recent_accuracy=Decimal("1.00000"),
            historical_accuracy=Decimal("1.00000"),

            consecutive_errors=0,

            calculated_at=calculated_at,

            algorithm_version=(
                algorithm_version
            ),
        )

    def test_missing_mastery_is_created_on_read(
        self,
    ):
        self.assertFalse(
            SkillMastery.objects.filter(
                user=self.user,
                skill=self.skill,
            ).exists()
        )

        state = ensure_skill_mastery_current(
            user=self.user,
            skill_id=self.skill.pk,
            as_of=self.as_of,
        )

        self.assertEqual(
            state.user_id,
            self.user.pk,
        )

        self.assertEqual(
            state.skill_id,
            self.skill.pk,
        )

        # No assessment evidence exists.
        self.assertEqual(
            state.observation_count,
            0,
        )

        self.assertIsNone(
            state.mastery_score
        )

        self.assertEqual(
            state.calculated_at,
            self.as_of,
        )

        self.assertEqual(
            state.algorithm_version,
            self.current_version,
        )

    def test_fresh_mastery_is_not_recalculated(
        self,
    ):
        original_calculated_at = (
            self.as_of
            - timedelta(hours=23)
        )

        original = self.create_existing_state(
            calculated_at=(
                original_calculated_at
            ),
        )

        state = ensure_skill_mastery_current(
            user=self.user,
            skill_id=self.skill.pk,
            as_of=self.as_of,
        )

        self.assertEqual(
            state.pk,
            original.pk,
        )

        # Fresh snapshot is returned unchanged.
        self.assertEqual(
            state.calculated_at,
            original_calculated_at,
        )

        self.assertEqual(
            state.mastery_score,
            Decimal("0.80000"),
        )

        self.assertEqual(
            state.observation_count,
            3,
        )

    def test_mastery_refreshes_at_24_hour_boundary(
        self,
    ):
        original = self.create_existing_state(
            calculated_at=(
                self.as_of
                - timedelta(hours=24)
            ),
        )

        state = ensure_skill_mastery_current(
            user=self.user,
            skill_id=self.skill.pk,
            as_of=self.as_of,
        )

        self.assertEqual(
            state.pk,
            original.pk,
        )

        # No real assessment evidence exists, so
        # successful recalculation returns cold start.
        self.assertEqual(
            state.observation_count,
            0,
        )

        self.assertIsNone(
            state.mastery_score
        )

        self.assertEqual(
            state.calculated_at,
            self.as_of,
        )

    def test_old_algorithm_version_forces_refresh(
        self,
    ):
        original = self.create_existing_state(
            calculated_at=self.as_of,
            algorithm_version="rule_v1",
        )

        state = ensure_skill_mastery_current(
            user=self.user,
            skill_id=self.skill.pk,
            as_of=self.as_of,
        )

        self.assertEqual(
            state.pk,
            original.pk,
        )

        # Fresh by time, but obsolete by algorithm.
        # Therefore it must still be rebuilt.
        self.assertEqual(
            state.algorithm_version,
            self.current_version,
        )

        self.assertEqual(
            state.calculated_at,
            self.as_of,
        )

        self.assertEqual(
            state.observation_count,
            0,
        )

        self.assertIsNone(
            state.mastery_score
        )

class SkillPriorityCalculationTests(TestCase):
    def setUp(self):
        self.as_of = timezone.now()

    def test_unseen_skill_uses_weakness_and_uncertainty_only(
        self,
    ):
        result = calculate_skill_priority(
            mastery_estimate=Decimal("0.50"),
            observation_count=0,
            consecutive_errors=0,
            last_evidence_at=None,
            last_learning_activity_at=None,
            as_of=self.as_of,
        )

        self.assertEqual(
            result.weakness,
            Decimal("0.50000"),
        )

        self.assertEqual(
            result.recurring_errors,
            Decimal("0.00000"),
        )

        # No previous assessment means there is
        # nothing to review yet.
        self.assertEqual(
            result.review_due,
            Decimal("0.00000"),
        )

        self.assertEqual(
            result.uncertainty,
            Decimal("1.00000"),
        )

        self.assertEqual(
            result.recent_learning,
            Decimal("0.00000"),
        )

        # .40*.5 + .10*1 = .30
        self.assertEqual(
            result.priority_score,
            Decimal("0.30000"),
        )

    def test_recurring_errors_saturate_at_three(
        self,
    ):
        two_errors = calculate_skill_priority(
            mastery_estimate=Decimal("0.50"),
            observation_count=3,
            consecutive_errors=2,
            as_of=self.as_of,
        )

        three_errors = calculate_skill_priority(
            mastery_estimate=Decimal("0.50"),
            observation_count=3,
            consecutive_errors=3,
            as_of=self.as_of,
        )

        self.assertEqual(
            two_errors.recurring_errors,
            Decimal("0.66667"),
        )

        self.assertEqual(
            three_errors.recurring_errors,
            Decimal("1.00000"),
        )

    def test_review_due_is_half_after_fourteen_days(
        self,
    ):
        result = calculate_skill_priority(
            mastery_estimate=Decimal("0.50"),
            observation_count=3,
            consecutive_errors=0,
            last_evidence_at=(
                self.as_of
                - timedelta(days=14)
            ),
            as_of=self.as_of,
        )

        self.assertEqual(
            result.review_due,
            Decimal("0.50000"),
        )

    def test_zero_day_old_evidence_is_not_review_due(
        self,
    ):
        result = calculate_skill_priority(
            mastery_estimate=Decimal("0.50"),
            observation_count=3,
            consecutive_errors=0,
            last_evidence_at=self.as_of,
            as_of=self.as_of,
        )

        self.assertEqual(
            result.review_due,
            Decimal("0.00000"),
        )

    def test_recent_learning_counts_at_exactly_seven_days(
        self,
    ):
        result = calculate_skill_priority(
            mastery_estimate=Decimal("0.50"),
            observation_count=0,
            consecutive_errors=0,
            last_evidence_at=None,
            last_learning_activity_at=(
                self.as_of
                - timedelta(days=7)
            ),
            as_of=self.as_of,
        )

        self.assertEqual(
            result.recent_learning,
            Decimal("1.00000"),
        )

        # Cold start .30 + recent-learning .15.
        self.assertEqual(
            result.priority_score,
            Decimal("0.45000"),
        )

    def test_learning_older_than_seven_days_does_not_count(
        self,
    ):
        result = calculate_skill_priority(
            mastery_estimate=Decimal("0.50"),
            observation_count=0,
            consecutive_errors=0,
            last_evidence_at=None,
            last_learning_activity_at=(
                self.as_of
                - timedelta(
                    days=7,
                    microseconds=1,
                )
            ),
            as_of=self.as_of,
        )

        self.assertEqual(
            result.recent_learning,
            Decimal("0.00000"),
        )

    def test_assessment_after_learning_clears_learning_boost(
        self,
    ):
        learning_at = (
            self.as_of
            - timedelta(days=2)
        )

        assessment_at = (
            self.as_of
            - timedelta(days=1)
        )

        result = calculate_skill_priority(
            mastery_estimate=Decimal("0.50"),
            observation_count=3,
            consecutive_errors=0,
            last_evidence_at=assessment_at,
            last_learning_activity_at=learning_at,
            as_of=self.as_of,
        )

        self.assertEqual(
            result.recent_learning,
            Decimal("0.00000"),
        )

    def test_learning_after_assessment_creates_learning_boost(
        self,
    ):
        assessment_at = (
            self.as_of
            - timedelta(days=4)
        )

        learning_at = (
            self.as_of
            - timedelta(days=2)
        )

        result = calculate_skill_priority(
            mastery_estimate=Decimal("0.50"),
            observation_count=3,
            consecutive_errors=0,
            last_evidence_at=assessment_at,
            last_learning_activity_at=learning_at,
            as_of=self.as_of,
        )

        self.assertEqual(
            result.recent_learning,
            Decimal("1.00000"),
        )

    def test_full_priority_formula(
        self,
    ):
        result = calculate_skill_priority(
            mastery_estimate=Decimal("0.20"),
            observation_count=3,
            consecutive_errors=2,

            last_evidence_at=(
                self.as_of
                - timedelta(days=14)
            ),

            last_learning_activity_at=(
                self.as_of
                - timedelta(days=2)
            ),

            as_of=self.as_of,
        )

        self.assertEqual(
            result.weakness,
            Decimal("0.80000"),
        )

        self.assertEqual(
            result.recurring_errors,
            Decimal("0.66667"),
        )

        self.assertEqual(
            result.review_due,
            Decimal("0.50000"),
        )

        self.assertEqual(
            result.uncertainty,
            Decimal("0.40000"),
        )

        self.assertEqual(
            result.recent_learning,
            Decimal("1.00000"),
        )

        # .40*.8
        # + .20*(2/3)
        # + .15*.5
        # + .10*.4
        # + .15*1
        # = .718333...
        self.assertEqual(
            result.priority_score,
            Decimal("0.71833"),
        )

class UserSkillPriorityIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="priority-db@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Priority DB Topic",
            category="grammar",
            display_order=10000,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="PRIORITY-01",
            skill_name="Priority DB Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.lesson = Lesson.objects.create(
            title="Priority DB Lesson",
            lesson_type="grammar",
            content={
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=10000,
            is_active=True,
        )

        LessonSkill.objects.create(
            lesson=cls.lesson,
            skill=cls.skill,
        )

        cls.question = Question.objects.create(
            source_id="PRIORITY-Q-001",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text=(
                "Priority integration test question."
            ),
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.5000"),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=cls.question,
            skill=cls.skill,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

        QuestionOption.objects.create(
            question=cls.question,
            option_key="A",
            option_text="Wrong",
            misconception_code="priority_wrong",
            is_correct=False,
            display_order=1,
        )

        QuestionOption.objects.create(
            question=cls.question,
            option_key="B",
            option_text="Correct",
            misconception_code=None,
            is_correct=True,
            display_order=2,
        )

        QuestionOption.objects.create(
            question=cls.question,
            option_key="C",
            option_text="Wrong 2",
            misconception_code="priority_wrong",
            is_correct=False,
            display_order=3,
        )

    def setUp(self):
        self.as_of = timezone.now()

    def create_assessment_evidence(
        self,
        *,
        selected_option_key,
        ended_at=None,
    ):
        if ended_at is None:
            ended_at = self.as_of

        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

        added = add_question_to_session(
            user=self.user,
            session_id=session.pk,
            question_id=self.question.pk,
        )

        session_question = (
            SessionQuestion.objects.get(
                pk=added.session_question_id
            )
        )

        save_option_answer(
            user=self.user,
            session_question_id=(
                session_question.pk
            ),
            selected_option_key=(
                selected_option_key
            ),
        )

        complete_session(
            user=self.user,
            session_id=session.pk,
        )

        # The service correctly completes the
        # assessment at current time. For deterministic
        # priority tests we then place the completed
        # evidence at the explicit test timestamp.
        AssessmentSession.objects.filter(
            pk=session.pk
        ).update(
            ended_at=ended_at
        )

        session.refresh_from_db()

        return session

    def create_learning_activity(
        self,
        *,
        ended_at,
    ):
        study_session = (
            StudySession.objects.create(
                user=self.user,
                lesson=self.lesson,
                assessment_session=None,
                activity_type=(
                    StudySession.ActivityType.LESSON
                ),
                status=(
                    StudySession.Status.COMPLETED
                ),
                ended_at=ended_at,
                last_activity_at=ended_at,
            )
        )

        record_learning_completion_event(
            user=self.user,
            study_session_id=study_session.pk,
        )

        return study_session

    def test_cold_start_priority_is_built_from_database(
        self,
    ):
        result = build_user_skill_priority(
            user=self.user,
            skill_id=self.skill.pk,
            as_of=self.as_of,
        )

        self.assertEqual(
            result.skill_id,
            self.skill.pk,
        )

        self.assertEqual(
            result.skill_code,
            "PRIORITY-01",
        )

        self.assertEqual(
            result.mastery_estimate,
            Decimal("0.50"),
        )

        self.assertEqual(
            result.observation_count,
            0,
        )

        self.assertEqual(
            result.consecutive_errors,
            0,
        )

        self.assertIsNone(
            result.last_evidence_at
        )

        self.assertIsNone(
            result.last_learning_activity_at
        )

        self.assertEqual(
            result.priority_score,
            Decimal("0.30000"),
        )

    def test_one_correct_answer_personalizes_priority_before_mastery_is_persistable(
        self,
    ):
        self.create_assessment_evidence(
            selected_option_key="B",
            ended_at=self.as_of,
        )

        result = build_user_skill_priority(
            user=self.user,
            skill_id=self.skill.pk,
            as_of=self.as_of,
        )

        self.assertEqual(
            result.observation_count,
            1,
        )

        # alpha=2, prior=.5, one correct:
        #
        # (1 + 1) / (2 + 1) = 2/3
        self.assertAlmostEqual(
            result.mastery_estimate,
            Decimal("0.6666666667"),
            places=9,
        )

        self.assertEqual(
            result.consecutive_errors,
            0,
        )

        self.assertEqual(
            result.last_evidence_at,
            self.as_of,
        )

        # W = 1/3
        # U = 2/3
        #
        # .40*(1/3) + .10*(2/3)
        # = .20
        self.assertEqual(
            result.priority_score,
            Decimal("0.20000"),
        )

        # This is the important integration rule:
        # priority already uses personalized M-hat even
        # though n < 3 means persisted mastery_score
        # remains NULL.
        state = SkillMastery.objects.get(
            user=self.user,
            skill=self.skill,
        )

        self.assertIsNone(
            state.mastery_score
        )

    def test_incorrect_answer_increases_error_priority(
        self,
    ):
        self.create_assessment_evidence(
            selected_option_key="A",
            ended_at=self.as_of,
        )

        result = build_user_skill_priority(
            user=self.user,
            skill_id=self.skill.pk,
            as_of=self.as_of,
        )

        self.assertEqual(
            result.observation_count,
            1,
        )

        self.assertAlmostEqual(
            result.mastery_estimate,
            Decimal("0.3333333333"),
            places=9,
        )

        self.assertEqual(
            result.consecutive_errors,
            1,
        )

        self.assertEqual(
            result.recurring_errors,
            Decimal("0.33333"),
        )

        # W = 2/3
        # E = 1/3
        # U = 2/3
        #
        # .40*(2/3)
        # + .20*(1/3)
        # + .10*(2/3)
        # = .40
        self.assertEqual(
            result.priority_score,
            Decimal("0.40000"),
        )

    def test_recent_learning_activity_adds_priority_boost(
        self,
    ):
        learning_at = (
            self.as_of
            - timedelta(days=2)
        )

        self.create_learning_activity(
            ended_at=learning_at,
        )

        result = build_user_skill_priority(
            user=self.user,
            skill_id=self.skill.pk,
            as_of=self.as_of,
        )

        self.assertEqual(
            result.observation_count,
            0,
        )

        self.assertEqual(
            result.last_learning_activity_at,
            learning_at,
        )

        self.assertEqual(
            result.recent_learning,
            Decimal("1.00000"),
        )

        # Cold-start base .30
        # + recent-learning boost .15.
        self.assertEqual(
            result.priority_score,
            Decimal("0.45000"),
        )

    def test_assessment_after_learning_removes_learning_boost(
        self,
    ):
        learning_at = (
            self.as_of
            - timedelta(days=2)
        )

        assessment_at = (
            self.as_of
            - timedelta(days=1)
        )

        self.create_learning_activity(
            ended_at=learning_at,
        )

        self.create_assessment_evidence(
            selected_option_key="B",
            ended_at=assessment_at,
        )

        result = build_user_skill_priority(
            user=self.user,
            skill_id=self.skill.pk,
            as_of=self.as_of,
        )

        self.assertEqual(
            result.last_learning_activity_at,
            learning_at,
        )

        self.assertEqual(
            result.last_evidence_at,
            assessment_at,
        )

        # A later assessment has already checked
        # the skill after the learning activity.
        self.assertEqual(
            result.recent_learning,
            Decimal("0.00000"),
        )

        self.assertEqual(
            result.observation_count,
            1,
        )

class UserSkillPriorityRankingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="priority-ranking@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Priority Ranking Topic",
            category="grammar",
            display_order=11000,
            is_active=True,
        )

        # -----------------------------------------
        # Skill A
        # -----------------------------------------

        cls.skill_a = Skill.objects.create(
            topic=cls.topic,
            skill_code="RANK-A",
            skill_name="Ranking Skill A",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        # -----------------------------------------
        # Skill B
        # -----------------------------------------

        cls.skill_b = Skill.objects.create(
            topic=cls.topic,
            skill_code="RANK-B",
            skill_name="Ranking Skill B",
            cefr_level="A1",
            is_extension=False,
            display_order=2,
            is_active=True,
        )

        cls.question_b = Question.objects.create(
            source_id="RANK-Q-B-001",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text=(
                "Ranking evidence test question."
            ),
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.5000"),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=cls.question_b,
            skill=cls.skill_b,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

        QuestionOption.objects.create(
            question=cls.question_b,
            option_key="A",
            option_text="Wrong",
            misconception_code="rank_wrong",
            is_correct=False,
            display_order=1,
        )

        QuestionOption.objects.create(
            question=cls.question_b,
            option_key="B",
            option_text="Correct",
            misconception_code=None,
            is_correct=True,
            display_order=2,
        )

        QuestionOption.objects.create(
            question=cls.question_b,
            option_key="C",
            option_text="Wrong 2",
            misconception_code="rank_wrong",
            is_correct=False,
            display_order=3,
        )

    def setUp(self):
        self.as_of = timezone.now()

    def test_equal_priorities_use_deterministic_skill_code_tiebreak(
        self,
    ):
        ranked = rank_user_skill_priorities(
            user=self.user,
            skill_ids=[
                self.skill_b.pk,
                self.skill_a.pk,
            ],
            as_of=self.as_of,
        )

        self.assertEqual(
            len(ranked),
            2,
        )

        # Both skills are unseen.
        #
        # M-hat = .5
        # W = .5
        # U = 1
        #
        # P = .40*.5 + .10*1
        #   = .30
        self.assertEqual(
            ranked[0].priority_score,
            Decimal("0.30000"),
        )

        self.assertEqual(
            ranked[1].priority_score,
            Decimal("0.30000"),
        )

        # Input order was B, A.
        #
        # Equal scores must therefore be resolved
        # by canonical skill_code ordering.
        self.assertEqual(
            [
                row.skill_code
                for row in ranked
            ],
            [
                "RANK-A",
                "RANK-B",
            ],
        )

    def test_select_top_priority_returns_first_ranked_skill(
        self,
    ):
        selected = select_top_priority_skill(
            user=self.user,
            skill_ids=[
                self.skill_b.pk,
                self.skill_a.pk,
            ],
            as_of=self.as_of,
        )

        self.assertIsNotNone(
            selected
        )

        self.assertEqual(
            selected.skill_id,
            self.skill_a.pk,
        )

        self.assertEqual(
            selected.skill_code,
            "RANK-A",
        )

        self.assertEqual(
            selected.priority_score,
            Decimal("0.30000"),
        )

    def test_empty_candidate_set_returns_no_selection(
        self,
    ):
        ranked = rank_user_skill_priorities(
            user=self.user,
            skill_ids=[],
            as_of=self.as_of,
        )

        self.assertEqual(
            ranked,
            (),
        )

        selected = select_top_priority_skill(
            user=self.user,
            skill_ids=[],
            as_of=self.as_of,
        )

        self.assertIsNone(
            selected
        )

    def test_duplicate_candidate_skill_is_rejected(
        self,
    ):
        with self.assertRaises(
            ValueError
        ):
            rank_user_skill_priorities(
                user=self.user,
                skill_ids=[
                    self.skill_a.pk,
                    self.skill_a.pk,
                ],
                as_of=self.as_of,
            )

    def test_real_evidence_priority_overrides_tiebreak(
        self,
    ):
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

        added = add_question_to_session(
            user=self.user,
            session_id=session.pk,
            question_id=self.question_b.pk,
        )

        save_option_answer(
            user=self.user,
            session_question_id=(
                added.session_question_id
            ),
            selected_option_key="A",
        )

        complete_session(
            user=self.user,
            session_id=session.pk,
        )

        # Freeze evidence time exactly at ranking as_of.
        AssessmentSession.objects.filter(
            pk=session.pk
        ).update(
            ended_at=self.as_of
        )

        ranked = rank_user_skill_priorities(
            user=self.user,
            skill_ids=[
                self.skill_a.pk,
                self.skill_b.pk,
            ],
            as_of=self.as_of,
        )

        self.assertEqual(
            len(ranked),
            2,
        )

        # -----------------------------------------
        # Skill B
        #
        # One incorrect observation:
        #
        # M-hat = 1/3
        # W = 2/3
        # E = 1/3
        # U = 2/3
        # R = 0
        # L = 0
        #
        # P =
        #   .40*(2/3)
        # + .20*(1/3)
        # + .10*(2/3)
        # = .40
        # -----------------------------------------

        self.assertEqual(
            ranked[0].skill_id,
            self.skill_b.pk,
        )

        self.assertEqual(
            ranked[0].skill_code,
            "RANK-B",
        )

        self.assertEqual(
            ranked[0].priority_score,
            Decimal("0.40000"),
        )

        # -----------------------------------------
        # Skill A
        #
        # Still unseen cold start:
        #
        # P = .30
        # -----------------------------------------

        self.assertEqual(
            ranked[1].skill_id,
            self.skill_a.pk,
        )

        self.assertEqual(
            ranked[1].skill_code,
            "RANK-A",
        )

        self.assertEqual(
            ranked[1].priority_score,
            Decimal("0.30000"),
        )

        # Important:
        #
        # Alphabetically A would win a tie,
        # but actual priority must dominate
        # the deterministic tie-break.
        self.assertGreater(
            ranked[0].priority_score,
            ranked[1].priority_score,
        )

        selected = select_top_priority_skill(
            user=self.user,
            skill_ids=[
                self.skill_a.pk,
                self.skill_b.pk,
            ],
            as_of=self.as_of,
        )

        self.assertIsNotNone(
            selected
        )

        self.assertEqual(
            selected.skill_id,
            self.skill_b.pk,
        )

        self.assertEqual(
            selected.priority_score,
            Decimal("0.40000"),
        )

class QuestionSelectionCalculationTests(TestCase):
    def make_candidate(
        self,
        *,
        question_id,
        source_id,
        difficulty,
        is_primary=True,
        was_seen_before=False,
    ):
        return QuestionCandidate(
            question_id=question_id,
            source_id=source_id,
            difficulty_score=Decimal(
                str(difficulty)
            ),
            is_primary=is_primary,
            was_seen_before=was_seen_before,
        )

    def test_nearest_difficulty_wins_when_other_factors_equal(
        self,
    ):
        candidates = [
            self.make_candidate(
                question_id=1,
                source_id="QS-001",
                difficulty="0.20",
            ),
            self.make_candidate(
                question_id=2,
                source_id="QS-002",
                difficulty="0.55",
            ),
            self.make_candidate(
                question_id=3,
                source_id="QS-003",
                difficulty="0.80",
            ),
        ]

        ranked = rank_question_candidates(
            candidates=candidates,
            target_difficulty=Decimal("0.50"),
        )

        self.assertEqual(
            ranked[0].question_id,
            2,
        )

        self.assertEqual(
            ranked[0].difficulty_distance,
            Decimal("0.05"),
        )

    def test_primary_mapping_beats_secondary_even_when_secondary_is_closer(
        self,
    ):
        candidates = [
            self.make_candidate(
                question_id=1,
                source_id="QS-PRIMARY",
                difficulty="0.20",
                is_primary=True,
            ),
            self.make_candidate(
                question_id=2,
                source_id="QS-SECONDARY",
                difficulty="0.50",
                is_primary=False,
            ),
        ]

        ranked = rank_question_candidates(
            candidates=candidates,
            target_difficulty=Decimal("0.50"),
        )

        self.assertEqual(
            ranked[0].question_id,
            1,
        )

        self.assertTrue(
            ranked[0].is_primary
        )

        self.assertEqual(
            ranked[0].difficulty_distance,
            Decimal("0.30"),
        )

    def test_unseen_question_beats_seen_question_before_difficulty(
        self,
    ):
        candidates = [
            self.make_candidate(
                question_id=1,
                source_id="QS-SEEN",
                difficulty="0.50",
                is_primary=True,
                was_seen_before=True,
            ),
            self.make_candidate(
                question_id=2,
                source_id="QS-UNSEEN",
                difficulty="0.80",
                is_primary=True,
                was_seen_before=False,
            ),
        ]

        ranked = rank_question_candidates(
            candidates=candidates,
            target_difficulty=Decimal("0.50"),
        )

        self.assertEqual(
            ranked[0].question_id,
            2,
        )

        self.assertFalse(
            ranked[0].was_seen_before
        )

    def test_cold_start_target_point_five_selects_mid_difficulty(
        self,
    ):
        candidates = [
            self.make_candidate(
                question_id=1,
                source_id="QS-EASY",
                difficulty="0.10",
            ),
            self.make_candidate(
                question_id=2,
                source_id="QS-MID",
                difficulty="0.50",
            ),
            self.make_candidate(
                question_id=3,
                source_id="QS-HARD",
                difficulty="0.90",
            ),
        ]

        selected = select_top_question_candidate(
            candidates=candidates,
            target_difficulty=Decimal("0.50"),
        )

        self.assertIsNotNone(
            selected
        )

        self.assertEqual(
            selected.question_id,
            2,
        )

        self.assertEqual(
            selected.target_difficulty,
            Decimal("0.50"),
        )

        self.assertEqual(
            selected.difficulty_distance,
            Decimal("0.00"),
        )

    def test_equal_candidates_use_source_id_tiebreak(
        self,
    ):
        candidates = [
            self.make_candidate(
                question_id=2,
                source_id="QS-B",
                difficulty="0.50",
            ),
            self.make_candidate(
                question_id=1,
                source_id="QS-A",
                difficulty="0.50",
            ),
        ]

        ranked = rank_question_candidates(
            candidates=candidates,
            target_difficulty=Decimal("0.50"),
        )

        self.assertEqual(
            [
                row.source_id
                for row in ranked
            ],
            [
                "QS-A",
                "QS-B",
            ],
        )

    def test_empty_candidate_set_returns_no_selection(
        self,
    ):
        ranked = rank_question_candidates(
            candidates=[],
            target_difficulty=Decimal("0.50"),
        )

        self.assertEqual(
            ranked,
            (),
        )

        selected = select_top_question_candidate(
            candidates=[],
            target_difficulty=Decimal("0.50"),
        )

        self.assertIsNone(
            selected
        )

class QuestionCandidateDBIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="question-candidate@example.com",
            password="test-password-123",
        )

        cls.other_user = User.objects.create_user(
            email="question-candidate-other@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Question Candidate Topic",
            category="grammar",
            display_order=12000,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="QCAND-01",
            skill_name="Question Candidate Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.other_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="QCAND-02",
            skill_name="Other Question Candidate Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=2,
            is_active=True,
        )

        # -----------------------------------------
        # Active primary questions for selected Skill
        # -----------------------------------------

        cls.question_mid = cls.create_primary_question(
            source_id="QCAND-MID",
            difficulty="0.5000",
            skill=cls.skill,
        )

        cls.question_easy = cls.create_primary_question(
            source_id="QCAND-EASY",
            difficulty="0.2000",
            skill=cls.skill,
        )

        # -----------------------------------------
        # Inactive Question:
        # must never become a candidate.
        # -----------------------------------------

        cls.question_inactive = (
            cls.create_primary_question(
                source_id="QCAND-INACTIVE",
                difficulty="0.5000",
                skill=cls.skill,
                is_active=False,
            )
        )

        # -----------------------------------------
        # Active, but linked only to another Skill:
        # must not become a candidate.
        # -----------------------------------------

        cls.question_unrelated = (
            cls.create_primary_question(
                source_id="QCAND-OTHER",
                difficulty="0.5000",
                skill=cls.other_skill,
            )
        )

        # -----------------------------------------
        # Multi-skill Question.
        #
        # other_skill = primary .60
        # selected skill = secondary .40
        #
        # It is still eligible for selected Skill,
        # but must carry is_primary=False.
        # -----------------------------------------

        cls.question_secondary = Question.objects.create(
            source_id="QCAND-SECONDARY",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text=(
                "Secondary Skill candidate question."
            ),
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.6000"),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=cls.question_secondary,
            skill=cls.other_skill,
            is_primary=True,
            weight=Decimal("0.60000"),
        )

        QuestionSkill.objects.create(
            question=cls.question_secondary,
            skill=cls.skill,
            is_primary=False,
            weight=Decimal("0.40000"),
        )

    @classmethod
    def create_primary_question(
        cls,
        *,
        source_id,
        difficulty,
        skill,
        is_active=True,
    ):
        question = Question.objects.create(
            source_id=source_id,
            question_type="multiple_choice",
            item_format="single_choice",
            question_text=(
                f"Candidate question {source_id}."
            ),
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal(
                difficulty
            ),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=is_active,
        )

        QuestionSkill.objects.create(
            question=question,
            skill=skill,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

        return question

    def setUp(self):
        self.session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

    def get_candidate(
        self,
        *,
        question_id,
    ):
        candidates = build_question_candidates(
            user=self.user,
            session_id=self.session.pk,
            skill_id=self.skill.pk,
        )

        return next(
            (
                candidate
                for candidate in candidates
                if candidate.question_id == question_id
            ),
            None,
        )

    def test_only_active_questions_linked_to_selected_skill_are_returned(
        self,
    ):
        candidates = build_question_candidates(
            user=self.user,
            session_id=self.session.pk,
            skill_id=self.skill.pk,
        )

        candidate_ids = {
            candidate.question_id
            for candidate in candidates
        }

        self.assertIn(
            self.question_mid.pk,
            candidate_ids,
        )

        self.assertIn(
            self.question_easy.pk,
            candidate_ids,
        )

        self.assertIn(
            self.question_secondary.pk,
            candidate_ids,
        )

        self.assertNotIn(
            self.question_inactive.pk,
            candidate_ids,
        )

        self.assertNotIn(
            self.question_unrelated.pk,
            candidate_ids,
        )

        secondary = self.get_candidate(
            question_id=self.question_secondary.pk,
        )

        self.assertIsNotNone(
            secondary
        )

        self.assertFalse(
            secondary.is_primary
        )

    def test_question_already_in_current_session_is_excluded(
        self,
    ):
        add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.question_mid.pk,
        )

        candidates = build_question_candidates(
            user=self.user,
            session_id=self.session.pk,
            skill_id=self.skill.pk,
        )

        candidate_ids = {
            candidate.question_id
            for candidate in candidates
        }

        self.assertNotIn(
            self.question_mid.pk,
            candidate_ids,
        )

        # Other eligible questions remain available.
        self.assertIn(
            self.question_easy.pk,
            candidate_ids,
        )

    def test_previously_viewed_question_is_marked_seen(
        self,
    ):
        old_session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

        added = add_question_to_session(
            user=self.user,
            session_id=old_session.pk,
            question_id=self.question_mid.pk,
        )

        mark_session_question_viewed(
            user=self.user,
            session_question_id=(
                added.session_question_id
            ),
        )

        complete_session(
            user=self.user,
            session_id=old_session.pk,
        )

        candidate = self.get_candidate(
            question_id=self.question_mid.pk,
        )

        self.assertIsNotNone(
            candidate
        )

        self.assertTrue(
            candidate.was_seen_before
        )

    def test_previously_selected_but_never_viewed_is_not_seen(
        self,
    ):
        old_session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

        add_question_to_session(
            user=self.user,
            session_id=old_session.pk,
            question_id=self.question_mid.pk,
        )

        # Deliberately never call
        # mark_session_question_viewed().
        complete_session(
            user=self.user,
            session_id=old_session.pk,
        )

        candidate = self.get_candidate(
            question_id=self.question_mid.pk,
        )

        self.assertIsNotNone(
            candidate
        )

        # Selection alone does not mean that the
        # learner actually saw the Question.
        self.assertFalse(
            candidate.was_seen_before
        )

    def test_other_users_history_does_not_mark_question_seen(
        self,
    ):
        other_session = AssessmentSession.objects.create(
            user=self.other_user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

        added = add_question_to_session(
            user=self.other_user,
            session_id=other_session.pk,
            question_id=self.question_mid.pk,
        )

        mark_session_question_viewed(
            user=self.other_user,
            session_question_id=(
                added.session_question_id
            ),
        )

        complete_session(
            user=self.other_user,
            session_id=other_session.pk,
        )

        candidate = self.get_candidate(
            question_id=self.question_mid.pk,
        )

        self.assertIsNotNone(
            candidate
        )

        # Historical exposure is user-specific.
        self.assertFalse(
            candidate.was_seen_before
        )

class AdaptiveQuestionSelectionIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="adaptive-selection@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Adaptive Selection Topic",
            category="grammar",
            display_order=13000,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="ADSEL-01",
            skill_name="Adaptive Selection Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.empty_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="ADSEL-EMPTY",
            skill_name="Adaptive Empty Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=2,
            is_active=True,
        )

        # Main candidate pool.
        cls.question_easy = cls.create_question(
            source_id="ADSEL-EASY",
            difficulty="0.2500",
        )

        cls.question_mid = cls.create_question(
            source_id="ADSEL-MID",
            difficulty="0.5000",
        )

        cls.question_hard = cls.create_question(
            source_id="ADSEL-HARD",
            difficulty="0.8000",
        )

        # Used as an already-selected question in
        # the current active assessment.
        cls.question_seed = cls.create_question(
            source_id="ADSEL-SEED",
            difficulty="0.9000",
        )


    @classmethod
    def create_question(
        cls,
        *,
        source_id,
        difficulty,
    ):
        question = Question.objects.create(
            source_id=source_id,
            question_type="multiple_choice",
            item_format="single_choice",
            question_text=(
                f"Adaptive selection {source_id}."
            ),
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal(
                difficulty
            ),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=question,
            skill=cls.skill,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

        QuestionOption.objects.create(
            question=question,
            option_key="A",
            option_text="Wrong",
            misconception_code="adaptive_wrong",
            is_correct=False,
            display_order=1,
        )

        QuestionOption.objects.create(
            question=question,
            option_key="B",
            option_text="Correct",
            misconception_code=None,
            is_correct=True,
            display_order=2,
        )

        QuestionOption.objects.create(
            question=question,
            option_key="C",
            option_text="Wrong 2",
            misconception_code="adaptive_wrong",
            is_correct=False,
            display_order=3,
        )

        return question

    def setUp(self):
        self.as_of = timezone.now()

        self.session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

    def create_completed_wrong_evidence(
        self,
    ):
        question_history = self.create_question(
            source_id="ADSEL-HISTORY",
            difficulty="0.5000",
        )

        historical_session = (
            AssessmentSession.objects.create(
                user=self.user,
                mode=AssessmentSession.Mode.ADAPTIVE,
                status=AssessmentSession.Status.ACTIVE,
            )
        )

        added = add_question_to_session(
            user=self.user,
            session_id=historical_session.pk,
            question_id=question_history.pk,
        )

        save_option_answer(
            user=self.user,
            session_question_id=(
                added.session_question_id
            ),
            selected_option_key="A",
        )

        complete_session(
            user=self.user,
            session_id=historical_session.pk,
        )

        AssessmentSession.objects.filter(
            pk=historical_session.pk
        ).update(
            ended_at=self.as_of
        )

        historical_session.refresh_from_db()

        return historical_session

    def test_cold_start_selects_mid_difficulty_and_adds_question(
        self,
    ):
        result = select_and_add_adaptive_question(
            user=self.user,
            session_id=self.session.pk,
            skill_id=self.skill.pk,
            as_of=self.as_of,
        )

        self.assertIsNotNone(
            result
        )

        # Cold start:
        # M-hat = .50
        # target difficulty = .50
        self.assertEqual(
            result.question_id,
            self.question_mid.pk,
        )

        self.assertEqual(
            result.source_id,
            "ADSEL-MID",
        )

        self.assertEqual(
            result.sequence_number,
            1,
        )

        session_question = (
            SessionQuestion.objects.get(
                pk=result.session_question_id
            )
        )

        self.assertEqual(
            session_question.session,
            self.session,
        )

        self.assertEqual(
            session_question.question,
            self.question_mid,
        )

        self.assertEqual(
            session_question.difficulty_score_snapshot,
            Decimal("0.5000"),
        )

    def test_completed_wrong_evidence_shifts_selection_toward_easier_question(
        self,
    ):
        self.create_completed_wrong_evidence()

        result = select_and_add_adaptive_question(
            user=self.user,
            session_id=self.session.pk,
            skill_id=self.skill.pk,
            as_of=self.as_of,
        )

        self.assertIsNotNone(
            result
        )

        # One finalized wrong observation at d=.50:
        #
        # alpha=2
        # prior=.5
        #
        # M-hat =
        # (2*.5 + 0) / (2 + 1)
        # = 1/3
        #
        # easy .25 -> distance ~.0833
        # mid  .50 -> distance ~.1667
        #
        # Historical question is already seen,
        # so unseen candidates are preferred first.
        self.assertEqual(
            result.question_id,
            self.question_easy.pk,
        )

        self.assertEqual(
            result.source_id,
            "ADSEL-EASY",
        )

    def test_current_active_answer_does_not_change_target_difficulty(
        self,
    ):
        added = add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.question_seed.pk,
        )

        # Wrong answer exists, but the assessment is
        # still ACTIVE.
        save_option_answer(
            user=self.user,
            session_question_id=(
                added.session_question_id
            ),
            selected_option_key="A",
        )

        result = select_and_add_adaptive_question(
            user=self.user,
            session_id=self.session.pk,
            skill_id=self.skill.pk,
            as_of=self.as_of,
        )

        self.assertIsNotNone(
            result
        )

        # Current-session answer must NOT become
        # mastery evidence.
        #
        # Therefore this is still cold start:
        #
        # M-hat = .50
        #
        # mid .50 must win.
        #
        # If the current wrong answer leaked into
        # mastery, M-hat would move near 1/3 and
        # easy .25 would win instead.
        self.assertEqual(
            result.question_id,
            self.question_mid.pk,
        )

        self.assertEqual(
            result.source_id,
            "ADSEL-MID",
        )

        self.assertEqual(
            result.sequence_number,
            2,
        )

    def test_no_eligible_question_returns_none_without_creating_row(
        self,
    ):
        before_count = (
            SessionQuestion.objects.filter(
                session=self.session
            ).count()
        )

        result = select_and_add_adaptive_question(
            user=self.user,
            session_id=self.session.pk,
            skill_id=self.empty_skill.pk,
            as_of=self.as_of,
        )

        self.assertIsNone(
            result
        )

        after_count = (
            SessionQuestion.objects.filter(
                session=self.session
            ).count()
        )

        self.assertEqual(
            after_count,
            before_count,
        )

    def test_diagnostic_session_is_rejected_by_adaptive_selector(
        self,
    ):
        diagnostic_session = (
            AssessmentSession.objects.create(
                user=self.user,
                mode=AssessmentSession.Mode.DIAGNOSTIC,
                status=AssessmentSession.Status.ACTIVE,
            )
        )

        with self.assertRaises(
            LearningServiceError
        ):
            select_and_add_adaptive_question(
                user=self.user,
                session_id=diagnostic_session.pk,
                skill_id=self.skill.pk,
                as_of=self.as_of,
            )

        self.assertEqual(
            SessionQuestion.objects.filter(
                session=diagnostic_session
            ).count(),
            0,
        )

class AdaptiveAssessmentPlannerTests(TestCase):
    def make_skill(
        self,
        *,
        skill_id,
        skill_code,
        priority,
        question_ids,
    ):
        return AssemblySkillCandidate(
            skill_id=skill_id,
            skill_code=skill_code,
            priority_score=Decimal(
                str(priority)
            ),
            ranked_question_ids=tuple(
                question_ids
            ),
        )

    def test_five_skills_are_distributed_two_questions_each(
        self,
    ):
        skills = [
            self.make_skill(
                skill_id=1,
                skill_code="A",
                priority="0.90",
                question_ids=[101, 102],
            ),
            self.make_skill(
                skill_id=2,
                skill_code="B",
                priority="0.80",
                question_ids=[201, 202],
            ),
            self.make_skill(
                skill_id=3,
                skill_code="C",
                priority="0.70",
                question_ids=[301, 302],
            ),
            self.make_skill(
                skill_id=4,
                skill_code="D",
                priority="0.60",
                question_ids=[401, 402],
            ),
            self.make_skill(
                skill_id=5,
                skill_code="E",
                priority="0.50",
                question_ids=[501, 502],
            ),
        ]

        plan = plan_adaptive_assessment(
            skill_candidates=skills,
        )

        self.assertEqual(
            len(plan),
            10,
        )

        self.assertEqual(
            [
                slot.focus_skill_code
                for slot in plan
            ],
            [
                "A",
                "B",
                "C",
                "D",
                "E",
                "A",
                "B",
                "C",
                "D",
                "E",
            ],
        )

        self.assertEqual(
            [
                slot.occurrence_for_skill
                for slot in plan
            ],
            [
                1,
                1,
                1,
                1,
                1,
                2,
                2,
                2,
                2,
                2,
            ],
        )

        self.assertEqual(
            len(
                {
                    slot.question_id
                    for slot in plan
                }
            ),
            10,
        )

    def test_equal_priorities_use_skill_code_tiebreak(
        self,
    ):
        skills = [
            self.make_skill(
                skill_id=2,
                skill_code="B",
                priority="0.50",
                question_ids=[201, 202],
            ),
            self.make_skill(
                skill_id=1,
                skill_code="A",
                priority="0.50",
                question_ids=[101, 102],
            ),
        ]

        plan = plan_adaptive_assessment(
            skill_candidates=skills,
            target_question_count=4,
            target_distinct_skills=2,
            preferred_max_per_skill=2,
        )

        self.assertEqual(
            [
                slot.focus_skill_code
                for slot in plan
            ],
            [
                "A",
                "B",
                "A",
                "B",
            ],
        )

    def test_soft_cap_is_relaxed_when_four_skills_must_supply_ten_questions(
        self,
    ):
        skills = [
            self.make_skill(
                skill_id=1,
                skill_code="A",
                priority="0.90",
                question_ids=[101, 102, 103],
            ),
            self.make_skill(
                skill_id=2,
                skill_code="B",
                priority="0.80",
                question_ids=[201, 202, 203],
            ),
            self.make_skill(
                skill_id=3,
                skill_code="C",
                priority="0.70",
                question_ids=[301, 302, 303],
            ),
            self.make_skill(
                skill_id=4,
                skill_code="D",
                priority="0.60",
                question_ids=[401, 402, 403],
            ),
        ]

        plan = plan_adaptive_assessment(
            skill_candidates=skills,
        )

        self.assertEqual(
            [
                slot.focus_skill_code
                for slot in plan
            ],
            [
                "A",
                "B",
                "C",
                "D",
                "A",
                "B",
                "C",
                "D",
                "A",
                "B",
            ],
        )

        counts = {}

        for slot in plan:
            counts[
                slot.focus_skill_code
            ] = (
                counts.get(
                    slot.focus_skill_code,
                    0,
                )
                + 1
            )

        self.assertEqual(
            counts,
            {
                "A": 3,
                "B": 3,
                "C": 2,
                "D": 2,
            },
        )

    def test_same_skill_is_repeated_only_when_no_alternative_remains(
        self,
    ):
        skills = [
            self.make_skill(
                skill_id=1,
                skill_code="A",
                priority="0.90",
                question_ids=[
                    101,
                    102,
                    103,
                    104,
                ],
            ),
            self.make_skill(
                skill_id=2,
                skill_code="B",
                priority="0.80",
                question_ids=[
                    201,
                ],
            ),
        ]

        plan = plan_adaptive_assessment(
            skill_candidates=skills,
            target_question_count=5,
            target_distinct_skills=2,
            preferred_max_per_skill=2,
        )

        self.assertEqual(
            [
                slot.focus_skill_code
                for slot in plan
            ],
            [
                "A",
                "B",
                "A",
                "A",
                "A",
            ],
        )

        # The first adjacent A -> A occurs only
        # after Skill B has no remaining Question.
        self.assertEqual(
            plan[2].focus_skill_code,
            "A",
        )

        self.assertEqual(
            plan[3].focus_skill_code,
            "A",
        )

    def test_overlapping_multiskill_question_is_used_only_once(
        self,
    ):
        skills = [
            self.make_skill(
                skill_id=1,
                skill_code="A",
                priority="0.90",
                question_ids=[
                    100,
                    101,
                    102,
                ],
            ),
            self.make_skill(
                skill_id=2,
                skill_code="B",
                priority="0.80",
                question_ids=[
                    100,
                    201,
                    202,
                ],
            ),
        ]

        plan = plan_adaptive_assessment(
            skill_candidates=skills,
            target_question_count=4,
            target_distinct_skills=2,
            preferred_max_per_skill=2,
        )

        self.assertEqual(
            [
                slot.question_id
                for slot in plan
            ],
            [
                100,
                201,
                101,
                202,
            ],
        )

        self.assertEqual(
            len(
                {
                    slot.question_id
                    for slot in plan
                }
            ),
            4,
        )

        # B's best Question was Q100,
        # but A already consumed it.
        #
        # Therefore B must use its second-ranked
        # Question.
        self.assertEqual(
            plan[1].focus_skill_code,
            "B",
        )

        self.assertEqual(
            plan[1].question_rank_within_skill,
            2,
        )

    def test_question_order_within_skill_is_preserved(
        self,
    ):
        skills = [
            self.make_skill(
                skill_id=1,
                skill_code="A",
                priority="0.90",
                question_ids=[
                    300,
                    100,
                    200,
                ],
            ),
        ]

        plan = plan_adaptive_assessment(
            skill_candidates=skills,
            target_question_count=3,
            target_distinct_skills=1,
            preferred_max_per_skill=2,
        )

        # Planner must not re-sort Question IDs.
        #
        # 9D.9 has already ranked them.
        self.assertEqual(
            [
                slot.question_id
                for slot in plan
            ],
            [
                300,
                100,
                200,
            ],
        )

        self.assertEqual(
            [
                slot.question_rank_within_skill
                for slot in plan
            ],
            [
                1,
                2,
                3,
            ],
        )

    def test_insufficient_unique_questions_is_rejected(
        self,
    ):
        skills = [
            self.make_skill(
                skill_id=1,
                skill_code="A",
                priority="0.90",
                question_ids=[
                    100,
                    101,
                ],
            ),
            self.make_skill(
                skill_id=2,
                skill_code="B",
                priority="0.80",
                question_ids=[
                    100,
                    102,
                ],
            ),
        ]

        # Four references exist across both Skills,
        # but only three unique Questions: {100, 101, 102}
        with self.assertRaises(
            ValueError
        ):
            plan_adaptive_assessment(
                skill_candidates=skills,
                target_question_count=4,
                target_distinct_skills=2,
                preferred_max_per_skill=2,
            )

class AdaptiveAssessmentPreparationIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="adaptive-preparation@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Adaptive Preparation Topic",
            category="grammar",
            display_order=14000,
            is_active=True,
        )

        cls.skill_a = Skill.objects.create(
            topic=cls.topic,
            skill_code="APREP-A",
            skill_name="Adaptive Preparation A",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.skill_b = Skill.objects.create(
            topic=cls.topic,
            skill_code="APREP-B",
            skill_name="Adaptive Preparation B",
            cefr_level="A1",
            is_extension=False,
            display_order=2,
            is_active=True,
        )

        cls.empty_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="APREP-EMPTY",
            skill_name="Adaptive Preparation Empty",
            cefr_level="A1",
            is_extension=False,
            display_order=3,
            is_active=True,
        )

        cls.question_a_mid = (
            cls.create_primary_question(
                source_id="APREP-A-MID",
                difficulty="0.5000",
                skill=cls.skill_a,
            )
        )

        cls.question_b_mid = (
            cls.create_primary_question(
                source_id="APREP-B-MID",
                difficulty="0.5000",
                skill=cls.skill_b,
            )
        )

    @classmethod
    def create_primary_question(
        cls,
        *,
        source_id,
        difficulty,
        skill,
    ):
        question = Question.objects.create(
            source_id=source_id,
            question_type="multiple_choice",
            item_format="single_choice",
            question_text=(
                f"Adaptive preparation {source_id}."
            ),
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal(
                difficulty
            ),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=question,
            skill=skill,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

        return question

    @classmethod
    def add_options(
        cls,
        *,
        question,
    ):
        QuestionOption.objects.create(
            question=question,
            option_key="A",
            option_text="Wrong",
            misconception_code="aprep_wrong",
            is_correct=False,
            display_order=1,
        )

        QuestionOption.objects.create(
            question=question,
            option_key="B",
            option_text="Correct",
            misconception_code=None,
            is_correct=True,
            display_order=2,
        )

        QuestionOption.objects.create(
            question=question,
            option_key="C",
            option_text="Wrong 2",
            misconception_code="aprep_wrong",
            is_correct=False,
            display_order=3,
        )

    def setUp(self):
        self.session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

    def create_wrong_completed_evidence(
        self,
        *,
        skill,
        source_id,
    ):
        question = self.create_primary_question(
            source_id=source_id,
            difficulty="0.5000",
            skill=skill,
        )

        self.add_options(
            question=question,
        )

        historical_session = (
            AssessmentSession.objects.create(
                user=self.user,
                mode=AssessmentSession.Mode.ADAPTIVE,
                status=AssessmentSession.Status.ACTIVE,
            )
        )

        added = add_question_to_session(
            user=self.user,
            session_id=historical_session.pk,
            question_id=question.pk,
        )

        save_option_answer(
            user=self.user,
            session_question_id=(
                added.session_question_id
            ),
            selected_option_key="A",
        )

        complete_session(
            user=self.user,
            session_id=historical_session.pk,
        )

        return question

    def get_prepared_skill(
        self,
        prepared,
        skill_id,
    ):
        return next(
            (
                row
                for row in prepared
                if row.skill_id == skill_id
            ),
            None,
        )

    def test_prepared_skills_follow_priority_order(
        self,
    ):
        # Skill A remains cold:
        # P = .30
        #
        # One wrong finalized observation for B:
        # P = .40 under the current priority rule.
        self.create_wrong_completed_evidence(
            skill=self.skill_b,
            source_id="APREP-B-EVIDENCE",
        )

        prepared = (
            prepare_adaptive_assessment_candidates(
                user=self.user,
                session_id=self.session.pk,
                eligible_skill_ids=[
                    self.skill_a.pk,
                    self.skill_b.pk,
                ],
            )
        )

        self.assertEqual(
            [
                row.skill_code
                for row in prepared
            ],
            [
                "APREP-B",
                "APREP-A",
            ],
        )

        self.assertGreater(
            prepared[0].priority_score,
            prepared[1].priority_score,
        )

    def test_mastery_estimate_controls_question_difficulty_ranking(
        self,
    ):
        # One finalized wrong answer at difficulty .50
        # gives M-hat approximately 1/3.
        self.create_wrong_completed_evidence(
            skill=self.skill_a,
            source_id="APREP-A-EVIDENCE",
        )

        question_easy = (
            self.create_primary_question(
                source_id="APREP-A-EASY",
                difficulty="0.2500",
                skill=self.skill_a,
            )
        )

        question_hard = (
            self.create_primary_question(
                source_id="APREP-A-HARD",
                difficulty="0.8000",
                skill=self.skill_a,
            )
        )

        prepared = (
            prepare_adaptive_assessment_candidates(
                user=self.user,
                session_id=self.session.pk,
                eligible_skill_ids=[
                    self.skill_a.pk,
                ],
            )
        )

        self.assertEqual(
            len(prepared),
            1,
        )

        skill_result = prepared[0]

        # Existing APREP-A-MID is .50.
        #
        # With M-hat about .33333:
        #
        # .25 distance ~ .08333
        # .50 distance ~ .16667
        # .80 distance ~ .46667
        #
        # Therefore EASY must rank first.
        self.assertEqual(
            skill_result.ranked_question_ids[0],
            question_easy.pk,
        )

        self.assertLess(
            skill_result.ranked_question_ids.index(
                self.question_a_mid.pk
            ),
            skill_result.ranked_question_ids.index(
                question_hard.pk
            ),
        )

    def test_skill_without_available_questions_is_skipped(
        self,
    ):
        prepared = (
            prepare_adaptive_assessment_candidates(
                user=self.user,
                session_id=self.session.pk,
                eligible_skill_ids=[
                    self.skill_a.pk,
                    self.empty_skill.pk,
                ],
            )
        )

        prepared_skill_ids = {
            row.skill_id
            for row in prepared
        }

        self.assertIn(
            self.skill_a.pk,
            prepared_skill_ids,
        )

        self.assertNotIn(
            self.empty_skill.pk,
            prepared_skill_ids,
        )

    def test_multiskill_question_can_appear_in_both_skill_candidate_lists(
        self,
    ):
        shared_question = Question.objects.create(
            source_id="APREP-SHARED",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text=(
                "Adaptive preparation shared question."
            ),
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.5000"),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=shared_question,
            skill=self.skill_a,
            is_primary=True,
            weight=Decimal("0.60000"),
        )

        QuestionSkill.objects.create(
            question=shared_question,
            skill=self.skill_b,
            is_primary=False,
            weight=Decimal("0.40000"),
        )

        prepared = (
            prepare_adaptive_assessment_candidates(
                user=self.user,
                session_id=self.session.pk,
                eligible_skill_ids=[
                    self.skill_a.pk,
                    self.skill_b.pk,
                ],
            )
        )

        prepared_a = self.get_prepared_skill(
            prepared,
            self.skill_a.pk,
        )

        prepared_b = self.get_prepared_skill(
            prepared,
            self.skill_b.pk,
        )

        self.assertIsNotNone(
            prepared_a
        )

        self.assertIsNotNone(
            prepared_b
        )

        self.assertIn(
            shared_question.pk,
            prepared_a.ranked_question_ids,
        )

        self.assertIn(
            shared_question.pk,
            prepared_b.ranked_question_ids,
        )

        # Preparation intentionally does NOT solve
        # cross-Skill duplicate Question collisions.
        #
        # 9D.10A planner owns that responsibility.
        self.assertEqual(
            sum(
                shared_question.pk
                in row.ranked_question_ids
                for row in prepared
            ),
            2,
        )

    def test_question_already_in_current_session_is_excluded(
        self,
    ):
        question_other = (
            self.create_primary_question(
                source_id="APREP-A-OTHER",
                difficulty="0.6000",
                skill=self.skill_a,
            )
        )

        add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.question_a_mid.pk,
        )

        prepared = (
            prepare_adaptive_assessment_candidates(
                user=self.user,
                session_id=self.session.pk,
                eligible_skill_ids=[
                    self.skill_a.pk,
                ],
            )
        )

        self.assertEqual(
            len(prepared),
            1,
        )

        ranked_ids = (
            prepared[0].ranked_question_ids
        )

        self.assertNotIn(
            self.question_a_mid.pk,
            ranked_ids,
        )

        self.assertIn(
            question_other.pk,
            ranked_ids,
        )

class AdaptiveAssessmentAssemblyIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="adaptive-assembly@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Adaptive Assembly Topic",
            category="grammar",
            display_order=15000,
            is_active=True,
        )

        cls.skills = []

        for index, code in enumerate(
            [
                "AASM-A",
                "AASM-B",
                "AASM-C",
                "AASM-D",
                "AASM-E",
            ],
            start=1,
        ):
            skill = Skill.objects.create(
                topic=cls.topic,
                skill_code=code,
                skill_name=f"Adaptive Assembly {code}",
                cefr_level="A1",
                is_extension=False,
                display_order=index,
                is_active=True,
            )

            cls.skills.append(skill)

            # Two unique Questions per Skill.
            #
            # q1 is exactly cold-start target .50.
            # q2 is farther away at .70.
            cls.create_question(
                skill=skill,
                source_id=f"{code}-Q1",
                difficulty="0.5000",
            )

            cls.create_question(
                skill=skill,
                source_id=f"{code}-Q2",
                difficulty="0.7000",
            )

    @classmethod
    def create_question(
        cls,
        *,
        skill,
        source_id,
        difficulty,
    ):
        question = Question.objects.create(
            source_id=source_id,
            question_type="multiple_choice",
            item_format="single_choice",
            question_text=(
                f"Adaptive assembly {source_id}."
            ),
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal(
                difficulty
            ),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=question,
            skill=skill,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

        return question

    def setUp(self):
        self.session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

        self.eligible_skill_ids = [
            skill.pk
            for skill in self.skills
        ]

    def test_assembly_creates_exactly_ten_unique_questions(
        self,
    ):
        result = assemble_adaptive_assessment(
            user=self.user,
            session_id=self.session.pk,
            eligible_skill_ids=(
                self.eligible_skill_ids
            ),
        )

        self.assertEqual(
            result.session_id,
            self.session.pk,
        )

        self.assertEqual(
            result.question_count,
            10,
        )

        self.assertEqual(
            len(result.slots),
            10,
        )

        session_questions = list(
            SessionQuestion.objects
            .filter(
                session=self.session,
            )
            .order_by(
                "sequence_number"
            )
        )

        self.assertEqual(
            len(session_questions),
            10,
        )

        self.assertEqual(
            [
                row.sequence_number
                for row in session_questions
            ],
            list(
                range(1, 11)
            ),
        )

        self.assertEqual(
            len(
                {
                    row.question_id
                    for row in session_questions
                }
            ),
            10,
        )

    def test_assembly_uses_expected_five_skill_distribution(
        self,
    ):
        result = assemble_adaptive_assessment(
            user=self.user,
            session_id=self.session.pk,
            eligible_skill_ids=(
                self.eligible_skill_ids
            ),
        )

        self.assertEqual(
            [
                slot.focus_skill_code
                for slot in result.slots
            ],
            [
                "AASM-A",
                "AASM-B",
                "AASM-C",
                "AASM-D",
                "AASM-E",
                "AASM-A",
                "AASM-B",
                "AASM-C",
                "AASM-D",
                "AASM-E",
            ],
        )

        # Cold-start priorities are equal, so
        # canonical skill_code ordering determines
        # A -> B -> C -> D -> E.
        #
        # Each Skill then receives its second slot
        # during the priority-fill phase.
        self.assertEqual(
            [
                slot.slot_number
                for slot in result.slots
            ],
            list(
                range(1, 11)
            ),
        )

    def test_nonempty_session_is_rejected_without_adding_more_questions(
        self,
    ):
        first_question = (
            Question.objects.get(
                source_id="AASM-A-Q1"
            )
        )

        add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=first_question.pk,
        )

        self.assertEqual(
            SessionQuestion.objects.filter(
                session=self.session
            ).count(),
            1,
        )

        with self.assertRaises(
            LearningServiceError
        ):
            assemble_adaptive_assessment(
                user=self.user,
                session_id=self.session.pk,
                eligible_skill_ids=(
                    self.eligible_skill_ids
                ),
            )

        # Existing Question remains, but no new
        # Questions were appended.
        self.assertEqual(
            SessionQuestion.objects.filter(
                session=self.session
            ).count(),
            1,
        )

    def test_insufficient_question_bank_is_rejected_without_writes(
        self,
    ):
        # Only four Skills are eligible.
        #
        # Each has exactly two Questions:
        # 4 * 2 = 8 unique Questions.
        #
        # Canonical assessment requires exactly 10.
        insufficient_skill_ids = [
            skill.pk
            for skill in self.skills[:4]
        ]

        with self.assertRaises(
            LearningServiceError
        ):
            assemble_adaptive_assessment(
                user=self.user,
                session_id=self.session.pk,
                eligible_skill_ids=(
                    insufficient_skill_ids
                ),
            )

        self.assertEqual(
            SessionQuestion.objects.filter(
                session=self.session
            ).count(),
            0,
        )

    def test_mid_assembly_failure_rolls_back_all_written_questions(
        self,
    ):
        # Keep an unpatched reference to the real
        # single-Question service.
        real_add_question = (
            add_question_to_session
        )

        call_count = {
            "value": 0,
        }

        def fail_on_sixth_question(
            *,
            user,
            session_id,
            question_id,
        ):
            call_count[
                "value"
            ] += 1

            if call_count["value"] == 6:
                raise LearningServiceError(
                    "Forced assembly failure."
                )

            return real_add_question(
                user=user,
                session_id=session_id,
                question_id=question_id,
            )

        with patch(
            "learning_engine.services."
            "add_question_to_session",
            side_effect=(
                fail_on_sixth_question
            ),
        ):
            with self.assertRaises(
                LearningServiceError
            ):
                assemble_adaptive_assessment(
                    user=self.user,
                    session_id=self.session.pk,
                    eligible_skill_ids=(
                        self.eligible_skill_ids
                    ),
                )

        self.assertEqual(
            call_count["value"],
            6,
        )

        # Five inserts succeeded before the forced
        # sixth failure.
        #
        # Because the outer assembly service is
        # transaction.atomic, ALL five must have
        # been rolled back.
        self.assertEqual(
            SessionQuestion.objects.filter(
                session=self.session
            ).count(),
            0,
        )

class DiagnosticBlueprintTests(TestCase):
    def test_canonical_blueprint_is_valid_and_has_fifteen_slots(
        self,
    ):
        blueprint = validate_diagnostic_blueprint()

        self.assertEqual(
            len(blueprint),
            15,
        )

        self.assertEqual(
            [
                slot.slot_number
                for slot in blueprint
            ],
            list(
                range(1, 16)
            ),
        )

    def test_canonical_blueprint_has_five_anchors_three_times_each(
        self,
    ):
        blueprint = validate_diagnostic_blueprint()

        counts = {}

        for slot in blueprint:
            counts[
                slot.skill_code
            ] = (
                counts.get(
                    slot.skill_code,
                    0,
                )
                + 1
            )

        self.assertEqual(
            counts,
            {
                "ACC-02": 3,
                "MOVE-01": 3,
                "VOC-01": 3,
                "VERB-05": 3,
                "VOC-10": 3,
            },
        )

    def test_canonical_blueprint_has_three_a1_and_two_a2_anchors(
        self,
    ):
        blueprint = validate_diagnostic_blueprint()

        cefr_by_skill = {}

        for slot in blueprint:
            cefr_by_skill.setdefault(
                slot.skill_code,
                slot.cefr_level,
            )

        self.assertEqual(
            cefr_by_skill,
            {
                "ACC-02": "A1",
                "MOVE-01": "A1",
                "VOC-01": "A1",
                "VERB-05": "A2",
                "VOC-10": "A2",
            },
        )

        self.assertEqual(
            list(
                cefr_by_skill.values()
            ).count("A1"),
            3,
        )

        self.assertEqual(
            list(
                cefr_by_skill.values()
            ).count("A2"),
            2,
        )

    def test_grammar_anchors_follow_easy_medium_hard_targets(
        self,
    ):
        blueprint = validate_diagnostic_blueprint()

        expected = {
            "ACC-02": [
                Decimal("0.2500"),
                Decimal("0.5000"),
                Decimal("0.7500"),
            ],
            "MOVE-01": [
                Decimal("0.2500"),
                Decimal("0.5000"),
                Decimal("0.7500"),
            ],
            "VERB-05": [
                Decimal("0.2500"),
                Decimal("0.5000"),
                Decimal("0.7500"),
            ],
        }

        for skill_code, difficulties in expected.items():
            actual = [
                slot.target_difficulty
                for slot in blueprint
                if slot.skill_code == skill_code
            ]

            self.assertEqual(
                actual,
                difficulties,
            )

    def test_vocabulary_targets_match_real_bank_difficulty(
        self,
    ):
        blueprint = validate_diagnostic_blueprint()

        voc_01 = [
            slot.target_difficulty
            for slot in blueprint
            if slot.skill_code == "VOC-01"
        ]

        voc_10 = [
            slot.target_difficulty
            for slot in blueprint
            if slot.skill_code == "VOC-10"
        ]

        self.assertEqual(
            voc_01,
            [
                Decimal("0.2500"),
                Decimal("0.2500"),
                Decimal("0.2500"),
            ],
        )

        self.assertEqual(
            voc_10,
            [
                Decimal("0.5000"),
                Decimal("0.5000"),
                Decimal("0.5000"),
            ],
        )

    def test_inconsistent_cefr_for_same_anchor_is_rejected(
        self,
    ):
        broken = list(
            DIAGNOSTIC_BLUEPRINT_V1
        )

        original = broken[12]

        # Slot 13 is VOC-01 and should remain A1.
        broken[12] = DiagnosticBlueprintSlot(
            slot_number=original.slot_number,
            round_number=original.round_number,
            anchor_order=original.anchor_order,
            skill_code=original.skill_code,
            cefr_level="A2",
            target_difficulty=(
                original.target_difficulty
            ),
        )

        with self.assertRaises(
            ValueError
        ):
            validate_diagnostic_blueprint(
                broken
            )

    def test_wrong_a1_a2_anchor_distribution_is_rejected(
        self,
    ):
        broken = []

        for slot in DIAGNOSTIC_BLUEPRINT_V1:
            # Convert the whole VOC-10 anchor
            # from A2 to A1.
            if slot.skill_code == "VOC-10":
                broken.append(
                    DiagnosticBlueprintSlot(
                        slot_number=(
                            slot.slot_number
                        ),
                        round_number=(
                            slot.round_number
                        ),
                        anchor_order=(
                            slot.anchor_order
                        ),
                        skill_code=(
                            slot.skill_code
                        ),
                        cefr_level="A1",
                        target_difficulty=(
                            slot.target_difficulty
                        ),
                    )
                )

            else:
                broken.append(
                    slot
                )

        with self.assertRaises(
            ValueError
        ):
            validate_diagnostic_blueprint(
                broken
            )

class DiagnosticQuestionSelectionIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="diagnostic-selection@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Diagnostic Selection Topic",
            category="grammar",
            display_order=16000,
            is_active=True,
        )

        # =========================================
        # Canonical diagnostic anchors
        # =========================================

        cls.acc_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="ACC-02",
            skill_name="Diagnostic ACC-02",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.move_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="MOVE-01",
            skill_name="Diagnostic MOVE-01",
            cefr_level="A1",
            is_extension=False,
            display_order=2,
            is_active=True,
        )

        cls.voc_a1_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="VOC-01",
            skill_name="Diagnostic VOC-01",
            cefr_level="A1",
            is_extension=False,
            display_order=3,
            is_active=True,
        )

        cls.verb_a2_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="VERB-05",
            skill_name="Diagnostic VERB-05",
            cefr_level="A2",
            is_extension=False,
            display_order=4,
            is_active=True,
        )

        cls.voc_a2_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="VOC-10",
            skill_name="Diagnostic VOC-10",
            cefr_level="A2",
            is_extension=False,
            display_order=5,
            is_active=True,
        )

        cls.other_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="DSEL-OTHER",
            skill_name="Diagnostic Other Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=6,
            is_active=True,
        )

        # =========================================
        # Grammar anchors
        #
        # Exactly .25 / .50 / .75
        # =========================================

        cls.acc_q25 = cls.create_primary_question(
            skill=cls.acc_skill,
            source_id="DACC-025",
            difficulty="0.2500",
        )

        cls.acc_q50 = cls.create_primary_question(
            skill=cls.acc_skill,
            source_id="DACC-050",
            difficulty="0.5000",
        )

        cls.acc_q75 = cls.create_primary_question(
            skill=cls.acc_skill,
            source_id="DACC-075",
            difficulty="0.7500",
        )

        cls.move_q25 = cls.create_primary_question(
            skill=cls.move_skill,
            source_id="DMOVE-025",
            difficulty="0.2500",
        )

        cls.move_q50 = cls.create_primary_question(
            skill=cls.move_skill,
            source_id="DMOVE-050",
            difficulty="0.5000",
        )

        cls.move_q75 = cls.create_primary_question(
            skill=cls.move_skill,
            source_id="DMOVE-075",
            difficulty="0.7500",
        )

        cls.verb_q25 = cls.create_primary_question(
            skill=cls.verb_a2_skill,
            source_id="DVERB-025",
            difficulty="0.2500",
        )

        cls.verb_q50 = cls.create_primary_question(
            skill=cls.verb_a2_skill,
            source_id="DVERB-050",
            difficulty="0.5000",
        )

        cls.verb_q75 = cls.create_primary_question(
            skill=cls.verb_a2_skill,
            source_id="DVERB-075",
            difficulty="0.7500",
        )

        # =========================================
        # Vocabulary anchors
        #
        # Real bank semantics:
        # VOC-01 -> .25
        # VOC-10 -> .50
        # =========================================

        cls.voc01_q1 = cls.create_primary_question(
            skill=cls.voc_a1_skill,
            source_id="DVOC01-001",
            difficulty="0.2500",
        )

        cls.voc01_q2 = cls.create_primary_question(
            skill=cls.voc_a1_skill,
            source_id="DVOC01-002",
            difficulty="0.2500",
        )

        cls.voc01_q3 = cls.create_primary_question(
            skill=cls.voc_a1_skill,
            source_id="DVOC01-003",
            difficulty="0.2500",
        )

        cls.voc10_q1 = cls.create_primary_question(
            skill=cls.voc_a2_skill,
            source_id="DVOC10-001",
            difficulty="0.5000",
        )

        cls.voc10_q2 = cls.create_primary_question(
            skill=cls.voc_a2_skill,
            source_id="DVOC10-002",
            difficulty="0.5000",
        )

        cls.voc10_q3 = cls.create_primary_question(
            skill=cls.voc_a2_skill,
            source_id="DVOC10-003",
            difficulty="0.5000",
        )

    @classmethod
    def create_primary_question(
        cls,
        *,
        skill,
        source_id,
        difficulty,
    ):
        question = Question.objects.create(
            source_id=source_id,
            question_type="multiple_choice",
            item_format="single_choice",
            question_text=(
                f"Diagnostic selection {source_id}."
            ),
            cefr_level=skill.cefr_level,
            is_extension=False,
            difficulty_score=Decimal(
                difficulty
            ),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=question,
            skill=skill,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

        return question

    def setUp(self):
        self.session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.DIAGNOSTIC,
            status=AssessmentSession.Status.ACTIVE,
        )

    def test_plan_contains_fifteen_unique_questions_and_does_not_write_db(
        self,
    ):
        self.assertEqual(
            SessionQuestion.objects.filter(
                session=self.session
            ).count(),
            0,
        )

        plan = plan_diagnostic_questions(
            user=self.user,
            session_id=self.session.pk,
        )

        self.assertEqual(
            len(plan),
            15,
        )

        self.assertEqual(
            [
                slot.slot_number
                for slot in plan
            ],
            list(
                range(1, 16)
            ),
        )

        self.assertEqual(
            len(
                {
                    slot.question_id
                    for slot in plan
                }
            ),
            15,
        )

        # Planning is read-only.
        self.assertEqual(
            SessionQuestion.objects.filter(
                session=self.session
            ).count(),
            0,
        )

    def test_grammar_anchors_select_25_50_75_progression(
        self,
    ):
        plan = plan_diagnostic_questions(
            user=self.user,
            session_id=self.session.pk,
        )

        expected = {
            "ACC-02": [
                Decimal("0.2500"),
                Decimal("0.5000"),
                Decimal("0.7500"),
            ],
            "MOVE-01": [
                Decimal("0.2500"),
                Decimal("0.5000"),
                Decimal("0.7500"),
            ],
            "VERB-05": [
                Decimal("0.2500"),
                Decimal("0.5000"),
                Decimal("0.7500"),
            ],
        }

        for (
            skill_code,
            expected_difficulties,
        ) in expected.items():
            actual = [
                slot.question_difficulty
                for slot in plan
                if slot.skill_code == skill_code
            ]

            self.assertEqual(
                actual,
                expected_difficulties,
            )

            # Exact bank matches exist, so every
            # distance must be zero.
            actual_distances = [
                slot.difficulty_distance
                for slot in plan
                if slot.skill_code == skill_code
            ]

            self.assertEqual(
                actual_distances,
                [
                    Decimal("0.0000"),
                    Decimal("0.0000"),
                    Decimal("0.0000"),
                ],
            )

    def test_vocabulary_anchors_use_three_distinct_questions_at_real_bank_difficulty(
        self,
    ):
        plan = plan_diagnostic_questions(
            user=self.user,
            session_id=self.session.pk,
        )

        voc_01_slots = [
            slot
            for slot in plan
            if slot.skill_code == "VOC-01"
        ]

        voc_10_slots = [
            slot
            for slot in plan
            if slot.skill_code == "VOC-10"
        ]

        self.assertEqual(
            len(voc_01_slots),
            3,
        )

        self.assertEqual(
            len(voc_10_slots),
            3,
        )

        self.assertEqual(
            {
                slot.question_id
                for slot in voc_01_slots
            },
            {
                self.voc01_q1.pk,
                self.voc01_q2.pk,
                self.voc01_q3.pk,
            },
        )

        self.assertEqual(
            {
                slot.question_id
                for slot in voc_10_slots
            },
            {
                self.voc10_q1.pk,
                self.voc10_q2.pk,
                self.voc10_q3.pk,
            },
        )

        self.assertTrue(
            all(
                slot.question_difficulty
                == Decimal("0.2500")
                for slot in voc_01_slots
            )
        )

        self.assertTrue(
            all(
                slot.question_difficulty
                == Decimal("0.5000")
                for slot in voc_10_slots
            )
        )

    def test_historically_unseen_question_beats_seen_exact_difficulty(
        self,
    ):
        historical_session = (
            AssessmentSession.objects.create(
                user=self.user,
                mode=AssessmentSession.Mode.DIAGNOSTIC,
                status=AssessmentSession.Status.ACTIVE,
            )
        )

        added = add_question_to_session(
            user=self.user,
            session_id=historical_session.pk,
            question_id=self.acc_q25.pk,
        )

        mark_session_question_viewed(
            user=self.user,
            session_question_id=(
                added.session_question_id
            ),
        )

        # Abandoned assessment still represents
        # genuine historical visual exposure.
        abandon_session(
            user=self.user,
            session_id=historical_session.pk,
        )

        plan = plan_diagnostic_questions(
            user=self.user,
            session_id=self.session.pk,
        )

        acc_slots = [
            slot
            for slot in plan
            if slot.skill_code == "ACC-02"
        ]

        # Round 1 target is .25.
        #
        # DACC-025 is an exact match but has been
        # genuinely seen before.
        #
        # DACC-050 is unseen, so novelty wins before
        # difficulty distance.
        self.assertEqual(
            acc_slots[0].question_id,
            self.acc_q50.pk,
        )

        self.assertFalse(
            acc_slots[0].was_seen_before
        )

        # The seen Question can still be used later
        # if the anchor exhausts unseen alternatives.
        self.assertIn(
            self.acc_q25.pk,
            {
                slot.question_id
                for slot in acc_slots
            },
        )

    def test_secondary_only_mapping_is_never_selected_for_anchor(
        self,
    ):
        secondary_question = Question.objects.create(
            source_id="DACC-SECONDARY-ONLY",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text=(
                "Secondary-only diagnostic question."
            ),
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.2500"),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        # Valid multi-skill structure:
        #
        # DSEL-OTHER = primary .60
        # ACC-02     = secondary .40
        QuestionSkill.objects.create(
            question=secondary_question,
            skill=self.other_skill,
            is_primary=True,
            weight=Decimal("0.60000"),
        )

        QuestionSkill.objects.create(
            question=secondary_question,
            skill=self.acc_skill,
            is_primary=False,
            weight=Decimal("0.40000"),
        )

        plan = plan_diagnostic_questions(
            user=self.user,
            session_id=self.session.pk,
        )

        selected_question_ids = {
            slot.question_id
            for slot in plan
        }

        self.assertNotIn(
            secondary_question.pk,
            selected_question_ids,
        )

        acc_question_ids = {
            slot.question_id
            for slot in plan
            if slot.skill_code == "ACC-02"
        }

        self.assertEqual(
            acc_question_ids,
            {
                self.acc_q25.pk,
                self.acc_q50.pk,
                self.acc_q75.pk,
            },
        )

class DiagnosticAssessmentAssemblyIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="diagnostic-assembly@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Diagnostic Assembly Topic",
            category="grammar",
            display_order=17000,
            is_active=True,
        )

        # =========================================
        # Canonical diagnostic anchors
        # =========================================

        cls.acc_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="ACC-02",
            skill_name="Diagnostic Assembly ACC-02",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.move_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="MOVE-01",
            skill_name="Diagnostic Assembly MOVE-01",
            cefr_level="A1",
            is_extension=False,
            display_order=2,
            is_active=True,
        )

        cls.voc_a1_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="VOC-01",
            skill_name="Diagnostic Assembly VOC-01",
            cefr_level="A1",
            is_extension=False,
            display_order=3,
            is_active=True,
        )

        cls.verb_a2_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="VERB-05",
            skill_name="Diagnostic Assembly VERB-05",
            cefr_level="A2",
            is_extension=False,
            display_order=4,
            is_active=True,
        )

        cls.voc_a2_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="VOC-10",
            skill_name="Diagnostic Assembly VOC-10",
            cefr_level="A2",
            is_extension=False,
            display_order=5,
            is_active=True,
        )

        # =========================================
        # ACC-02
        # =========================================

        cls.acc_q25 = cls.create_question(
            skill=cls.acc_skill,
            source_id="DASM-ACC-025",
            difficulty="0.2500",
        )

        cls.acc_q50 = cls.create_question(
            skill=cls.acc_skill,
            source_id="DASM-ACC-050",
            difficulty="0.5000",
        )

        cls.acc_q75 = cls.create_question(
            skill=cls.acc_skill,
            source_id="DASM-ACC-075",
            difficulty="0.7500",
        )

        # =========================================
        # MOVE-01
        # =========================================

        cls.move_q25 = cls.create_question(
            skill=cls.move_skill,
            source_id="DASM-MOVE-025",
            difficulty="0.2500",
        )

        cls.move_q50 = cls.create_question(
            skill=cls.move_skill,
            source_id="DASM-MOVE-050",
            difficulty="0.5000",
        )

        cls.move_q75 = cls.create_question(
            skill=cls.move_skill,
            source_id="DASM-MOVE-075",
            difficulty="0.7500",
        )

        # =========================================
        # VOC-01
        # =========================================

        cls.voc01_q1 = cls.create_question(
            skill=cls.voc_a1_skill,
            source_id="DASM-VOC01-001",
            difficulty="0.2500",
        )

        cls.voc01_q2 = cls.create_question(
            skill=cls.voc_a1_skill,
            source_id="DASM-VOC01-002",
            difficulty="0.2500",
        )

        cls.voc01_q3 = cls.create_question(
            skill=cls.voc_a1_skill,
            source_id="DASM-VOC01-003",
            difficulty="0.2500",
        )

        # =========================================
        # VERB-05
        # =========================================

        cls.verb_q25 = cls.create_question(
            skill=cls.verb_a2_skill,
            source_id="DASM-VERB-025",
            difficulty="0.2500",
        )

        cls.verb_q50 = cls.create_question(
            skill=cls.verb_a2_skill,
            source_id="DASM-VERB-050",
            difficulty="0.5000",
        )

        cls.verb_q75 = cls.create_question(
            skill=cls.verb_a2_skill,
            source_id="DASM-VERB-075",
            difficulty="0.7500",
        )

        # =========================================
        # VOC-10
        # =========================================

        cls.voc10_q1 = cls.create_question(
            skill=cls.voc_a2_skill,
            source_id="DASM-VOC10-001",
            difficulty="0.5000",
        )

        cls.voc10_q2 = cls.create_question(
            skill=cls.voc_a2_skill,
            source_id="DASM-VOC10-002",
            difficulty="0.5000",
        )

        cls.voc10_q3 = cls.create_question(
            skill=cls.voc_a2_skill,
            source_id="DASM-VOC10-003",
            difficulty="0.5000",
        )

    @classmethod
    def create_question(
        cls,
        *,
        skill,
        source_id,
        difficulty,
    ):
        question = Question.objects.create(
            source_id=source_id,
            question_type="multiple_choice",
            item_format="single_choice",
            question_text=(
                f"Diagnostic assembly {source_id}."
            ),
            cefr_level=skill.cefr_level,
            is_extension=False,
            difficulty_score=Decimal(
                difficulty
            ),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=question,
            skill=skill,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

        return question

    def setUp(self):
        self.session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.DIAGNOSTIC,
            status=AssessmentSession.Status.ACTIVE,
        )

    def test_assembly_creates_exactly_fifteen_unique_questions(
        self,
    ):
        result = assemble_diagnostic_assessment(
            user=self.user,
            session_id=self.session.pk,
        )

        self.assertEqual(
            result.session_id,
            self.session.pk,
        )

        self.assertEqual(
            result.question_count,
            15,
        )

        self.assertEqual(
            len(result.slots),
            15,
        )

        session_questions = list(
            SessionQuestion.objects
            .filter(
                session=self.session,
            )
            .order_by(
                "sequence_number"
            )
        )

        self.assertEqual(
            len(session_questions),
            15,
        )

        self.assertEqual(
            [
                row.sequence_number
                for row in session_questions
            ],
            list(
                range(1, 16)
            ),
        )

        self.assertEqual(
            len(
                {
                    row.question_id
                    for row in session_questions
                }
            ),
            15,
        )

    def test_assembly_follows_frozen_blueprint_order(
        self,
    ):
        result = assemble_diagnostic_assessment(
            user=self.user,
            session_id=self.session.pk,
        )

        self.assertEqual(
            [
                slot.focus_skill_code
                for slot in result.slots
            ],
            [
                "ACC-02",
                "MOVE-01",
                "VOC-01",
                "VERB-05",
                "VOC-10",

                "ACC-02",
                "MOVE-01",
                "VOC-01",
                "VERB-05",
                "VOC-10",

                "ACC-02",
                "MOVE-01",
                "VOC-01",
                "VERB-05",
                "VOC-10",
            ],
        )

        self.assertEqual(
            [
                slot.target_difficulty
                for slot in result.slots
            ],
            [
                Decimal("0.2500"),
                Decimal("0.2500"),
                Decimal("0.2500"),
                Decimal("0.2500"),
                Decimal("0.5000"),

                Decimal("0.5000"),
                Decimal("0.5000"),
                Decimal("0.2500"),
                Decimal("0.5000"),
                Decimal("0.5000"),

                Decimal("0.7500"),
                Decimal("0.7500"),
                Decimal("0.2500"),
                Decimal("0.7500"),
                Decimal("0.5000"),
            ],
        )

        self.assertEqual(
            [
                slot.round_number
                for slot in result.slots
            ],
            [
                1, 1, 1, 1, 1,
                2, 2, 2, 2, 2,
                3, 3, 3, 3, 3,
            ],
        )

    def test_wrong_mode_and_nonempty_session_are_rejected(
        self,
    ):
        # -----------------------------------------
        # Adaptive mode is invalid.
        # -----------------------------------------

        adaptive_session = (
            AssessmentSession.objects.create(
                user=self.user,
                mode=AssessmentSession.Mode.ADAPTIVE,
                status=AssessmentSession.Status.ACTIVE,
            )
        )

        with self.assertRaises(
            LearningServiceError
        ):
            assemble_diagnostic_assessment(
                user=self.user,
                session_id=adaptive_session.pk,
            )

        self.assertEqual(
            SessionQuestion.objects.filter(
                session=adaptive_session
            ).count(),
            0,
        )

        # -----------------------------------------
        # Diagnostic session must initially be empty.
        # -----------------------------------------

        add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.acc_q25.pk,
        )

        self.assertEqual(
            SessionQuestion.objects.filter(
                session=self.session
            ).count(),
            1,
        )

        with self.assertRaises(
            LearningServiceError
        ):
            assemble_diagnostic_assessment(
                user=self.user,
                session_id=self.session.pk,
            )

        # Existing row stays, but assembly adds none.
        self.assertEqual(
            SessionQuestion.objects.filter(
                session=self.session
            ).count(),
            1,
        )

    def test_insufficient_anchor_bank_is_rejected_without_writes(
        self,
    ):
        # VOC-10 requires three unique Questions.
        #
        # Deactivate one, leaving only two.
        self.voc10_q3.is_active = False

        self.voc10_q3.save(
            update_fields=[
                "is_active",
            ]
        )

        with self.assertRaises(
            LearningServiceError
        ):
            assemble_diagnostic_assessment(
                user=self.user,
                session_id=self.session.pk,
            )

        # Planning fails before any write occurs.
        self.assertEqual(
            SessionQuestion.objects.filter(
                session=self.session
            ).count(),
            0,
        )

    def test_mid_assembly_failure_rolls_back_all_written_questions(
        self,
    ):
        real_add_question = (
            add_question_to_session
        )

        call_count = {
            "value": 0,
        }

        def fail_on_ninth_question(
            *,
            user,
            session_id,
            question_id,
        ):
            call_count[
                "value"
            ] += 1

            if call_count["value"] == 9:
                raise LearningServiceError(
                    "Forced diagnostic assembly "
                    "failure."
                )

            return real_add_question(
                user=user,
                session_id=session_id,
                question_id=question_id,
            )

        with patch(
            "learning_engine.services."
            "add_question_to_session",
            side_effect=(
                fail_on_ninth_question
            ),
        ):
            with self.assertRaises(
                LearningServiceError
            ):
                assemble_diagnostic_assessment(
                    user=self.user,
                    session_id=self.session.pk,
                )

        self.assertEqual(
            call_count["value"],
            9,
        )

        # Q1-Q8 were written before the forced
        # failure, but the outer transaction must
        # roll every one of them back.
        self.assertEqual(
            SessionQuestion.objects.filter(
                session=self.session
            ).count(),
            0,
        )

class ColdStartRoutingTests(TestCase):
    def test_unknown_new_learner_is_asked_about_prior_experience(
        self,
    ):
        route = determine_learner_entry_route(
            active_assessment_mode=None,
            has_finalized_assessment_evidence=False,
            prior_russian_experience=None,
        )

        self.assertEqual(
            route,
            LearnerEntryRoute.ASK_PRIOR_EXPERIENCE,
        )

    def test_true_beginner_goes_to_beginner_path(
        self,
    ):
        route = determine_learner_entry_route(
            active_assessment_mode=None,
            has_finalized_assessment_evidence=False,
            prior_russian_experience=False,
        )

        self.assertEqual(
            route,
            LearnerEntryRoute.BEGINNER_PATH,
        )

    def test_prior_experience_starts_diagnostic(
        self,
    ):
        route = determine_learner_entry_route(
            active_assessment_mode=None,
            has_finalized_assessment_evidence=False,
            prior_russian_experience=True,
        )

        self.assertEqual(
            route,
            LearnerEntryRoute.START_DIAGNOSTIC,
        )

    def test_existing_evidence_goes_to_adaptive(
        self,
    ):
        # Prior-experience answer no longer matters
        # once real assessment evidence exists.
        route = determine_learner_entry_route(
            active_assessment_mode=None,
            has_finalized_assessment_evidence=True,
            prior_russian_experience=False,
        )

        self.assertEqual(
            route,
            LearnerEntryRoute.ADAPTIVE,
        )

    def test_active_diagnostic_is_resumed_before_other_routes(
        self,
    ):
        route = determine_learner_entry_route(
            active_assessment_mode="diagnostic",
            has_finalized_assessment_evidence=True,
            prior_russian_experience=False,
        )

        self.assertEqual(
            route,
            LearnerEntryRoute.RESUME_DIAGNOSTIC,
        )

    def test_active_adaptive_is_resumed_before_other_routes(
        self,
    ):
        route = determine_learner_entry_route(
            active_assessment_mode="adaptive",
            has_finalized_assessment_evidence=False,
            prior_russian_experience=True,
        )

        self.assertEqual(
            route,
            LearnerEntryRoute.RESUME_ADAPTIVE,
        )

    def test_invalid_inputs_are_rejected(
        self,
    ):
        with self.assertRaises(
            ValueError
        ):
            determine_learner_entry_route(
                active_assessment_mode="lesson",
                has_finalized_assessment_evidence=False,
                prior_russian_experience=None,
            )

        with self.assertRaises(
            ValueError
        ):
            determine_learner_entry_route(
                active_assessment_mode=None,
                has_finalized_assessment_evidence=1,
                prior_russian_experience=None,
            )

        with self.assertRaises(
            ValueError
        ):
            determine_learner_entry_route(
                active_assessment_mode=None,
                has_finalized_assessment_evidence=False,
                prior_russian_experience="yes",
            )

class LearnerEntryStateIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="learner-entry@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Learner Entry Topic",
            category="grammar",
            display_order=18000,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="ENTRY-01",
            skill_name="Learner Entry Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.question = Question.objects.create(
            source_id="ENTRY-Q001",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text="Learner entry test question.",
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.2500"),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=cls.question,
            skill=cls.skill,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

    def _set_prior_experience(
        self,
        value,
    ):
        User.objects.filter(
            pk=self.user.pk,
        ).update(
            prior_russian_experience=value,
        )

        self.user.refresh_from_db(
            fields=[
                "prior_russian_experience",
            ]
        )

    def _create_session(
        self,
        *,
        mode,
        status=AssessmentSession.Status.ACTIVE,
    ):
        ended_at = None

        if status in {
            AssessmentSession.Status.COMPLETED,
            AssessmentSession.Status.ABANDONED,
        }:
            ended_at = timezone.now()

        return AssessmentSession.objects.create(
            user=self.user,
            mode=mode,
            status=status,
            ended_at=ended_at,
        )

    def _add_question(
        self,
        *,
        session,
        viewed=False,
    ):
        added = add_question_to_session(
            user=self.user,
            session_id=session.pk,
            question_id=self.question.pk,
        )

        if viewed:
            mark_session_question_viewed(
                user=self.user,
                session_question_id=(
                    added.session_question_id
                ),
            )

        return added

    def test_new_user_without_assessment_state_is_cold_start(
        self,
    ):
        self._set_prior_experience(
            None
        )

        state = resolve_learner_entry_state(
            user=self.user,
        )

        self.assertIsNone(
            state.prior_russian_experience
        )

        self.assertEqual(
            state.route,
            LearnerEntryRoute.ASK_PRIOR_EXPERIENCE,
        )

        self.assertIsNone(
            state.active_session_id
        )

        self.assertIsNone(
            state.active_assessment_mode
        )

        self.assertFalse(
            state.has_finalized_assessment_evidence
        )

    def test_completed_viewed_question_counts_as_finalized_evidence(
        self,
    ):
        # Even a persisted beginner answer no longer
        # controls routing once real finalized
        # assessment evidence exists.
        self._set_prior_experience(
            False
        )

        session = self._create_session(
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        self._add_question(
            session=session,
            viewed=True,
        )

        session.status = (
            AssessmentSession.Status.COMPLETED
        )

        session.ended_at = timezone.now()

        session.save(
            update_fields=[
                "status",
                "ended_at",
            ]
        )

        state = resolve_learner_entry_state(
            user=self.user,
        )

        self.assertTrue(
            state.has_finalized_assessment_evidence
        )

        self.assertFalse(
            state.prior_russian_experience
        )

        self.assertEqual(
            state.route,
            LearnerEntryRoute.ADAPTIVE,
        )

        self.assertIsNone(
            state.active_session_id
        )

    def test_completed_never_viewed_question_does_not_count_as_evidence(
        self,
    ):
        self._set_prior_experience(
            False
        )

        session = self._create_session(
            mode=AssessmentSession.Mode.DIAGNOSTIC,
        )

        # Selected into assessment, but the learner
        # never actually viewed it.
        self._add_question(
            session=session,
            viewed=False,
        )

        session.status = (
            AssessmentSession.Status.COMPLETED
        )

        session.ended_at = timezone.now()

        session.save(
            update_fields=[
                "status",
                "ended_at",
            ]
        )

        state = resolve_learner_entry_state(
            user=self.user,
        )

        self.assertFalse(
            state.has_finalized_assessment_evidence
        )

        self.assertFalse(
            state.prior_russian_experience
        )

        self.assertEqual(
            state.route,
            LearnerEntryRoute.BEGINNER_PATH,
        )

    def test_abandoned_viewed_question_does_not_count_as_finalized_evidence(
        self,
    ):
        self._set_prior_experience(
            True
        )

        session = self._create_session(
            mode=AssessmentSession.Mode.DIAGNOSTIC,
        )

        self._add_question(
            session=session,
            viewed=True,
        )

        session.status = (
            AssessmentSession.Status.ABANDONED
        )

        session.ended_at = timezone.now()

        session.save(
            update_fields=[
                "status",
                "ended_at",
            ]
        )

        state = resolve_learner_entry_state(
            user=self.user,
        )

        self.assertFalse(
            state.has_finalized_assessment_evidence
        )

        self.assertTrue(
            state.prior_russian_experience
        )

        # Prior study is persisted, but there is still
        # no finalized assessment evidence.
        self.assertEqual(
            state.route,
            LearnerEntryRoute.START_DIAGNOSTIC,
        )

    def test_active_diagnostic_is_resolved_for_resume(
        self,
    ):
        # Resume wins even if persisted onboarding
        # says the learner is a true beginner.
        self._set_prior_experience(
            False
        )

        session = self._create_session(
            mode=AssessmentSession.Mode.DIAGNOSTIC,
        )

        state = resolve_learner_entry_state(
            user=self.user,
        )

        self.assertEqual(
            state.route,
            LearnerEntryRoute.RESUME_DIAGNOSTIC,
        )

        self.assertEqual(
            state.active_session_id,
            session.pk,
        )

        self.assertEqual(
            state.active_assessment_mode,
            AssessmentSession.Mode.DIAGNOSTIC,
        )

    def test_active_adaptive_is_resolved_for_resume(
        self,
    ):
        # Resume wins even if persisted onboarding
        # would otherwise start a diagnostic.
        self._set_prior_experience(
            True
        )

        session = self._create_session(
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        state = resolve_learner_entry_state(
            user=self.user,
        )

        self.assertEqual(
            state.route,
            LearnerEntryRoute.RESUME_ADAPTIVE,
        )

        self.assertEqual(
            state.active_session_id,
            session.pk,
        )

        self.assertEqual(
            state.active_assessment_mode,
            AssessmentSession.Mode.ADAPTIVE,
        )

    def test_multiple_active_assessments_are_rejected(
        self,
    ):
        self._set_prior_experience(
            None
        )

        self._create_session(
            mode=AssessmentSession.Mode.DIAGNOSTIC,
        )

        self._create_session(
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        with self.assertRaises(
            ValueError
        ):
            resolve_learner_entry_state(
                user=self.user,
            )

    def test_resolver_reads_prior_experience_from_database_not_stale_instance(
        self,
    ):
        # Persisted value remains NULL.
        self._set_prior_experience(
            None
        )

        # Mutate only the Python object.
        # Deliberately do NOT save it.
        self.user.prior_russian_experience = True

        state = resolve_learner_entry_state(
            user=self.user,
        )

        # Persisted NULL must win over the stale
        # in-memory value.
        self.assertIsNone(
            state.prior_russian_experience
        )

        self.assertEqual(
            state.route,
            LearnerEntryRoute.ASK_PRIOR_EXPERIENCE,
        )

class PriorRussianExperienceSubmissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="onboarding-submit@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Onboarding Diagnostic Topic",
            category="grammar",
            display_order=19000,
            is_active=True,
        )

        # =========================================
        # Canonical diagnostic anchors
        # =========================================

        skill_specs = (
            (
                "ACC-02",
                "A1",
                "Onboarding ACC-02",
            ),
            (
                "MOVE-01",
                "A1",
                "Onboarding MOVE-01",
            ),
            (
                "VOC-01",
                "A1",
                "Onboarding VOC-01",
            ),
            (
                "VERB-05",
                "A2",
                "Onboarding VERB-05",
            ),
            (
                "VOC-10",
                "A2",
                "Onboarding VOC-10",
            ),
        )

        cls.skills = {}

        for (
            index,
            (
                skill_code,
                cefr_level,
                skill_name,
            ),
        ) in enumerate(
            skill_specs,
            start=1,
        ):
            cls.skills[
                skill_code
            ] = Skill.objects.create(
                topic=cls.topic,
                skill_code=skill_code,
                skill_name=skill_name,
                cefr_level=cefr_level,
                is_extension=False,
                display_order=index,
                is_active=True,
            )

        # =========================================
        # Question Bank sufficient for the exact
        # 15-question diagnostic blueprint.
        # =========================================

        question_specs = (
            # ACC-02
            (
                "ACC-02",
                "ONB-ACC-025",
                "0.2500",
            ),
            (
                "ACC-02",
                "ONB-ACC-050",
                "0.5000",
            ),
            (
                "ACC-02",
                "ONB-ACC-075",
                "0.7500",
            ),

            # MOVE-01
            (
                "MOVE-01",
                "ONB-MOVE-025",
                "0.2500",
            ),
            (
                "MOVE-01",
                "ONB-MOVE-050",
                "0.5000",
            ),
            (
                "MOVE-01",
                "ONB-MOVE-075",
                "0.7500",
            ),

            # VOC-01
            (
                "VOC-01",
                "ONB-VOC01-001",
                "0.2500",
            ),
            (
                "VOC-01",
                "ONB-VOC01-002",
                "0.2500",
            ),
            (
                "VOC-01",
                "ONB-VOC01-003",
                "0.2500",
            ),

            # VERB-05
            (
                "VERB-05",
                "ONB-VERB-025",
                "0.2500",
            ),
            (
                "VERB-05",
                "ONB-VERB-050",
                "0.5000",
            ),
            (
                "VERB-05",
                "ONB-VERB-075",
                "0.7500",
            ),

            # VOC-10
            (
                "VOC-10",
                "ONB-VOC10-001",
                "0.5000",
            ),
            (
                "VOC-10",
                "ONB-VOC10-002",
                "0.5000",
            ),
            (
                "VOC-10",
                "ONB-VOC10-003",
                "0.5000",
            ),
        )

        cls.questions = {}

        for (
            skill_code,
            source_id,
            difficulty,
        ) in question_specs:
            skill = cls.skills[
                skill_code
            ]

            question = Question.objects.create(
                source_id=source_id,
                question_type="multiple_choice",
                item_format="single_choice",
                question_text=(
                    f"Onboarding diagnostic "
                    f"{source_id}."
                ),
                cefr_level=skill.cefr_level,
                is_extension=False,
                difficulty_score=Decimal(
                    difficulty
                ),
                correct_answer=None,
                accepted_answers=None,
                explanation=None,
                audio_url=None,
                is_active=True,
            )

            QuestionSkill.objects.create(
                question=question,
                skill=skill,
                is_primary=True,
                weight=Decimal("1.00000"),
            )

            cls.questions[
                source_id
            ] = question

    def setUp(self):
        # Always begin each test at the real
        # unanswered onboarding state.
        User.objects.filter(
            pk=self.user.pk,
        ).update(
            prior_russian_experience=None,
        )

        self.user.refresh_from_db()

    def test_beginner_answer_is_persisted_without_assessment(
        self,
    ):
        result = submit_prior_russian_experience(
            user=self.user,
            prior_russian_experience=False,
        )

        self.user.refresh_from_db()

        self.assertFalse(
            self.user.prior_russian_experience
        )

        self.assertEqual(
            result.user_id,
            self.user.pk,
        )

        self.assertFalse(
            result.prior_russian_experience
        )

        self.assertEqual(
            result.route,
            LearnerEntryRoute.BEGINNER_PATH,
        )

        self.assertIsNone(
            result.assessment_session_id
        )

        self.assertEqual(
            result.question_count,
            0,
        )

        self.assertEqual(
            AssessmentSession.objects.filter(
                user=self.user,
            ).count(),
            0,
        )

    def test_prior_experience_creates_complete_diagnostic_assessment(
        self,
    ):
        result = submit_prior_russian_experience(
            user=self.user,
            prior_russian_experience=True,
        )

        self.user.refresh_from_db()

        self.assertTrue(
            self.user.prior_russian_experience
        )

        self.assertTrue(
            result.prior_russian_experience
        )

        self.assertEqual(
            result.route,
            LearnerEntryRoute.RESUME_DIAGNOSTIC,
        )

        self.assertIsNotNone(
            result.assessment_session_id
        )

        self.assertEqual(
            result.question_count,
            15,
        )

        session = AssessmentSession.objects.get(
            pk=result.assessment_session_id
        )

        self.assertEqual(
            session.user,
            self.user,
        )

        self.assertEqual(
            session.mode,
            AssessmentSession.Mode.DIAGNOSTIC,
        )

        self.assertEqual(
            session.status,
            AssessmentSession.Status.ACTIVE,
        )

        session_questions = list(
            SessionQuestion.objects
            .filter(
                session=session,
            )
            .order_by(
                "sequence_number"
            )
        )

        self.assertEqual(
            len(session_questions),
            15,
        )

        self.assertEqual(
            [
                row.sequence_number
                for row in session_questions
            ],
            list(
                range(1, 16)
            ),
        )

        self.assertEqual(
            len(
                {
                    row.question_id
                    for row in session_questions
                }
            ),
            15,
        )

        # Authoritative resolver must now see the
        # newly created ACTIVE diagnostic.
        state = resolve_learner_entry_state(
            user=self.user,
        )

        self.assertEqual(
            state.route,
            LearnerEntryRoute.RESUME_DIAGNOSTIC,
        )

        self.assertEqual(
            state.active_session_id,
            session.pk,
        )

    def test_invalid_prior_experience_value_is_rejected(
        self,
    ):
        invalid_values = (
            None,
            0,
            1,
            "yes",
        )

        for value in invalid_values:
            with self.subTest(
                value=value
            ):
                with self.assertRaises(
                    LearningServiceError
                ):
                    submit_prior_russian_experience(
                        user=self.user,
                        prior_russian_experience=value,
                    )

        self.user.refresh_from_db()

        self.assertIsNone(
            self.user.prior_russian_experience
        )

        self.assertEqual(
            AssessmentSession.objects.filter(
                user=self.user,
            ).count(),
            0,
        )

    def test_onboarding_answer_cannot_be_submitted_twice(
        self,
    ):
        first = submit_prior_russian_experience(
            user=self.user,
            prior_russian_experience=False,
        )

        self.assertEqual(
            first.route,
            LearnerEntryRoute.BEGINNER_PATH,
        )

        with self.assertRaises(
            LearningServiceError
        ):
            submit_prior_russian_experience(
                user=self.user,
                prior_russian_experience=True,
            )

        self.user.refresh_from_db()

        # Original authoritative decision remains.
        self.assertFalse(
            self.user.prior_russian_experience
        )

        # No diagnostic may be created by the
        # rejected second submission.
        self.assertEqual(
            AssessmentSession.objects.filter(
                user=self.user,
            ).count(),
            0,
        )

    def test_diagnostic_failure_rolls_back_answer_and_session(
        self,
    ):
        # Diagnostic requires three VOC-10
        # Questions. Make the bank insufficient.
        question = self.questions[
            "ONB-VOC10-003"
        ]

        question.is_active = False

        question.save(
            update_fields=[
                "is_active",
            ]
        )

        with self.assertRaises(
            LearningServiceError
        ):
            submit_prior_russian_experience(
                user=self.user,
                prior_russian_experience=True,
            )

        self.user.refresh_from_db()

        # The True answer was saved before diagnostic
        # assembly began, but the outer transaction
        # must restore NULL.
        self.assertIsNone(
            self.user.prior_russian_experience
        )

        # start_session() also happened inside the
        # transaction and must be rolled back.
        self.assertEqual(
            AssessmentSession.objects.filter(
                user=self.user,
            ).count(),
            0,
        )

        self.assertEqual(
            SessionQuestion.objects.filter(
                session__user=self.user,
            ).count(),
            0,
        )

class BeginnerPathResolverIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="beginner-path@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Beginner Path Topic",
            category="grammar",
            display_order=20000,
            is_active=True,
        )

        cls.a1_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="BPATH-A1",
            skill_name="Beginner Path A1",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.a1_skill_2 = Skill.objects.create(
            topic=cls.topic,
            skill_code="BPATH-A1-2",
            skill_name="Beginner Path A1 Second",
            cefr_level="A1",
            is_extension=False,
            display_order=2,
            is_active=True,
        )

        cls.a2_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="BPATH-A2",
            skill_name="Beginner Path A2",
            cefr_level="A2",
            is_extension=False,
            display_order=3,
            is_active=True,
        )

        cls.extension_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="BPATH-EXT",
            skill_name="Beginner Path Extension",
            cefr_level="A1",
            is_extension=True,
            display_order=4,
            is_active=True,
        )

        cls.inactive_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="BPATH-INACTIVE",
            skill_name="Beginner Path Inactive",
            cefr_level="A1",
            is_extension=False,
            display_order=5,
            is_active=False,
        )

    def create_lesson(
        self,
        *,
        title,
        display_order,
        skills,
        is_active=True,
    ):
        lesson = Lesson.objects.create(
            title=title,
            lesson_type="grammar",
            content={
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=display_order,
            is_active=is_active,
        )

        for skill in skills:
            LessonSkill.objects.create(
                lesson=lesson,
                skill=skill,
            )

        return lesson

    def test_no_eligible_lessons_returns_explicit_unavailable_state(
        self,
    ):
        result = resolve_beginner_lesson(
            user=self.user,
        )

        self.assertEqual(
            result.action,
            BeginnerLessonAction.NO_AVAILABLE_LESSON,
        )

        self.assertEqual(
            result.unavailable_reason,
            (
                BeginnerLessonUnavailableReason
                .NO_ELIGIBLE_LESSONS
            ),
        )

        self.assertIsNone(
            result.lesson_id
        )

        self.assertIsNone(
            result.lesson_progress_id
        )

    def test_earliest_core_a1_lesson_is_selected_without_creating_progress(
        self,
    ):
        later = self.create_lesson(
            title="Later A1 Lesson",
            display_order=20,
            skills=[
                self.a1_skill,
            ],
        )

        earlier = self.create_lesson(
            title="Earlier A1 Lesson",
            display_order=10,
            skills=[
                self.a1_skill,
            ],
        )

        result = resolve_beginner_lesson(
            user=self.user,
        )

        self.assertEqual(
            result.action,
            BeginnerLessonAction.START_LESSON,
        )

        self.assertEqual(
            result.lesson_id,
            earlier.pk,
        )

        self.assertEqual(
            result.lesson_title,
            "Earlier A1 Lesson",
        )

        self.assertEqual(
            result.lesson_display_order,
            10,
        )

        self.assertIsNone(
            result.lesson_progress_id
        )

        # Resolver is read-only.
        self.assertEqual(
            LessonProgress.objects.filter(
                user=self.user,
            ).count(),
            0,
        )

        self.assertNotEqual(
            result.lesson_id,
            later.pk,
        )

    def test_most_recent_in_progress_lesson_is_resumed_before_new_lesson(
        self,
    ):
        first = self.create_lesson(
            title="First Beginner Lesson",
            display_order=1,
            skills=[
                self.a1_skill,
            ],
        )

        second = self.create_lesson(
            title="Second Beginner Lesson",
            display_order=2,
            skills=[
                self.a1_skill_2,
            ],
        )

        third = self.create_lesson(
            title="Third Beginner Lesson",
            display_order=3,
            skills=[
                self.a1_skill,
            ],
        )

        older_time = (
            timezone.now()
            - timedelta(days=2)
        )

        newer_time = (
            timezone.now()
            - timedelta(hours=2)
        )

        older_progress = (
            LessonProgress.objects.create(
                user=self.user,
                lesson=first,
                status=(
                    LessonProgress.Status.IN_PROGRESS
                ),
                completion_ratio=Decimal("0.3000"),
                last_accessed_at=older_time,
            )
        )

        newer_progress = (
            LessonProgress.objects.create(
                user=self.user,
                lesson=second,
                status=(
                    LessonProgress.Status.IN_PROGRESS
                ),
                completion_ratio=Decimal("0.6000"),
                last_accessed_at=newer_time,
            )
        )

        result = resolve_beginner_lesson(
            user=self.user,
        )

        self.assertEqual(
            result.action,
            BeginnerLessonAction.RESUME_LESSON,
        )

        self.assertEqual(
            result.lesson_id,
            second.pk,
        )

        self.assertEqual(
            result.lesson_progress_id,
            newer_progress.pk,
        )

        # Resume priority wins even though another
        # untouched eligible Lesson exists.
        self.assertNotEqual(
            result.lesson_id,
            third.pk,
        )

        self.assertNotEqual(
            result.lesson_progress_id,
            older_progress.pk,
        )

    def test_completed_lessons_are_skipped_and_next_unfinished_is_selected(
        self,
    ):
        first = self.create_lesson(
            title="Completed Beginner Lesson",
            display_order=1,
            skills=[
                self.a1_skill,
            ],
        )

        second = self.create_lesson(
            title="Next Beginner Lesson",
            display_order=2,
            skills=[
                self.a1_skill_2,
            ],
        )

        LessonProgress.objects.create(
            user=self.user,
            lesson=first,
            status=LessonProgress.Status.COMPLETED,
            completion_ratio=Decimal("1.0000"),
            completed_at=timezone.now(),
            last_accessed_at=timezone.now(),
        )

        result = resolve_beginner_lesson(
            user=self.user,
        )

        self.assertEqual(
            result.action,
            BeginnerLessonAction.START_LESSON,
        )

        self.assertEqual(
            result.lesson_id,
            second.pk,
        )

        self.assertIsNone(
            result.lesson_progress_id
        )

    def test_all_completed_returns_all_completed_state(
        self,
    ):
        first = self.create_lesson(
            title="Completed A1 One",
            display_order=1,
            skills=[
                self.a1_skill,
            ],
        )

        second = self.create_lesson(
            title="Completed A1 Two",
            display_order=2,
            skills=[
                self.a1_skill_2,
            ],
        )

        completed_at = timezone.now()

        for lesson in (
            first,
            second,
        ):
            LessonProgress.objects.create(
                user=self.user,
                lesson=lesson,
                status=(
                    LessonProgress.Status.COMPLETED
                ),
                completion_ratio=Decimal("1.0000"),
                completed_at=completed_at,
                last_accessed_at=completed_at,
            )

        result = resolve_beginner_lesson(
            user=self.user,
        )

        self.assertEqual(
            result.action,
            BeginnerLessonAction.NO_AVAILABLE_LESSON,
        )

        self.assertEqual(
            result.unavailable_reason,
            (
                BeginnerLessonUnavailableReason
                .ALL_COMPLETED
            ),
        )

        self.assertIsNone(
            result.lesson_id
        )

        self.assertIsNone(
            result.lesson_progress_id
        )

    def test_ineligible_lessons_are_excluded_from_beginner_path(
        self,
    ):
        # No Skill mapping.
        self.create_lesson(
            title="No Skill Lesson",
            display_order=1,
            skills=[],
        )

        # A2-only.
        self.create_lesson(
            title="A2 Lesson",
            display_order=2,
            skills=[
                self.a2_skill,
            ],
        )

        # Extension Skill.
        self.create_lesson(
            title="Extension Lesson",
            display_order=3,
            skills=[
                self.extension_skill,
            ],
        )

        # Inactive Skill.
        self.create_lesson(
            title="Inactive Skill Lesson",
            display_order=4,
            skills=[
                self.inactive_skill,
            ],
        )

        # Mixed A1 + A2:
        # must NOT count as a core beginner Lesson.
        self.create_lesson(
            title="Mixed A1 A2 Lesson",
            display_order=5,
            skills=[
                self.a1_skill,
                self.a2_skill,
            ],
        )

        # Inactive Lesson despite valid A1 mapping.
        self.create_lesson(
            title="Inactive A1 Lesson",
            display_order=6,
            skills=[
                self.a1_skill,
            ],
            is_active=False,
        )

        valid = self.create_lesson(
            title="Valid Core A1 Lesson",
            display_order=100,
            skills=[
                self.a1_skill,
                self.a1_skill_2,
            ],
        )

        result = resolve_beginner_lesson(
            user=self.user,
        )

        self.assertEqual(
            result.action,
            BeginnerLessonAction.START_LESSON,
        )

        self.assertEqual(
            result.lesson_id,
            valid.pk,
        )

        self.assertEqual(
            result.lesson_title,
            "Valid Core A1 Lesson",
        )

class LearnerEntryAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="entry-api@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Learner Entry API Topic",
            category="grammar",
            display_order=21000,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="ENTRY-API-A1",
            skill_name="Learner Entry API A1 Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.lesson = Lesson.objects.create(
            title="Learner Entry API Beginner Lesson",
            lesson_type="grammar",
            content={
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=1,
            is_active=True,
        )

        LessonSkill.objects.create(
            lesson=cls.lesson,
            skill=cls.skill,
        )

        cls.question = Question.objects.create(
            source_id="ENTRY-API-Q001",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text="Learner entry API evidence question.",
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.2500"),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=cls.question,
            skill=cls.skill,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

    def setUp(self):
        self.url = reverse(
            "learning_engine:learner-entry"
        )

        # Every test begins with unanswered
        # onboarding and no stale in-memory value.
        User.objects.filter(
            pk=self.user.pk,
        ).update(
            prior_russian_experience=None,
        )

        self.user.refresh_from_db()

    def _login(self):
        self.client.force_login(
            self.user
        )

    def _set_prior_experience(
        self,
        value,
    ):
        User.objects.filter(
            pk=self.user.pk,
        ).update(
            prior_russian_experience=value,
        )

        self.user.refresh_from_db(
            fields=[
                "prior_russian_experience",
            ]
        )

    def _create_active_session(
        self,
        *,
        mode,
    ):
        return AssessmentSession.objects.create(
            user=self.user,
            mode=mode,
            status=AssessmentSession.Status.ACTIVE,
        )

    def _create_finalized_evidence(
        self,
    ):
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

        added = add_question_to_session(
            user=self.user,
            session_id=session.pk,
            question_id=self.question.pk,
        )

        mark_session_question_viewed(
            user=self.user,
            session_question_id=(
                added.session_question_id
            ),
        )

        complete_session(
            user=self.user,
            session_id=session.pk,
        )

        return session

    def test_authentication_is_required(
        self,
    ):
        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertIn(
            "next=",
            response.url,
        )

    def test_unknown_new_learner_returns_ask_prior_experience(
        self,
    ):
        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["route"],
            "ask_prior_experience",
        )

        self.assertIsNone(
            payload["prior_russian_experience"]
        )

        self.assertFalse(
            payload[
                "has_finalized_assessment_evidence"
            ]
        )

        self.assertIsNone(
            payload["active_assessment"]
        )

        self.assertIsNone(
            payload["beginner_lesson"]
        )

    def test_true_beginner_returns_resolved_beginner_lesson(
        self,
    ):
        self._set_prior_experience(
            False
        )

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["route"],
            "beginner_path",
        )

        self.assertFalse(
            payload["prior_russian_experience"]
        )

        self.assertIsNone(
            payload["active_assessment"]
        )

        beginner_lesson = (
            payload["beginner_lesson"]
        )

        self.assertIsNotNone(
            beginner_lesson
        )

        self.assertEqual(
            beginner_lesson["action"],
            "start_lesson",
        )

        self.assertEqual(
            beginner_lesson["lesson_id"],
            self.lesson.pk,
        )

        self.assertEqual(
            beginner_lesson["lesson_title"],
            self.lesson.title,
        )

        self.assertEqual(
            beginner_lesson[
                "lesson_display_order"
            ],
            self.lesson.display_order,
        )

        self.assertIsNone(
            beginner_lesson[
                "lesson_progress_id"
            ]
        )

        self.assertIsNone(
            beginner_lesson[
                "unavailable_reason"
            ]
        )

        # GET entry state must stay read-only.
        self.assertEqual(
            LessonProgress.objects.filter(
                user=self.user,
            ).count(),
            0,
        )

    def test_active_diagnostic_returns_resume_diagnostic(
        self,
    ):
        self._set_prior_experience(
            True
        )

        session = self._create_active_session(
            mode=(
                AssessmentSession.Mode.DIAGNOSTIC
            ),
        )

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["route"],
            "resume_diagnostic",
        )

        self.assertEqual(
            payload["active_assessment"],
            {
                "session_id": session.pk,
                "mode": "diagnostic",
            },
        )

        self.assertIsNone(
            payload["beginner_lesson"]
        )

    def test_active_adaptive_returns_resume_adaptive(
        self,
    ):
        # Resume has priority even if onboarding
        # state would otherwise suggest diagnostic.
        self._set_prior_experience(
            True
        )

        session = self._create_active_session(
            mode=(
                AssessmentSession.Mode.ADAPTIVE
            ),
        )

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["route"],
            "resume_adaptive",
        )

        self.assertEqual(
            payload["active_assessment"],
            {
                "session_id": session.pk,
                "mode": "adaptive",
            },
        )

        self.assertIsNone(
            payload["beginner_lesson"]
        )

    def test_finalized_assessment_evidence_returns_adaptive_route(
        self,
    ):
        # Deliberately persist False.
        #
        # Real assessment evidence must still
        # override the old onboarding answer.
        self._set_prior_experience(
            False
        )

        self._create_finalized_evidence()

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["route"],
            "adaptive",
        )

        self.assertTrue(
            payload[
                "has_finalized_assessment_evidence"
            ]
        )

        self.assertIsNone(
            payload["active_assessment"]
        )

        self.assertIsNone(
            payload["beginner_lesson"]
        )

    def test_multiple_active_assessments_return_conflict(
        self,
    ):
        self._create_active_session(
            mode=(
                AssessmentSession.Mode.DIAGNOSTIC
            ),
        )

        self._create_active_session(
            mode=(
                AssessmentSession.Mode.ADAPTIVE
            ),
        )

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            409,
        )

        payload = response.json()

        self.assertEqual(
            payload["error"],
            "learner_state_conflict",
        )

        self.assertIn(
            "more than one active",
            payload["detail"].lower(),
        )

class PriorExperienceAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="prior-experience-api@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Prior Experience API Topic",
            category="grammar",
            display_order=22000,
            is_active=True,
        )

        # =========================================
        # Beginner Lesson
        # =========================================

        cls.beginner_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="ONBOARD-API-A1",
            skill_name="Onboarding API Beginner Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.beginner_lesson = Lesson.objects.create(
            title="Onboarding API Beginner Lesson",
            lesson_type="grammar",
            content={
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=1,
            is_active=True,
        )

        LessonSkill.objects.create(
            lesson=cls.beginner_lesson,
            skill=cls.beginner_skill,
        )

        # =========================================
        # Canonical diagnostic anchors
        # =========================================

        skill_specs = (
            ("ACC-02", "A1"),
            ("MOVE-01", "A1"),
            ("VOC-01", "A1"),
            ("VERB-05", "A2"),
            ("VOC-10", "A2"),
        )

        cls.diagnostic_skills = {}

        for index, (
            skill_code,
            cefr_level,
        ) in enumerate(
            skill_specs,
            start=10,
        ):
            skill = Skill.objects.create(
                topic=cls.topic,
                skill_code=skill_code,
                skill_name=(
                    f"API Diagnostic {skill_code}"
                ),
                cefr_level=cefr_level,
                is_extension=False,
                display_order=index,
                is_active=True,
            )

            cls.diagnostic_skills[
                skill_code
            ] = skill

        question_specs = (
            ("ACC-02", "API-ACC-025", "0.2500"),
            ("ACC-02", "API-ACC-050", "0.5000"),
            ("ACC-02", "API-ACC-075", "0.7500"),

            ("MOVE-01", "API-MOVE-025", "0.2500"),
            ("MOVE-01", "API-MOVE-050", "0.5000"),
            ("MOVE-01", "API-MOVE-075", "0.7500"),

            ("VOC-01", "API-VOC01-001", "0.2500"),
            ("VOC-01", "API-VOC01-002", "0.2500"),
            ("VOC-01", "API-VOC01-003", "0.2500"),

            ("VERB-05", "API-VERB-025", "0.2500"),
            ("VERB-05", "API-VERB-050", "0.5000"),
            ("VERB-05", "API-VERB-075", "0.7500"),

            ("VOC-10", "API-VOC10-001", "0.5000"),
            ("VOC-10", "API-VOC10-002", "0.5000"),
            ("VOC-10", "API-VOC10-003", "0.5000"),
        )

        cls.diagnostic_questions = {}

        for (
            skill_code,
            source_id,
            difficulty,
        ) in question_specs:
            skill = cls.diagnostic_skills[
                skill_code
            ]

            question = Question.objects.create(
                source_id=source_id,
                question_type="multiple_choice",
                item_format="single_choice",
                question_text=(
                    f"Prior experience API "
                    f"{source_id}."
                ),
                cefr_level=skill.cefr_level,
                is_extension=False,
                difficulty_score=Decimal(
                    difficulty
                ),
                correct_answer=None,
                accepted_answers=None,
                explanation=None,
                audio_url=None,
                is_active=True,
            )

            QuestionSkill.objects.create(
                question=question,
                skill=skill,
                is_primary=True,
                weight=Decimal("1.00000"),
            )

            cls.diagnostic_questions[
                source_id
            ] = question

    def setUp(self):
        self.url = reverse(
            "learning_engine:"
            "submit-prior-experience"
        )

        User.objects.filter(
            pk=self.user.pk,
        ).update(
            prior_russian_experience=None,
        )

        self.user.refresh_from_db()

    def _login(self):
        self.client.force_login(
            self.user
        )

    def _post_json(
        self,
        payload,
    ):
        return self.client.post(
            self.url,
            data=json.dumps(
                payload
            ),
            content_type="application/json",
        )

    def test_authentication_is_required(
        self,
    ):
        response = self._post_json(
            {
                "prior_russian_experience": False,
            }
        )

        self.assertEqual(
            response.status_code,
            302,
        )

    def test_false_persists_beginner_answer_and_returns_beginner_lesson(
        self,
    ):
        self._login()

        response = self._post_json(
            {
                "prior_russian_experience": False,
            }
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["route"],
            "beginner_path",
        )

        self.assertFalse(
            payload[
                "prior_russian_experience"
            ]
        )

        self.assertIsNone(
            payload["active_assessment"]
        )

        self.assertEqual(
            payload[
                "beginner_lesson"
            ][
                "action"
            ],
            "start_lesson",
        )

        self.assertEqual(
            payload[
                "beginner_lesson"
            ][
                "lesson_id"
            ],
            self.beginner_lesson.pk,
        )

        self.user.refresh_from_db()

        self.assertFalse(
            self.user.prior_russian_experience
        )

        self.assertEqual(
            AssessmentSession.objects.filter(
                user=self.user,
            ).count(),
            0,
        )

        # Resolving the Lesson remains read-only.
        self.assertEqual(
            LessonProgress.objects.filter(
                user=self.user,
            ).count(),
            0,
        )

    def test_true_creates_and_returns_fifteen_question_diagnostic(
        self,
    ):
        self._login()

        response = self._post_json(
            {
                "prior_russian_experience": True,
            }
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["route"],
            "resume_diagnostic",
        )

        self.assertTrue(
            payload[
                "prior_russian_experience"
            ]
        )

        self.assertIsNone(
            payload["beginner_lesson"]
        )

        active = payload[
            "active_assessment"
        ]

        self.assertIsNotNone(
            active
        )

        self.assertEqual(
            active["mode"],
            "diagnostic",
        )

        self.assertEqual(
            active["question_count"],
            15,
        )

        session = AssessmentSession.objects.get(
            pk=active["session_id"]
        )

        self.assertEqual(
            session.user,
            self.user,
        )

        self.assertEqual(
            session.mode,
            AssessmentSession.Mode.DIAGNOSTIC,
        )

        self.assertEqual(
            session.status,
            AssessmentSession.Status.ACTIVE,
        )

        session_questions = (
            SessionQuestion.objects
            .filter(
                session=session,
            )
            .order_by(
                "sequence_number"
            )
        )

        self.assertEqual(
            session_questions.count(),
            15,
        )

        self.assertEqual(
            list(
                session_questions.values_list(
                    "sequence_number",
                    flat=True,
                )
            ),
            list(
                range(1, 16)
            ),
        )

        self.assertEqual(
            session_questions.values(
                "question_id"
            ).distinct().count(),
            15,
        )

        self.user.refresh_from_db()

        self.assertTrue(
            self.user.prior_russian_experience
        )

    def test_invalid_json_and_invalid_field_type_return_400(
        self,
    ):
        self._login()

        invalid_json_response = (
            self.client.post(
                self.url,
                data="{broken-json",
                content_type=(
                    "application/json"
                ),
            )
        )

        self.assertEqual(
            invalid_json_response.status_code,
            400,
        )

        self.assertEqual(
            invalid_json_response.json()[
                "error"
            ],
            "invalid_json",
        )

        invalid_type_response = (
            self._post_json(
                {
                    "prior_russian_experience":
                        "yes",
                }
            )
        )

        self.assertEqual(
            invalid_type_response.status_code,
            400,
        )

        self.assertEqual(
            invalid_type_response.json()[
                "error"
            ],
            "invalid_field",
        )

        self.user.refresh_from_db()

        self.assertIsNone(
            self.user.prior_russian_experience
        )

    def test_second_submission_returns_conflict_without_changing_original_answer(
        self,
    ):
        self._login()

        first = self._post_json(
            {
                "prior_russian_experience": False,
            }
        )

        self.assertEqual(
            first.status_code,
            200,
        )

        second = self._post_json(
            {
                "prior_russian_experience": True,
            }
        )

        self.assertEqual(
            second.status_code,
            409,
        )

        self.assertEqual(
            second.json()["error"],
            "onboarding_conflict",
        )

        self.user.refresh_from_db()

        self.assertFalse(
            self.user.prior_russian_experience
        )

        self.assertEqual(
            AssessmentSession.objects.filter(
                user=self.user,
            ).count(),
            0,
        )

    def test_diagnostic_failure_returns_409_and_rolls_back_everything(
        self,
    ):
        self._login()

        # Leave only two active VOC-10 Questions.
        broken_question = (
            self.diagnostic_questions[
                "API-VOC10-003"
            ]
        )

        broken_question.is_active = False

        broken_question.save(
            update_fields=[
                "is_active",
            ]
        )

        response = self._post_json(
            {
                "prior_russian_experience": True,
            }
        )

        self.assertEqual(
            response.status_code,
            409,
        )

        self.assertEqual(
            response.json()["error"],
            "onboarding_conflict",
        )

        self.user.refresh_from_db()

        # The onboarding answer was written before
        # assembly, but outer transaction must
        # restore NULL.
        self.assertIsNone(
            self.user.prior_russian_experience
        )

        self.assertEqual(
            AssessmentSession.objects.filter(
                user=self.user,
            ).count(),
            0,
        )

        self.assertEqual(
            SessionQuestion.objects.filter(
                session__user=self.user,
            ).count(),
            0,
        )

class LessonDetailAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="lesson-detail-api@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Lesson Detail API Topic",
            category="grammar",
            display_order=23000,
            is_active=True,
        )

        cls.skill_1 = Skill.objects.create(
            topic=cls.topic,
            skill_code="LDETAIL-A1-01",
            skill_name="Lesson Detail First Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.skill_2 = Skill.objects.create(
            topic=cls.topic,
            skill_code="LDETAIL-A1-02",
            skill_name="Lesson Detail Second Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=2,
            is_active=True,
        )

        cls.lesson = Lesson.objects.create(
            title="Lesson Detail API Lesson",
            lesson_type="grammar",
            content={
                "theory": [
                    {
                        "type": "text",
                        "text": "Theory example.",
                    },
                ],
                "examples": [
                    {
                        "ru": "Это пример.",
                        "vi": "Đây là ví dụ.",
                    },
                ],
                "practice": [],
            },
            audio_url=(
                "https://example.test/"
                "lesson-detail.mp3"
            ),
            display_order=5,
            is_active=True,
        )

        # Create in reverse order intentionally.
        #
        # API ordering must depend on Skill
        # display_order, not insertion order.
        LessonSkill.objects.create(
            lesson=cls.lesson,
            skill=cls.skill_2,
        )

        LessonSkill.objects.create(
            lesson=cls.lesson,
            skill=cls.skill_1,
        )

        cls.inactive_lesson = Lesson.objects.create(
            title="Inactive Lesson Detail API Lesson",
            lesson_type="grammar",
            content={
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=6,
            is_active=False,
        )

        LessonSkill.objects.create(
            lesson=cls.inactive_lesson,
            skill=cls.skill_1,
        )

    def setUp(self):
        self.url = reverse(
            "learning_engine:lesson-detail",
            kwargs={
                "lesson_id": self.lesson.pk,
            },
        )

    def _login(self):
        self.client.force_login(
            self.user
        )

    def test_authentication_is_required(
        self,
    ):
        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertIn(
            "next=",
            response.url,
        )

    def test_active_lesson_detail_is_serialized(
        self,
    ):
        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["lesson_id"],
            self.lesson.pk,
        )

        self.assertEqual(
            payload["title"],
            self.lesson.title,
        )

        self.assertEqual(
            payload["lesson_type"],
            "grammar",
        )

        self.assertEqual(
            payload["display_order"],
            5,
        )

        self.assertEqual(
            payload["content"],
            self.lesson.content,
        )

        self.assertEqual(
            payload["audio_url"],
            self.lesson.audio_url,
        )

        self.assertIsNone(
            payload["progress"]
        )

    def test_skills_are_serialized_in_deterministic_order(
        self,
    ):
        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        skills = response.json()[
            "skills"
        ]

        self.assertEqual(
            len(skills),
            2,
        )

        self.assertEqual(
            [
                row["skill_code"]
                for row in skills
            ],
            [
                "LDETAIL-A1-01",
                "LDETAIL-A1-02",
            ],
        )

        self.assertEqual(
            skills[0],
            {
                "skill_id": self.skill_1.pk,
                "skill_code": "LDETAIL-A1-01",
                "skill_name": (
                    "Lesson Detail First Skill"
                ),
                "cefr_level": "A1",
                "is_extension": False,
            },
        )

        self.assertEqual(
            skills[1],
            {
                "skill_id": self.skill_2.pk,
                "skill_code": "LDETAIL-A1-02",
                "skill_name": (
                    "Lesson Detail Second Skill"
                ),
                "cefr_level": "A1",
                "is_extension": False,
            },
        )

    def test_get_does_not_create_lesson_progress(
        self,
    ):
        self._login()

        self.assertEqual(
            LessonProgress.objects.filter(
                user=self.user,
                lesson=self.lesson,
            ).count(),
            0,
        )

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertIsNone(
            response.json()["progress"]
        )

        self.assertEqual(
            LessonProgress.objects.filter(
                user=self.user,
                lesson=self.lesson,
            ).count(),
            0,
        )

    def test_existing_progress_is_serialized_without_mutation(
        self,
    ):
        last_accessed_at = (
            timezone.now()
            - timedelta(hours=3)
        )

        progress = LessonProgress.objects.create(
            user=self.user,
            lesson=self.lesson,
            status=(
                LessonProgress.Status.IN_PROGRESS
            ),
            completion_ratio=Decimal("0.3500"),
            last_accessed_at=last_accessed_at,
        )

        progress.refresh_from_db()

        original_last_accessed_at = (
            progress.last_accessed_at
        )

        original_started_at = (
            progress.started_at
        )

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        progress_payload = (
            response.json()["progress"]
        )

        self.assertIsNotNone(
            progress_payload
        )

        self.assertEqual(
            progress_payload[
                "lesson_progress_id"
            ],
            progress.pk,
        )

        self.assertEqual(
            progress_payload["status"],
            "in_progress",
        )

        self.assertEqual(
            progress_payload[
                "completion_ratio"
            ],
            "0.3500",
        )

        self.assertEqual(
            progress_payload["started_at"],
            progress.started_at.isoformat(),
        )

        self.assertIsNone(
            progress_payload["completed_at"]
        )

        self.assertEqual(
            progress_payload[
                "last_accessed_at"
            ],
            progress
            .last_accessed_at
            .isoformat(),
        )

        # GET must not behave like "open Lesson".
        progress.refresh_from_db()

        self.assertEqual(
            progress.last_accessed_at,
            original_last_accessed_at,
        )

        self.assertEqual(
            progress.started_at,
            original_started_at,
        )

        self.assertEqual(
            progress.completion_ratio,
            Decimal("0.3500"),
        )

    def test_inactive_and_nonexistent_lessons_return_404(
        self,
    ):
        self._login()

        inactive_url = reverse(
            "learning_engine:lesson-detail",
            kwargs={
                "lesson_id": (
                    self.inactive_lesson.pk
                ),
            },
        )

        inactive_response = (
            self.client.get(
                inactive_url
            )
        )

        self.assertEqual(
            inactive_response.status_code,
            404,
        )

        missing_url = reverse(
            "learning_engine:lesson-detail",
            kwargs={
                "lesson_id": 999999999,
            },
        )

        missing_response = (
            self.client.get(
                missing_url
            )
        )

        self.assertEqual(
            missing_response.status_code,
            404,
        )

class LessonStartAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="lesson-start-api@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Lesson Start API Topic",
            category="grammar",
            display_order=24000,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="LSTART-A1-01",
            skill_name="Lesson Start API Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.lesson = Lesson.objects.create(
            title="Lesson Start API Lesson",
            lesson_type="grammar",
            content={
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=1,
            is_active=True,
        )

        LessonSkill.objects.create(
            lesson=cls.lesson,
            skill=cls.skill,
        )

        cls.inactive_lesson = Lesson.objects.create(
            title="Inactive Lesson Start API Lesson",
            lesson_type="grammar",
            content={
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=2,
            is_active=False,
        )

        LessonSkill.objects.create(
            lesson=cls.inactive_lesson,
            skill=cls.skill,
        )

    def setUp(self):
        self.url = reverse(
            "learning_engine:lesson-start",
            kwargs={
                "lesson_id": self.lesson.pk,
            },
        )

    def _login(self):
        self.client.force_login(
            self.user
        )

    def test_authentication_is_required(
        self,
    ):
        response = self.client.post(
            self.url
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            LessonProgress.objects.filter(
                user=self.user,
                lesson=self.lesson,
            ).count(),
            0,
        )

    def test_first_open_creates_in_progress_record(
        self,
    ):
        self._login()

        response = self.client.post(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["action"],
            "started",
        )

        self.assertEqual(
            payload["lesson_id"],
            self.lesson.pk,
        )

        progress = LessonProgress.objects.get(
            user=self.user,
            lesson=self.lesson,
        )

        self.assertEqual(
            payload[
                "progress"
            ][
                "lesson_progress_id"
            ],
            progress.pk,
        )

        self.assertEqual(
            progress.status,
            LessonProgress.Status.IN_PROGRESS,
        )

        self.assertEqual(
            progress.completion_ratio,
            Decimal("0.0000"),
        )

        self.assertIsNone(
            progress.completed_at
        )

        self.assertEqual(
            payload[
                "progress"
            ][
                "status"
            ],
            "in_progress",
        )

        self.assertEqual(
            payload[
                "progress"
            ][
                "completion_ratio"
            ],
            "0.0000",
        )

    def test_second_open_resumes_same_record_and_updates_last_accessed_at(
        self,
    ):
        self._login()

        first_response = self.client.post(
            self.url
        )

        self.assertEqual(
            first_response.status_code,
            200,
        )

        progress = LessonProgress.objects.get(
            user=self.user,
            lesson=self.lesson,
        )

        original_progress_id = progress.pk
        original_started_at = progress.started_at

        # Force an old timestamp so the assertion
        # does not depend on clock precision.
        old_last_accessed_at = (
            timezone.now()
            - timedelta(days=2)
        )

        LessonProgress.objects.filter(
            pk=progress.pk,
        ).update(
            last_accessed_at=(
                old_last_accessed_at
            ),
        )

        second_response = self.client.post(
            self.url
        )

        self.assertEqual(
            second_response.status_code,
            200,
        )

        payload = second_response.json()

        self.assertEqual(
            payload["action"],
            "resumed",
        )

        progress.refresh_from_db()

        # Idempotent with respect to the DB row.
        self.assertEqual(
            progress.pk,
            original_progress_id,
        )

        self.assertEqual(
            LessonProgress.objects.filter(
                user=self.user,
                lesson=self.lesson,
            ).count(),
            1,
        )

        # Resume does not restart the Lesson.
        self.assertEqual(
            progress.started_at,
            original_started_at,
        )

        self.assertGreater(
            progress.last_accessed_at,
            old_last_accessed_at,
        )

        self.assertEqual(
            payload[
                "progress"
            ][
                "lesson_progress_id"
            ],
            original_progress_id,
        )

    def test_resume_preserves_existing_completion_ratio(
        self,
    ):
        old_last_accessed_at = (
            timezone.now()
            - timedelta(days=3)
        )

        progress = LessonProgress.objects.create(
            user=self.user,
            lesson=self.lesson,
            status=(
                LessonProgress.Status.IN_PROGRESS
            ),
            completion_ratio=Decimal("0.6250"),
            last_accessed_at=(
                old_last_accessed_at
            ),
        )

        original_started_at = (
            progress.started_at
        )

        self._login()

        response = self.client.post(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.json()["action"],
            "resumed",
        )

        progress.refresh_from_db()

        self.assertEqual(
            progress.completion_ratio,
            Decimal("0.6250"),
        )

        self.assertEqual(
            progress.status,
            LessonProgress.Status.IN_PROGRESS,
        )

        self.assertEqual(
            progress.started_at,
            original_started_at,
        )

        self.assertGreater(
            progress.last_accessed_at,
            old_last_accessed_at,
        )

        self.assertIsNone(
            progress.completed_at
        )

    def test_completed_lesson_is_not_reset(
        self,
    ):
        completed_at = (
            timezone.now()
            - timedelta(days=5)
        )

        old_last_accessed_at = (
            timezone.now()
            - timedelta(days=4)
        )

        progress = LessonProgress.objects.create(
            user=self.user,
            lesson=self.lesson,
            status=(
                LessonProgress.Status.COMPLETED
            ),
            completion_ratio=Decimal("1.0000"),
            completed_at=completed_at,
            last_accessed_at=(
                old_last_accessed_at
            ),
        )

        original_started_at = (
            progress.started_at
        )

        self._login()

        response = self.client.post(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["action"],
            "already_completed",
        )

        progress.refresh_from_db()

        self.assertEqual(
            progress.status,
            LessonProgress.Status.COMPLETED,
        )

        self.assertEqual(
            progress.completion_ratio,
            Decimal("1.0000"),
        )

        self.assertEqual(
            progress.completed_at,
            completed_at,
        )

        self.assertEqual(
            progress.started_at,
            original_started_at,
        )

        self.assertGreater(
            progress.last_accessed_at,
            old_last_accessed_at,
        )

        self.assertEqual(
            payload[
                "progress"
            ][
                "status"
            ],
            "completed",
        )

        self.assertEqual(
            payload[
                "progress"
            ][
                "completion_ratio"
            ],
            "1.0000",
        )

    def test_inactive_and_nonexistent_lessons_return_404_without_progress(
        self,
    ):
        self._login()

        inactive_url = reverse(
            "learning_engine:lesson-start",
            kwargs={
                "lesson_id": (
                    self.inactive_lesson.pk
                ),
            },
        )

        inactive_response = (
            self.client.post(
                inactive_url
            )
        )

        self.assertEqual(
            inactive_response.status_code,
            404,
        )

        missing_url = reverse(
            "learning_engine:lesson-start",
            kwargs={
                "lesson_id": 999999999,
            },
        )

        missing_response = (
            self.client.post(
                missing_url
            )
        )

        self.assertEqual(
            missing_response.status_code,
            404,
        )

        self.assertEqual(
            LessonProgress.objects.filter(
                user=self.user,
            ).count(),
            0,
        )

    def test_lesson_detail_reflects_progress_created_by_start_endpoint(
        self,
    ):
        self._login()

        start_response = (
            self.client.post(
                self.url
            )
        )

        self.assertEqual(
            start_response.status_code,
            200,
        )

        started_payload = (
            start_response.json()
        )

        detail_url = reverse(
            "learning_engine:lesson-detail",
            kwargs={
                "lesson_id": self.lesson.pk,
            },
        )

        detail_response = (
            self.client.get(
                detail_url
            )
        )

        self.assertEqual(
            detail_response.status_code,
            200,
        )

        detail_payload = (
            detail_response.json()
        )

        self.assertIsNotNone(
            detail_payload["progress"]
        )

        self.assertEqual(
            detail_payload[
                "progress"
            ][
                "lesson_progress_id"
            ],
            started_payload[
                "progress"
            ][
                "lesson_progress_id"
            ],
        )

        self.assertEqual(
            detail_payload[
                "progress"
            ][
                "status"
            ],
            "in_progress",
        )

class LessonProgressMutationAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="lesson-progress-api@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Lesson Progress API Topic",
            category="grammar",
            display_order=25000,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="LPROGRESS-A1-01",
            skill_name="Lesson Progress API Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.lesson = Lesson.objects.create(
            title="Lesson Progress API Lesson",
            lesson_type="grammar",
            content={
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=1,
            is_active=True,
        )

        LessonSkill.objects.create(
            lesson=cls.lesson,
            skill=cls.skill,
        )

        cls.inactive_lesson = Lesson.objects.create(
            title="Inactive Lesson Progress API Lesson",
            lesson_type="grammar",
            content={
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=2,
            is_active=False,
        )

        LessonSkill.objects.create(
            lesson=cls.inactive_lesson,
            skill=cls.skill,
        )

    def setUp(self):
        self.progress_url = reverse(
            "learning_engine:lesson-progress-update",
            kwargs={
                "lesson_id": self.lesson.pk,
            },
        )

        self.complete_url = reverse(
            "learning_engine:lesson-complete",
            kwargs={
                "lesson_id": self.lesson.pk,
            },
        )

    def _login(self):
        self.client.force_login(
            self.user
        )

    def _patch_ratio(
        self,
        value,
    ):
        return self.client.patch(
            self.progress_url,
            data=json.dumps(
                {
                    "completion_ratio": value,
                }
            ),
            content_type="application/json",
        )

    def _create_progress(
        self,
        *,
        ratio="0.0000",
        last_accessed_at=None,
    ):
        if last_accessed_at is None:
            last_accessed_at = timezone.now()

        return LessonProgress.objects.create(
            user=self.user,
            lesson=self.lesson,
            status=(
                LessonProgress.Status.IN_PROGRESS
            ),
            completion_ratio=Decimal(
                ratio
            ),
            last_accessed_at=last_accessed_at,
        )

    def test_authentication_is_required_for_progress_mutations(
        self,
    ):
        patch_response = self._patch_ratio(
            "0.5000"
        )

        complete_response = self.client.post(
            self.complete_url
        )

        self.assertEqual(
            patch_response.status_code,
            302,
        )

        self.assertEqual(
            complete_response.status_code,
            302,
        )

        self.assertEqual(
            LessonProgress.objects.filter(
                user=self.user,
                lesson=self.lesson,
            ).count(),
            0,
        )

    def test_progress_update_increases_ratio_and_last_accessed_at(
        self,
    ):
        old_accessed_at = (
            timezone.now()
            - timedelta(days=3)
        )

        progress = self._create_progress(
            ratio="0.2500",
            last_accessed_at=old_accessed_at,
        )

        original_started_at = (
            progress.started_at
        )

        self._login()

        response = self._patch_ratio(
            "0.6250"
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["action"],
            "updated",
        )

        self.assertEqual(
            payload["lesson_id"],
            self.lesson.pk,
        )

        progress.refresh_from_db()

        self.assertEqual(
            progress.completion_ratio,
            Decimal("0.6250"),
        )

        self.assertEqual(
            progress.status,
            LessonProgress.Status.IN_PROGRESS,
        )

        self.assertEqual(
            progress.started_at,
            original_started_at,
        )

        self.assertIsNone(
            progress.completed_at
        )

        self.assertGreater(
            progress.last_accessed_at,
            old_accessed_at,
        )

        self.assertEqual(
            payload["progress"][
                "completion_ratio"
            ],
            "0.6250",
        )

        self.assertEqual(
            payload["progress"]["status"],
            "in_progress",
        )

    def test_progress_cannot_decrease(
        self,
    ):
        old_accessed_at = (
            timezone.now()
            - timedelta(days=2)
        )

        progress = self._create_progress(
            ratio="0.7000",
            last_accessed_at=old_accessed_at,
        )

        self._login()

        response = self._patch_ratio(
            "0.6000"
        )

        self.assertEqual(
            response.status_code,
            409,
        )

        self.assertEqual(
            response.json()["error"],
            "lesson_progress_conflict",
        )

        progress.refresh_from_db()

        self.assertEqual(
            progress.completion_ratio,
            Decimal("0.7000"),
        )

        self.assertEqual(
            progress.last_accessed_at,
            old_accessed_at,
        )

        self.assertEqual(
            progress.status,
            LessonProgress.Status.IN_PROGRESS,
        )

    def test_invalid_completion_ratios_return_400(
        self,
    ):
        self._create_progress(
            ratio="0.2000"
        )

        self._login()

        invalid_values = (
            -0.1,
            1,
            1.0,
            "1.0000",
            "0.12345",
            True,
            None,
            "invalid",
        )

        for value in invalid_values:
            with self.subTest(
                value=value
            ):
                response = self._patch_ratio(
                    value
                )

                self.assertEqual(
                    response.status_code,
                    400,
                )

                self.assertEqual(
                    response.json()["error"],
                    "invalid_completion_ratio",
                )

        progress = LessonProgress.objects.get(
            user=self.user,
            lesson=self.lesson,
        )

        self.assertEqual(
            progress.completion_ratio,
            Decimal("0.2000"),
        )

    def test_progress_update_and_completion_require_started_lesson(
        self,
    ):
        self._login()

        patch_response = self._patch_ratio(
            "0.5000"
        )

        self.assertEqual(
            patch_response.status_code,
            409,
        )

        self.assertEqual(
            patch_response.json()["error"],
            "lesson_progress_conflict",
        )

        complete_response = self.client.post(
            self.complete_url
        )

        self.assertEqual(
            complete_response.status_code,
            409,
        )

        self.assertEqual(
            complete_response.json()["error"],
            "lesson_progress_conflict",
        )

        self.assertFalse(
            LessonProgress.objects.filter(
                user=self.user,
                lesson=self.lesson,
            ).exists()
        )

    def test_complete_lesson_sets_canonical_completed_state(
        self,
    ):
        progress = self._create_progress(
            ratio="0.7500",
            last_accessed_at=(
                timezone.now()
                - timedelta(days=1)
            ),
        )

        original_started_at = (
            progress.started_at
        )

        self._login()

        response = self.client.post(
            self.complete_url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["action"],
            "completed",
        )

        progress.refresh_from_db()

        self.assertEqual(
            progress.status,
            LessonProgress.Status.COMPLETED,
        )

        self.assertEqual(
            progress.completion_ratio,
            Decimal("1.0000"),
        )

        self.assertIsNotNone(
            progress.completed_at
        )

        self.assertEqual(
            progress.last_accessed_at,
            progress.completed_at,
        )

        self.assertEqual(
            progress.started_at,
            original_started_at,
        )

        self.assertEqual(
            payload["progress"]["status"],
            "completed",
        )

        self.assertEqual(
            payload["progress"][
                "completion_ratio"
            ],
            "1.0000",
        )

        self.assertEqual(
            payload["progress"][
                "completed_at"
            ],
            progress.completed_at.isoformat(),
        )

    def test_repeated_completion_is_idempotent(
        self,
    ):
        self._create_progress(
            ratio="0.9000"
        )

        self._login()

        first_response = self.client.post(
            self.complete_url
        )

        self.assertEqual(
            first_response.status_code,
            200,
        )

        progress = LessonProgress.objects.get(
            user=self.user,
            lesson=self.lesson,
        )

        first_completed_at = (
            progress.completed_at
        )

        first_last_accessed_at = (
            progress.last_accessed_at
        )

        second_response = self.client.post(
            self.complete_url
        )

        self.assertEqual(
            second_response.status_code,
            200,
        )

        self.assertEqual(
            second_response.json()["action"],
            "already_completed",
        )

        progress.refresh_from_db()

        self.assertEqual(
            progress.completed_at,
            first_completed_at,
        )

        self.assertEqual(
            progress.last_accessed_at,
            first_last_accessed_at,
        )

        self.assertEqual(
            progress.status,
            LessonProgress.Status.COMPLETED,
        )

        self.assertEqual(
            progress.completion_ratio,
            Decimal("1.0000"),
        )

        self.assertEqual(
            LessonProgress.objects.filter(
                user=self.user,
                lesson=self.lesson,
            ).count(),
            1,
        )

    def test_completed_lesson_cannot_be_patched_backwards(
        self,
    ):
        completed_at = (
            timezone.now()
            - timedelta(days=1)
        )

        progress = LessonProgress.objects.create(
            user=self.user,
            lesson=self.lesson,
            status=(
                LessonProgress.Status.COMPLETED
            ),
            completion_ratio=Decimal("1.0000"),
            completed_at=completed_at,
            last_accessed_at=completed_at,
        )

        self._login()

        response = self._patch_ratio(
            "0.9000"
        )

        self.assertEqual(
            response.status_code,
            409,
        )

        progress.refresh_from_db()

        self.assertEqual(
            progress.status,
            LessonProgress.Status.COMPLETED,
        )

        self.assertEqual(
            progress.completion_ratio,
            Decimal("1.0000"),
        )

        self.assertEqual(
            progress.completed_at,
            completed_at,
        )

    def test_inactive_and_missing_lessons_return_404(
        self,
    ):
        self._login()

        inactive_progress_url = reverse(
            "learning_engine:lesson-progress-update",
            kwargs={
                "lesson_id": (
                    self.inactive_lesson.pk
                ),
            },
        )

        inactive_complete_url = reverse(
            "learning_engine:lesson-complete",
            kwargs={
                "lesson_id": (
                    self.inactive_lesson.pk
                ),
            },
        )

        response = self.client.patch(
            inactive_progress_url,
            data=json.dumps(
                {
                    "completion_ratio": "0.5000",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        response = self.client.post(
            inactive_complete_url
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        missing_id = 999999999

        missing_progress_url = reverse(
            "learning_engine:lesson-progress-update",
            kwargs={
                "lesson_id": missing_id,
            },
        )

        missing_complete_url = reverse(
            "learning_engine:lesson-complete",
            kwargs={
                "lesson_id": missing_id,
            },
        )

        response = self.client.patch(
            missing_progress_url,
            data=json.dumps(
                {
                    "completion_ratio": "0.5000",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        response = self.client.post(
            missing_complete_url
        )

        self.assertEqual(
            response.status_code,
            404,
        )

class ActiveAssessmentDetailAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="assessment-detail@example.com",
            password="test-password-123",
        )

        cls.other_user = User.objects.create_user(
            email="assessment-detail-other@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Assessment Detail API Topic",
            category="grammar",
            display_order=26000,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="ASSESS-DETAIL-A1",
            skill_name="Assessment Detail API Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.question_1 = Question.objects.create(
            source_id="ASSESS-DETAIL-Q001",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text="Choose the first answer.",
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.2500"),

            # Deliberately populated.
            #
            # API must NOT expose these.
            correct_answer="A",
            accepted_answers=[
                "A",
            ],
            explanation="Secret explanation.",

            audio_url=None,
            is_active=True,
        )

        cls.question_2 = Question.objects.create(
            source_id="ASSESS-DETAIL-Q002",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text="Choose the second answer.",
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.5000"),

            correct_answer="B",
            accepted_answers=[
                "B",
            ],
            explanation="Another secret explanation.",

            audio_url=(
                "https://example.test/q2.mp3"
            ),
            is_active=True,
        )

        for question in (
            cls.question_1,
            cls.question_2,
        ):
            QuestionSkill.objects.create(
                question=question,
                skill=cls.skill,
                is_primary=True,
                weight=Decimal("1.00000"),
            )

        # --------------------------------------------------
        # Question 1 options
        #
        # Created out of display order intentionally.
        # --------------------------------------------------

        QuestionOption.objects.create(
            question=cls.question_1,
            option_key="B",
            option_text="Wrong B",
            misconception_code="SECRET-B",
            is_correct=False,
            display_order=2,
        )

        QuestionOption.objects.create(
            question=cls.question_1,
            option_key="A",
            option_text="Correct A",
            misconception_code=None,
            is_correct=True,
            display_order=1,
        )

        QuestionOption.objects.create(
            question=cls.question_1,
            option_key="C",
            option_text="Wrong C",
            misconception_code="SECRET-C",
            is_correct=False,
            display_order=3,
        )

        # --------------------------------------------------
        # Question 2 options
        # --------------------------------------------------

        QuestionOption.objects.create(
            question=cls.question_2,
            option_key="B",
            option_text="Correct B",
            misconception_code=None,
            is_correct=True,
            display_order=2,
        )

        QuestionOption.objects.create(
            question=cls.question_2,
            option_key="A",
            option_text="Wrong A",
            misconception_code="SECRET-A",
            is_correct=False,
            display_order=1,
        )

        QuestionOption.objects.create(
            question=cls.question_2,
            option_key="C",
            option_text="Wrong C",
            misconception_code="SECRET-C2",
            is_correct=False,
            display_order=3,
        )

    def setUp(self):
        started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        self.session = AssessmentSession.objects.get(
            pk=started.session_id
        )

        added_1 = add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.question_1.pk,
        )

        added_2 = add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.question_2.pk,
        )

        self.session_question_1 = (
            SessionQuestion.objects.get(
                pk=added_1.session_question_id
            )
        )

        self.session_question_2 = (
            SessionQuestion.objects.get(
                pk=added_2.session_question_id
            )
        )

        self.url = reverse(
            "learning_engine:assessment-detail",
            kwargs={
                "session_id": self.session.pk,
            },
        )

    def _login(self):
        self.client.force_login(
            self.user
        )

    def _collect_keys(
        self,
        value,
    ):
        """
        Recursively collect every dictionary key
        appearing anywhere in a JSON structure.
        """

        keys = set()

        if isinstance(
            value,
            dict,
        ):
            for key, nested_value in value.items():
                keys.add(
                    key
                )

                keys.update(
                    self._collect_keys(
                        nested_value
                    )
                )

        elif isinstance(
            value,
            list,
        ):
            for item in value:
                keys.update(
                    self._collect_keys(
                        item
                    )
                )

        return keys

    def test_authentication_is_required(
        self,
    ):
        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            302,
        )

    def test_active_assessment_is_serialized_in_sequence_order(
        self,
    ):
        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["session_id"],
            self.session.pk,
        )

        self.assertEqual(
            payload["mode"],
            "adaptive",
        )

        self.assertEqual(
            payload["status"],
            "active",
        )

        self.assertEqual(
            payload["question_count"],
            2,
        )

        self.assertEqual(
            [
                row["sequence_number"]
                for row in payload["questions"]
            ],
            [
                1,
                2,
            ],
        )

        self.assertEqual(
            [
                row["question"]["source_id"]
                for row in payload["questions"]
            ],
            [
                "ASSESS-DETAIL-Q001",
                "ASSESS-DETAIL-Q002",
            ],
        )

    def test_question_options_are_safe_and_deterministically_ordered(
        self,
    ):
        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        first_question = (
            response.json()[
                "questions"
            ][0][
                "question"
            ]
        )

        self.assertEqual(
            [
                option["option_key"]
                for option
                in first_question["options"]
            ],
            [
                "A",
                "B",
                "C",
            ],
        )

        self.assertEqual(
            first_question["options"][0],
            {
                "option_key": "A",
                "option_text": "Correct A",
                "display_order": 1,
            },
        )

    def test_latest_answer_revision_is_returned_without_correctness(
        self,
    ):
        self._login()

        first_attempt = save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question_1.pk
            ),
            selected_option_key="B",
            response_time_ms=1200,
        )

        second_attempt = save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question_1.pk
            ),
            selected_option_key="A",
            response_time_ms=1800,
        )

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload[
                "answered_question_count"
            ],
            1,
        )

        answer = (
            payload[
                "questions"
            ][0][
                "current_answer"
            ]
        )

        self.assertEqual(
            answer["attempt_id"],
            second_attempt.attempt_id,
        )

        self.assertEqual(
            answer["attempt_number"],
            2,
        )

        self.assertEqual(
            answer["selected_option_key"],
            "A",
        )

        self.assertEqual(
            answer["response_time_ms"],
            1800,
        )

        self.assertNotEqual(
            answer["attempt_id"],
            first_attempt.attempt_id,
        )

        # Question 2 remains unanswered.
        self.assertIsNone(
            payload[
                "questions"
            ][1][
                "current_answer"
            ]
        )

    def test_viewed_and_answered_counts_are_serialized(
        self,
    ):
        mark_session_question_viewed(
            user=self.user,
            session_question_id=(
                self.session_question_1.pk
            ),
        )

        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question_2.pk
            ),
            selected_option_key="A",
            response_time_ms=1000,
        )

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        # Saving an answer also marks that
        # SessionQuestion as viewed.
        self.assertEqual(
            payload["viewed_question_count"],
            2,
        )

        self.assertEqual(
            payload["answered_question_count"],
            1,
        )

        self.assertIsNotNone(
            payload[
                "questions"
            ][0][
                "first_viewed_at"
            ]
        )

        self.assertIsNotNone(
            payload[
                "questions"
            ][1][
                "first_viewed_at"
            ]
        )

    def test_active_assessment_response_never_leaks_sensitive_fields(
        self,
    ):
        # Create real internal correctness data.
        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question_1.pk
            ),
            selected_option_key="A",
            response_time_ms=500,
        )

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        all_keys = self._collect_keys(
            payload
        )

        forbidden_keys = {
            "is_correct",
            "correct_answer",
            "accepted_answers",
            "explanation",
            "misconception_code",
            "difficulty_score",
            "difficulty_score_snapshot",
            "skill_snapshot",
        }

        self.assertTrue(
            forbidden_keys.isdisjoint(
                all_keys
            ),
            msg=(
                "Sensitive assessment fields "
                "leaked into ACTIVE assessment "
                f"response: "
                f"{forbidden_keys & all_keys}"
            ),
        )

        # Confirm DB really contains correctness.
        #
        # This prevents the test from passing only
        # because there was no sensitive data.
        attempt = (
            QuestionAttempt.objects.get(
                session_question=(
                    self.session_question_1
                ),
                attempt_number=1,
            )
        )

        self.assertTrue(
            attempt.is_correct
        )

        self.assertEqual(
            self.question_1.correct_answer,
            "A",
        )

        self.assertEqual(
            self.question_1.explanation,
            "Secret explanation.",
        )

    def test_session_must_be_active_and_owned_by_authenticated_user(
        self,
    ):
        self._login()

        # --------------------------------------------------
        # Another learner's ACTIVE session
        # --------------------------------------------------

        other_session = (
            AssessmentSession.objects.create(
                user=self.other_user,
                mode=(
                    AssessmentSession.Mode.ADAPTIVE
                ),
                status=(
                    AssessmentSession.Status.ACTIVE
                ),
            )
        )

        other_url = reverse(
            "learning_engine:assessment-detail",
            kwargs={
                "session_id": other_session.pk,
            },
        )

        response = self.client.get(
            other_url
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        # --------------------------------------------------
        # Own COMPLETED session
        # --------------------------------------------------

        completed_session = (
            AssessmentSession.objects.create(
                user=self.user,
                mode=(
                    AssessmentSession.Mode.ADAPTIVE
                ),
                status=(
                    AssessmentSession.Status.COMPLETED
                ),
                ended_at=timezone.now(),
            )
        )

        completed_url = reverse(
            "learning_engine:assessment-detail",
            kwargs={
                "session_id": (
                    completed_session.pk
                ),
            },
        )

        response = self.client.get(
            completed_url
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        # --------------------------------------------------
        # Own ABANDONED session
        # --------------------------------------------------

        abandoned_session = (
            AssessmentSession.objects.create(
                user=self.user,
                mode=(
                    AssessmentSession.Mode.DIAGNOSTIC
                ),
                status=(
                    AssessmentSession.Status.ABANDONED
                ),
                ended_at=timezone.now(),
            )
        )

        abandoned_url = reverse(
            "learning_engine:assessment-detail",
            kwargs={
                "session_id": (
                    abandoned_session.pk
                ),
            },
        )

        response = self.client.get(
            abandoned_url
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_get_assessment_is_read_only(
        self,
    ):
        mark_session_question_viewed(
            user=self.user,
            session_question_id=(
                self.session_question_1.pk
            ),
        )

        self.session_question_1.refresh_from_db()

        original_first_viewed_at = (
            self.session_question_1
            .first_viewed_at
        )

        original_active_time = (
            self.session_question_1
            .active_response_time_ms
        )

        original_attempt_count = (
            QuestionAttempt.objects
            .filter(
                session_question__session=(
                    self.session
                ),
            )
            .count()
        )

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.session_question_1.refresh_from_db()

        self.assertEqual(
            self.session_question_1
            .first_viewed_at,
            original_first_viewed_at,
        )

        self.assertEqual(
            self.session_question_1
            .active_response_time_ms,
            original_active_time,
        )

        self.assertEqual(
            QuestionAttempt.objects
            .filter(
                session_question__session=(
                    self.session
                ),
            )
            .count(),
            original_attempt_count,
        )

class AssessmentQuestionActivityAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="assessment-activity@example.com",
            password="test-password-123",
        )

        cls.other_user = User.objects.create_user(
            email="assessment-activity-other@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Assessment Activity API Topic",
            category="grammar",
            display_order=27000,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="ASSESS-ACT-A1",
            skill_name="Assessment Activity API Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.question = Question.objects.create(
            source_id="ASSESS-ACT-Q001",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text=(
                "Assessment activity test question."
            ),
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.2500"),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=cls.question,
            skill=cls.skill,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

    def setUp(self):
        started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        self.session = AssessmentSession.objects.get(
            pk=started.session_id
        )

        added = add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.question.pk,
        )

        self.session_question = (
            SessionQuestion.objects.get(
                pk=added.session_question_id
            )
        )

        self.viewed_url = reverse(
            "learning_engine:"
            "assessment-question-viewed",
            kwargs={
                "session_id": self.session.pk,
                "session_question_id": (
                    self.session_question.pk
                ),
            },
        )

        self.active_time_url = reverse(
            "learning_engine:"
            "assessment-question-active-time",
            kwargs={
                "session_id": self.session.pk,
                "session_question_id": (
                    self.session_question.pk
                ),
            },
        )

    def _login(self):
        self.client.force_login(
            self.user
        )

    def _post_active_time(
        self,
        delta_ms,
    ):
        return self.client.post(
            self.active_time_url,
            data=json.dumps(
                {
                    "delta_ms": delta_ms,
                }
            ),
            content_type="application/json",
        )

    def test_authentication_is_required_for_both_endpoints(
        self,
    ):
        viewed_response = self.client.post(
            self.viewed_url
        )

        active_time_response = (
            self._post_active_time(
                1000
            )
        )

        self.assertEqual(
            viewed_response.status_code,
            302,
        )

        self.assertEqual(
            active_time_response.status_code,
            302,
        )

        self.session_question.refresh_from_db()

        self.assertIsNone(
            self.session_question.first_viewed_at
        )

        self.assertEqual(
            self.session_question
            .active_response_time_ms,
            0,
        )

    def test_viewed_sets_first_viewed_at(
        self,
    ):
        self._login()

        self.assertIsNone(
            self.session_question.first_viewed_at
        )

        response = self.client.post(
            self.viewed_url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.session_question.refresh_from_db()

        self.assertIsNotNone(
            self.session_question.first_viewed_at
        )

        self.assertEqual(
            payload["session_id"],
            self.session.pk,
        )

        self.assertEqual(
            payload["session_question_id"],
            self.session_question.pk,
        )

        self.assertEqual(
            payload["first_viewed_at"],
            self.session_question
            .first_viewed_at
            .isoformat(),
        )

    def test_viewed_is_idempotent(
        self,
    ):
        self._login()

        first_response = self.client.post(
            self.viewed_url
        )

        self.assertEqual(
            first_response.status_code,
            200,
        )

        self.session_question.refresh_from_db()

        original_first_viewed_at = (
            self.session_question.first_viewed_at
        )

        second_response = self.client.post(
            self.viewed_url
        )

        self.assertEqual(
            second_response.status_code,
            200,
        )

        self.session_question.refresh_from_db()

        self.assertEqual(
            self.session_question.first_viewed_at,
            original_first_viewed_at,
        )

        self.assertEqual(
            second_response.json()[
                "first_viewed_at"
            ],
            original_first_viewed_at.isoformat(),
        )

    def test_active_time_is_cumulative(
        self,
    ):
        self._login()

        first_response = self._post_active_time(
            2000
        )

        self.assertEqual(
            first_response.status_code,
            200,
        )

        self.assertEqual(
            first_response.json()[
                "active_response_time_ms"
            ],
            2000,
        )

        second_response = self._post_active_time(
            3500
        )

        self.assertEqual(
            second_response.status_code,
            200,
        )

        self.assertEqual(
            second_response.json()[
                "active_response_time_ms"
            ],
            5500,
        )

        third_response = self._post_active_time(
            500
        )

        self.assertEqual(
            third_response.status_code,
            200,
        )

        self.assertEqual(
            third_response.json()[
                "active_response_time_ms"
            ],
            6000,
        )

        self.session_question.refresh_from_db()

        self.assertEqual(
            self.session_question
            .active_response_time_ms,
            6000,
        )

    def test_active_time_automatically_marks_question_viewed(
        self,
    ):
        self.assertIsNone(
            self.session_question.first_viewed_at
        )

        self._login()

        response = self._post_active_time(
            1500
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.session_question.refresh_from_db()

        self.assertIsNotNone(
            self.session_question.first_viewed_at
        )

        self.assertEqual(
            self.session_question
            .active_response_time_ms,
            1500,
        )

        self.assertEqual(
            response.json()[
                "first_viewed_at"
            ],
            self.session_question
            .first_viewed_at
            .isoformat(),
        )

    def test_invalid_active_time_payloads_return_400_without_mutation(
        self,
    ):
        self._login()

        invalid_values = (
            -1,
            1.5,
            "1000",
            True,
            None,
        )

        for value in invalid_values:
            with self.subTest(
                value=value
            ):
                response = (
                    self._post_active_time(
                        value
                    )
                )

                self.assertEqual(
                    response.status_code,
                    400,
                )

                self.assertEqual(
                    response.json()["error"],
                    "invalid_delta_ms",
                )

        invalid_json_response = (
            self.client.post(
                self.active_time_url,
                data="{broken-json",
                content_type=(
                    "application/json"
                ),
            )
        )

        self.assertEqual(
            invalid_json_response.status_code,
            400,
        )

        self.assertEqual(
            invalid_json_response.json()[
                "error"
            ],
            "invalid_json",
        )

        extra_field_response = (
            self.client.post(
                self.active_time_url,
                data=json.dumps(
                    {
                        "delta_ms": 1000,
                        "extra": True,
                    }
                ),
                content_type="application/json",
            )
        )

        self.assertEqual(
            extra_field_response.status_code,
            400,
        )

        self.assertEqual(
            extra_field_response.json()[
                "error"
            ],
            "invalid_fields",
        )

        self.session_question.refresh_from_db()

        self.assertIsNone(
            self.session_question.first_viewed_at
        )

        self.assertEqual(
            self.session_question
            .active_response_time_ms,
            0,
        )

    def test_wrong_session_and_other_user_resources_return_404(
        self,
    ):
        self._login()

        # --------------------------------------------------
        # Correct SessionQuestion but wrong session in URL.
        # --------------------------------------------------

        second_started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        wrong_session_viewed_url = reverse(
            "learning_engine:"
            "assessment-question-viewed",
            kwargs={
                "session_id": (
                    second_started.session_id
                ),
                "session_question_id": (
                    self.session_question.pk
                ),
            },
        )

        wrong_session_time_url = reverse(
            "learning_engine:"
            "assessment-question-active-time",
            kwargs={
                "session_id": (
                    second_started.session_id
                ),
                "session_question_id": (
                    self.session_question.pk
                ),
            },
        )

        response = self.client.post(
            wrong_session_viewed_url
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        response = self.client.post(
            wrong_session_time_url,
            data=json.dumps(
                {
                    "delta_ms": 1000,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        # --------------------------------------------------
        # Another learner's SessionQuestion.
        # --------------------------------------------------

        other_started = start_session(
            user=self.other_user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        other_added = add_question_to_session(
            user=self.other_user,
            session_id=other_started.session_id,
            question_id=self.question.pk,
        )

        other_viewed_url = reverse(
            "learning_engine:"
            "assessment-question-viewed",
            kwargs={
                "session_id": (
                    other_started.session_id
                ),
                "session_question_id": (
                    other_added
                    .session_question_id
                ),
            },
        )

        other_time_url = reverse(
            "learning_engine:"
            "assessment-question-active-time",
            kwargs={
                "session_id": (
                    other_started.session_id
                ),
                "session_question_id": (
                    other_added
                    .session_question_id
                ),
            },
        )

        response = self.client.post(
            other_viewed_url
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        response = self.client.post(
            other_time_url,
            data=json.dumps(
                {
                    "delta_ms": 1000,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_non_active_assessment_returns_409(
        self,
    ):
        self._login()

        for status in (
            AssessmentSession.Status.COMPLETED,
            AssessmentSession.Status.ABANDONED,
        ):
            with self.subTest(
                status=status
            ):
                started = start_session(
                    user=self.user,
                    mode=(
                        AssessmentSession.Mode
                        .DIAGNOSTIC
                    ),
                )

                added = add_question_to_session(
                    user=self.user,
                    session_id=started.session_id,
                    question_id=self.question.pk,
                )

                session = (
                    AssessmentSession.objects.get(
                        pk=started.session_id
                    )
                )

                session.status = status
                session.ended_at = timezone.now()

                session.save(
                    update_fields=[
                        "status",
                        "ended_at",
                    ]
                )

                viewed_url = reverse(
                    "learning_engine:"
                    "assessment-question-viewed",
                    kwargs={
                        "session_id": session.pk,
                        "session_question_id": (
                            added
                            .session_question_id
                        ),
                    },
                )

                active_time_url = reverse(
                    "learning_engine:"
                    "assessment-question-active-time",
                    kwargs={
                        "session_id": session.pk,
                        "session_question_id": (
                            added
                            .session_question_id
                        ),
                    },
                )

                response = self.client.post(
                    viewed_url
                )

                self.assertEqual(
                    response.status_code,
                    409,
                )

                self.assertEqual(
                    response.json()["error"],
                    "assessment_state_conflict",
                )

                response = self.client.post(
                    active_time_url,
                    data=json.dumps(
                        {
                            "delta_ms": 1000,
                        }
                    ),
                    content_type=(
                        "application/json"
                    ),
                )

                self.assertEqual(
                    response.status_code,
                    409,
                )

                self.assertEqual(
                    response.json()["error"],
                    "assessment_state_conflict",
                )

    def test_activity_endpoints_never_create_question_attempts(
        self,
    ):
        self._login()

        self.assertEqual(
            QuestionAttempt.objects.filter(
                session_question=(
                    self.session_question
                ),
            ).count(),
            0,
        )

        viewed_response = self.client.post(
            self.viewed_url
        )

        self.assertEqual(
            viewed_response.status_code,
            200,
        )

        time_response = self._post_active_time(
            4200
        )

        self.assertEqual(
            time_response.status_code,
            200,
        )

        self.assertEqual(
            QuestionAttempt.objects.filter(
                session_question=(
                    self.session_question
                ),
            ).count(),
            0,
        )

class AssessmentQuestionAnswerAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="assessment-answer@example.com",
            password="test-password-123",
        )

        cls.other_user = User.objects.create_user(
            email="assessment-answer-other@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Assessment Answer API Topic",
            category="grammar",
            display_order=28000,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="ASSESS-ANSWER-A1",
            skill_name="Assessment Answer API Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.question = Question.objects.create(
            source_id="ASSESS-ANSWER-Q001",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text="Choose the correct answer.",
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.5000"),

            # Deliberately populate sensitive data.
            correct_answer="B",
            accepted_answers=[
                "B",
            ],
            explanation="Secret answer explanation.",

            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=cls.question,
            skill=cls.skill,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

        QuestionOption.objects.create(
            question=cls.question,
            option_key="A",
            option_text="Wrong answer",
            misconception_code="SECRET-WRONG",
            is_correct=False,
            display_order=1,
        )

        QuestionOption.objects.create(
            question=cls.question,
            option_key="B",
            option_text="Correct answer",
            misconception_code=None,
            is_correct=True,
            display_order=2,
        )

        QuestionOption.objects.create(
            question=cls.question,
            option_key="C",
            option_text="Another wrong answer",
            misconception_code="SECRET-WRONG-2",
            is_correct=False,
            display_order=3,
        )

    def setUp(self):
        started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        self.session = AssessmentSession.objects.get(
            pk=started.session_id
        )

        added = add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.question.pk,
        )

        self.session_question = (
            SessionQuestion.objects.get(
                pk=added.session_question_id
            )
        )

        self.url = reverse(
            "learning_engine:"
            "assessment-question-answer",
            kwargs={
                "session_id": self.session.pk,
                "session_question_id": (
                    self.session_question.pk
                ),
            },
        )

        self.detail_url = reverse(
            "learning_engine:"
            "assessment-detail",
            kwargs={
                "session_id": self.session.pk,
            },
        )

    def _login(self):
        self.client.force_login(
            self.user
        )

    def _post_answer(
        self,
        *,
        option_key,
        response_time_ms=None,
        include_response_time=True,
    ):
        payload = {
            "selected_option_key": (
                option_key
            ),
        }

        if include_response_time:
            payload[
                "response_time_ms"
            ] = response_time_ms

        return self.client.post(
            self.url,
            data=json.dumps(
                payload
            ),
            content_type="application/json",
        )

    def _collect_keys(
        self,
        value,
    ):
        keys = set()

        if isinstance(
            value,
            dict,
        ):
            for key, nested_value in value.items():
                keys.add(
                    key
                )

                keys.update(
                    self._collect_keys(
                        nested_value
                    )
                )

        elif isinstance(
            value,
            list,
        ):
            for item in value:
                keys.update(
                    self._collect_keys(
                        item
                    )
                )

        return keys

    def test_authentication_is_required(
        self,
    ):
        response = self._post_answer(
            option_key="A",
            response_time_ms=1000,
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            QuestionAttempt.objects.filter(
                session_question=(
                    self.session_question
                ),
            ).count(),
            0,
        )

    def test_first_answer_creates_attempt_one_without_exposing_correctness(
        self,
    ):
        self._login()

        response = self._post_answer(
            option_key="A",
            response_time_ms=1500,
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["session_id"],
            self.session.pk,
        )

        self.assertEqual(
            payload["session_question_id"],
            self.session_question.pk,
        )

        self.assertEqual(
            payload["question_id"],
            self.question.pk,
        )

        self.assertEqual(
            payload["source_id"],
            self.question.source_id,
        )

        self.assertEqual(
            payload["sequence_number"],
            1,
        )

        self.assertEqual(
            payload["attempt"][
                "attempt_number"
            ],
            1,
        )

        self.assertEqual(
            payload["attempt"][
                "selected_option_key"
            ],
            "A",
        )

        attempt = (
            QuestionAttempt.objects.get(
                session_question=(
                    self.session_question
                ),
                attempt_number=1,
            )
        )

        # Server knows that A is wrong.
        self.assertFalse(
            attempt.is_correct
        )

        self.assertEqual(
            attempt.response_time_ms,
            1500,
        )

        # But learner response must not know.
        forbidden_keys = {
            "is_correct",
            "correct_answer",
            "accepted_answers",
            "explanation",
            "misconception_code",
        }

        self.assertTrue(
            forbidden_keys.isdisjoint(
                self._collect_keys(
                    payload
                )
            )
        )

    def test_answer_revision_creates_new_attempt_and_get_returns_latest(
        self,
    ):
        self._login()

        first_response = self._post_answer(
            option_key="A",
            response_time_ms=1500,
        )

        second_response = self._post_answer(
            option_key="B",
            response_time_ms=900,
        )

        self.assertEqual(
            first_response.status_code,
            200,
        )

        self.assertEqual(
            second_response.status_code,
            200,
        )

        self.assertEqual(
            first_response.json()[
                "attempt"
            ][
                "attempt_number"
            ],
            1,
        )

        self.assertEqual(
            second_response.json()[
                "attempt"
            ][
                "attempt_number"
            ],
            2,
        )

        attempts = list(
            QuestionAttempt.objects
            .filter(
                session_question=(
                    self.session_question
                ),
            )
            .order_by(
                "attempt_number"
            )
        )

        self.assertEqual(
            len(attempts),
            2,
        )

        self.assertEqual(
            attempts[0].selected_option_key,
            "A",
        )

        self.assertFalse(
            attempts[0].is_correct
        )

        self.assertEqual(
            attempts[1].selected_option_key,
            "B",
        )

        self.assertTrue(
            attempts[1].is_correct
        )

        # Old attempt was preserved rather than
        # overwritten.
        self.assertNotEqual(
            attempts[0].pk,
            attempts[1].pk,
        )

        # 9E.4A must now expose revision 2 as
        # current_answer.
        detail_response = self.client.get(
            self.detail_url
        )

        self.assertEqual(
            detail_response.status_code,
            200,
        )

        current_answer = (
            detail_response.json()[
                "questions"
            ][0][
                "current_answer"
            ]
        )

        self.assertEqual(
            current_answer[
                "attempt_number"
            ],
            2,
        )

        self.assertEqual(
            current_answer[
                "selected_option_key"
            ],
            "B",
        )

    def test_correct_and_wrong_answers_have_same_safe_response_shape(
        self,
    ):
        self._login()

        wrong_response = self._post_answer(
            option_key="A",
            response_time_ms=800,
        )

        correct_response = self._post_answer(
            option_key="B",
            response_time_ms=900,
        )

        self.assertEqual(
            wrong_response.status_code,
            200,
        )

        self.assertEqual(
            correct_response.status_code,
            200,
        )

        wrong_payload = (
            wrong_response.json()
        )

        correct_payload = (
            correct_response.json()
        )

        self.assertEqual(
            set(wrong_payload.keys()),
            set(correct_payload.keys()),
        )

        self.assertEqual(
            set(
                wrong_payload[
                    "attempt"
                ].keys()
            ),
            set(
                correct_payload[
                    "attempt"
                ].keys()
            ),
        )

        forbidden_keys = {
            "is_correct",
            "correct_answer",
            "accepted_answers",
            "explanation",
            "misconception_code",
        }

        self.assertTrue(
            forbidden_keys.isdisjoint(
                self._collect_keys(
                    wrong_payload
                )
            )
        )

        self.assertTrue(
            forbidden_keys.isdisjoint(
                self._collect_keys(
                    correct_payload
                )
            )
        )

        attempts = list(
            QuestionAttempt.objects
            .filter(
                session_question=(
                    self.session_question
                ),
            )
            .order_by(
                "attempt_number"
            )
        )

        # Verify the DB actually distinguishes the
        # two answers internally.
        self.assertFalse(
            attempts[0].is_correct
        )

        self.assertTrue(
            attempts[1].is_correct
        )

    def test_saving_answer_marks_question_viewed(
        self,
    ):
        self.assertIsNone(
            self.session_question.first_viewed_at
        )

        self._login()

        response = self._post_answer(
            option_key="B",
            response_time_ms=1000,
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.session_question.refresh_from_db()

        self.assertIsNotNone(
            self.session_question.first_viewed_at
        )

    def test_response_time_is_optional_but_invalid_values_return_400(
        self,
    ):
        self._login()

        # Omitted entirely.
        response = self._post_answer(
            option_key="A",
            include_response_time=False,
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        first_attempt = (
            QuestionAttempt.objects.get(
                session_question=(
                    self.session_question
                ),
                attempt_number=1,
            )
        )

        self.assertIsNone(
            first_attempt.response_time_ms
        )

        # Explicit null is also valid.
        response = self._post_answer(
            option_key="B",
            response_time_ms=None,
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        invalid_values = (
            -1,
            1.5,
            "1000",
            True,
        )

        for value in invalid_values:
            with self.subTest(
                value=value
            ):
                before_count = (
                    QuestionAttempt.objects
                    .filter(
                        session_question=(
                            self.session_question
                        ),
                    )
                    .count()
                )

                response = self._post_answer(
                    option_key="A",
                    response_time_ms=value,
                )

                self.assertEqual(
                    response.status_code,
                    400,
                )

                self.assertEqual(
                    response.json()["error"],
                    "invalid_response_time_ms",
                )

                self.assertEqual(
                    QuestionAttempt.objects
                    .filter(
                        session_question=(
                            self.session_question
                        ),
                    )
                    .count(),
                    before_count,
                )

    def test_invalid_payloads_do_not_create_attempt(
        self,
    ):
        self._login()

        invalid_requests = (
            (
                {},
                "missing_field",
            ),
            (
                {
                    "selected_option_key": "",
                },
                "invalid_selected_option_key",
            ),
            (
                {
                    "selected_option_key": 1,
                },
                "invalid_selected_option_key",
            ),
            (
                {
                    "selected_option_key": "A",
                    "unexpected": True,
                },
                "unexpected_fields",
            ),
        )

        for (
            payload,
            expected_error,
        ) in invalid_requests:
            with self.subTest(
                payload=payload
            ):
                response = self.client.post(
                    self.url,
                    data=json.dumps(
                        payload
                    ),
                    content_type=(
                        "application/json"
                    ),
                )

                self.assertEqual(
                    response.status_code,
                    400,
                )

                self.assertEqual(
                    response.json()["error"],
                    expected_error,
                )

        response = self.client.post(
            self.url,
            data="{broken-json",
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            400,
        )

        self.assertEqual(
            response.json()["error"],
            "invalid_json",
        )

        self.assertEqual(
            QuestionAttempt.objects.filter(
                session_question=(
                    self.session_question
                ),
            ).count(),
            0,
        )

    def test_unknown_option_returns_409_without_creating_attempt(
        self,
    ):
        self._login()

        response = self._post_answer(
            option_key="Z",
            response_time_ms=1000,
        )

        self.assertEqual(
            response.status_code,
            409,
        )

        self.assertEqual(
            response.json()["error"],
            "answer_submission_conflict",
        )

        self.assertEqual(
            QuestionAttempt.objects.filter(
                session_question=(
                    self.session_question
                ),
            ).count(),
            0,
        )

    def test_wrong_session_other_user_and_non_active_session_are_rejected(
        self,
    ):
        self._login()

        # ==================================================
        # Correct SessionQuestion under wrong session URL
        # -> 404
        # ==================================================

        another_started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        wrong_session_url = reverse(
            "learning_engine:"
            "assessment-question-answer",
            kwargs={
                "session_id": (
                    another_started.session_id
                ),
                "session_question_id": (
                    self.session_question.pk
                ),
            },
        )

        response = self.client.post(
            wrong_session_url,
            data=json.dumps(
                {
                    "selected_option_key": "A",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        # ==================================================
        # Another user's SessionQuestion
        # -> 404
        # ==================================================

        other_started = start_session(
            user=self.other_user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        other_added = add_question_to_session(
            user=self.other_user,
            session_id=other_started.session_id,
            question_id=self.question.pk,
        )

        other_url = reverse(
            "learning_engine:"
            "assessment-question-answer",
            kwargs={
                "session_id": (
                    other_started.session_id
                ),
                "session_question_id": (
                    other_added
                    .session_question_id
                ),
            },
        )

        response = self.client.post(
            other_url,
            data=json.dumps(
                {
                    "selected_option_key": "A",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        # ==================================================
        # Correct resource, but session no longer ACTIVE
        # -> 409
        # ==================================================

        for status in (
            AssessmentSession.Status.COMPLETED,
            AssessmentSession.Status.ABANDONED,
        ):
            with self.subTest(
                status=status
            ):
                started = start_session(
                    user=self.user,
                    mode=(
                        AssessmentSession.Mode
                        .DIAGNOSTIC
                    ),
                )

                added = add_question_to_session(
                    user=self.user,
                    session_id=started.session_id,
                    question_id=self.question.pk,
                )

                session = (
                    AssessmentSession.objects.get(
                        pk=started.session_id
                    )
                )

                session.status = status
                session.ended_at = timezone.now()

                session.save(
                    update_fields=[
                        "status",
                        "ended_at",
                    ]
                )

                answer_url = reverse(
                    "learning_engine:"
                    "assessment-question-answer",
                    kwargs={
                        "session_id": session.pk,
                        "session_question_id": (
                            added
                            .session_question_id
                        ),
                    },
                )

                before_count = (
                    QuestionAttempt.objects
                    .filter(
                        session_question_id=(
                            added
                            .session_question_id
                        ),
                    )
                    .count()
                )

                response = self.client.post(
                    answer_url,
                    data=json.dumps(
                        {
                            "selected_option_key":
                                "A",
                        }
                    ),
                    content_type=(
                        "application/json"
                    ),
                )

                self.assertEqual(
                    response.status_code,
                    409,
                )

                self.assertEqual(
                    response.json()["error"],
                    "answer_submission_conflict",
                )

                self.assertEqual(
                    QuestionAttempt.objects
                    .filter(
                        session_question_id=(
                            added
                            .session_question_id
                        ),
                    )
                    .count(),
                    before_count,
                )

class AssessmentFinishAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="assessment-finish@example.com",
            password="test-password-123",
            prior_russian_experience=False,
        )

        cls.other_user = User.objects.create_user(
            email="assessment-finish-other@example.com",
            password="test-password-123",
            prior_russian_experience=False,
        )

        cls.topic = Topic.objects.create(
            topic_name="Assessment Finish API Topic",
            category="grammar",
            display_order=29000,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="ASSESS-FINISH-A1",
            skill_name="Assessment Finish API Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.question = Question.objects.create(
            source_id="ASSESS-FINISH-Q001",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text=(
                "Assessment finish test question."
            ),
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.2500"),
            correct_answer=None,
            accepted_answers=None,
            explanation=None,
            audio_url=None,
            is_active=True,
        )

        QuestionSkill.objects.create(
            question=cls.question,
            skill=cls.skill,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

    def setUp(self):
        started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        self.session = AssessmentSession.objects.get(
            pk=started.session_id
        )

        added = add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.question.pk,
        )

        self.session_question = (
            SessionQuestion.objects.get(
                pk=added.session_question_id
            )
        )

        self.complete_url = reverse(
            "learning_engine:assessment-complete",
            kwargs={
                "session_id": self.session.pk,
            },
        )

        self.abandon_url = reverse(
            "learning_engine:assessment-abandon",
            kwargs={
                "session_id": self.session.pk,
            },
        )

        self.detail_url = reverse(
            "learning_engine:assessment-detail",
            kwargs={
                "session_id": self.session.pk,
            },
        )

    def _login(self):
        self.client.force_login(
            self.user
        )

    def test_authentication_is_required(
        self,
    ):
        complete_response = self.client.post(
            self.complete_url
        )

        abandon_response = self.client.post(
            self.abandon_url
        )

        self.assertEqual(
            complete_response.status_code,
            302,
        )

        self.assertEqual(
            abandon_response.status_code,
            302,
        )

        self.session.refresh_from_db()

        self.assertEqual(
            self.session.status,
            AssessmentSession.Status.ACTIVE,
        )

        self.assertIsNone(
            self.session.ended_at
        )

    def test_complete_closes_active_assessment(
        self,
    ):
        self._login()

        mark_session_question_viewed(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
        )

        response = self.client.post(
            self.complete_url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["action"],
            "completed",
        )

        self.session.refresh_from_db()

        self.assertEqual(
            self.session.status,
            AssessmentSession.Status.COMPLETED,
        )

        self.assertIsNotNone(
            self.session.ended_at
        )

        self.assertEqual(
            payload["assessment"]["session_id"],
            self.session.pk,
        )

        self.assertEqual(
            payload["assessment"]["mode"],
            "adaptive",
        )

        self.assertEqual(
            payload["assessment"]["status"],
            "completed",
        )

        self.assertEqual(
            payload["assessment"]["ended_at"],
            self.session.ended_at.isoformat(),
        )

        # ACTIVE assessment endpoint must no longer
        # expose the finished session.
        detail_response = self.client.get(
            self.detail_url
        )

        self.assertEqual(
            detail_response.status_code,
            404,
        )

    def test_abandon_closes_active_assessment(
        self,
    ):
        self._login()

        response = self.client.post(
            self.abandon_url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["action"],
            "abandoned",
        )

        self.session.refresh_from_db()

        self.assertEqual(
            self.session.status,
            AssessmentSession.Status.ABANDONED,
        )

        self.assertIsNotNone(
            self.session.ended_at
        )

        self.assertEqual(
            payload["assessment"]["status"],
            "abandoned",
        )

        self.assertEqual(
            payload["assessment"]["ended_at"],
            self.session.ended_at.isoformat(),
        )

        detail_response = self.client.get(
            self.detail_url
        )

        self.assertEqual(
            detail_response.status_code,
            404,
        )

    def test_completed_assessment_cannot_be_finished_again_or_abandoned(
        self,
    ):
        self._login()

        first_response = self.client.post(
            self.complete_url
        )

        self.assertEqual(
            first_response.status_code,
            200,
        )

        self.session.refresh_from_db()

        original_ended_at = (
            self.session.ended_at
        )

        second_complete = self.client.post(
            self.complete_url
        )

        self.assertEqual(
            second_complete.status_code,
            409,
        )

        self.assertEqual(
            second_complete.json()["error"],
            "assessment_state_conflict",
        )

        abandon_response = self.client.post(
            self.abandon_url
        )

        self.assertEqual(
            abandon_response.status_code,
            409,
        )

        self.session.refresh_from_db()

        self.assertEqual(
            self.session.status,
            AssessmentSession.Status.COMPLETED,
        )

        self.assertEqual(
            self.session.ended_at,
            original_ended_at,
        )

    def test_abandoned_assessment_cannot_be_finished_again_or_completed(
        self,
    ):
        self._login()

        first_response = self.client.post(
            self.abandon_url
        )

        self.assertEqual(
            first_response.status_code,
            200,
        )

        self.session.refresh_from_db()

        original_ended_at = (
            self.session.ended_at
        )

        second_abandon = self.client.post(
            self.abandon_url
        )

        self.assertEqual(
            second_abandon.status_code,
            409,
        )

        complete_response = self.client.post(
            self.complete_url
        )

        self.assertEqual(
            complete_response.status_code,
            409,
        )

        self.session.refresh_from_db()

        self.assertEqual(
            self.session.status,
            AssessmentSession.Status.ABANDONED,
        )

        self.assertEqual(
            self.session.ended_at,
            original_ended_at,
        )

    def test_other_user_and_missing_assessments_return_404(
        self,
    ):
        self._login()

        other_started = start_session(
            user=self.other_user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        other_complete_url = reverse(
            "learning_engine:assessment-complete",
            kwargs={
                "session_id": (
                    other_started.session_id
                ),
            },
        )

        other_abandon_url = reverse(
            "learning_engine:assessment-abandon",
            kwargs={
                "session_id": (
                    other_started.session_id
                ),
            },
        )

        response = self.client.post(
            other_complete_url
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        response = self.client.post(
            other_abandon_url
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        missing_complete_url = reverse(
            "learning_engine:assessment-complete",
            kwargs={
                "session_id": 999999999,
            },
        )

        missing_abandon_url = reverse(
            "learning_engine:assessment-abandon",
            kwargs={
                "session_id": 999999999,
            },
        )

        self.assertEqual(
            self.client.post(
                missing_complete_url
            ).status_code,
            404,
        )

        self.assertEqual(
            self.client.post(
                missing_abandon_url
            ).status_code,
            404,
        )

    def test_completed_viewed_assessment_becomes_finalized_evidence(
        self,
    ):
        # Persist False deliberately.
        #
        # Real finalized assessment evidence must
        # override the old beginner onboarding answer.
        User.objects.filter(
            pk=self.user.pk,
        ).update(
            prior_russian_experience=False,
        )

        self.user.refresh_from_db()

        mark_session_question_viewed(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
        )

        self._login()

        response = self.client.post(
            self.complete_url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        state = resolve_learner_entry_state(
            user=self.user,
        )

        self.assertTrue(
            state.has_finalized_assessment_evidence
        )

        self.assertEqual(
            state.route,
            LearnerEntryRoute.ADAPTIVE,
        )

        self.assertIsNone(
            state.active_session_id
        )

    def test_completed_never_viewed_assessment_is_not_finalized_evidence(
        self,
    ):
        User.objects.filter(
            pk=self.user.pk,
        ).update(
            prior_russian_experience=False,
        )

        self.user.refresh_from_db()

        self.assertIsNone(
            self.session_question.first_viewed_at
        )

        self._login()

        response = self.client.post(
            self.complete_url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        state = resolve_learner_entry_state(
            user=self.user,
        )

        self.assertFalse(
            state.has_finalized_assessment_evidence
        )

        self.assertEqual(
            state.route,
            LearnerEntryRoute.BEGINNER_PATH,
        )

    def test_abandoned_viewed_assessment_is_not_finalized_evidence(
        self,
    ):
        # With prior experience=True, absence of
        # finalized evidence should return the user
        # to the diagnostic-start route after abandon.
        User.objects.filter(
            pk=self.user.pk,
        ).update(
            prior_russian_experience=True,
        )

        self.user.refresh_from_db()

        mark_session_question_viewed(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
        )

        self._login()

        response = self.client.post(
            self.abandon_url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        state = resolve_learner_entry_state(
            user=self.user,
        )

        self.assertFalse(
            state.has_finalized_assessment_evidence
        )

        self.assertEqual(
            state.route,
            LearnerEntryRoute.START_DIAGNOSTIC,
        )

        self.assertIsNone(
            state.active_session_id
        )

    def test_finish_responses_do_not_expose_answer_feedback(
        self,
    ):
        self._login()

        response = self.client.post(
            self.complete_url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        forbidden_keys = {
            "is_correct",
            "correct_answer",
            "accepted_answers",
            "explanation",
            "misconception_code",
            "mastery",
            "score",
        }

        response_text = json.dumps(
            payload
        )

        for key in forbidden_keys:
            self.assertNotIn(
                key,
                response_text,
            )

class AssessmentResultAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="assessment-result@example.com",
            password="test-password-123",
        )

        cls.other_user = User.objects.create_user(
            email="assessment-result-other@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Assessment Result API Topic",
            category="grammar",
            display_order=30000,
            is_active=True,
        )

        cls.skill_1 = Skill.objects.create(
            topic=cls.topic,
            skill_code="RESULT-A1-01",
            skill_name="Assessment Result Skill One",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.skill_2 = Skill.objects.create(
            topic=cls.topic,
            skill_code="RESULT-A1-02",
            skill_name="Assessment Result Skill Two",
            cefr_level="A1",
            is_extension=False,
            display_order=2,
            is_active=True,
        )

        cls.skill_3 = Skill.objects.create(
            topic=cls.topic,
            skill_code="RESULT-A1-03",
            skill_name="Assessment Result Skill Three",
            cefr_level="A1",
            is_extension=False,
            display_order=3,
            is_active=True,
        )

        cls.question_1 = Question.objects.create(
            source_id="RESULT-Q001",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text="Result question one.",
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.2500"),
            correct_answer="A",
            accepted_answers=[
                "A",
            ],
            explanation="Secret explanation one.",
            audio_url=None,
            is_active=True,
        )

        cls.question_2 = Question.objects.create(
            source_id="RESULT-Q002",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text="Result question two.",
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.5000"),
            correct_answer="B",
            accepted_answers=[
                "B",
            ],
            explanation="Secret explanation two.",
            audio_url=None,
            is_active=True,
        )

        cls.question_3 = Question.objects.create(
            source_id="RESULT-Q003",
            question_type="multiple_choice",
            item_format="single_choice",
            question_text="Result question three.",
            cefr_level="A1",
            is_extension=False,
            difficulty_score=Decimal("0.7500"),
            correct_answer="A",
            accepted_answers=[
                "A",
            ],
            explanation="Secret explanation three.",
            audio_url=None,
            is_active=True,
        )

        # --------------------------------------------------
        # Skill mappings
        #
        # Q1 is genuinely multi-skill.
        # --------------------------------------------------

        QuestionSkill.objects.create(
            question=cls.question_1,
            skill=cls.skill_1,
            is_primary=True,
            weight=Decimal("0.70000"),
        )

        QuestionSkill.objects.create(
            question=cls.question_1,
            skill=cls.skill_2,
            is_primary=False,
            weight=Decimal("0.30000"),
        )

        QuestionSkill.objects.create(
            question=cls.question_2,
            skill=cls.skill_1,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

        QuestionSkill.objects.create(
            question=cls.question_3,
            skill=cls.skill_2,
            is_primary=True,
            weight=Decimal("1.00000"),
        )

        # --------------------------------------------------
        # Options
        # --------------------------------------------------

        for question, correct_key in (
            (
                cls.question_1,
                "A",
            ),
            (
                cls.question_2,
                "B",
            ),
            (
                cls.question_3,
                "A",
            ),
        ):
            for display_order, option_key in enumerate(
                (
                    "A",
                    "B",
                    "C",
                ),
                start=1,
            ):
                QuestionOption.objects.create(
                    question=question,
                    option_key=option_key,
                    option_text=(
                        f"Option {option_key}"
                    ),
                    misconception_code=(
                        None
                        if option_key == correct_key
                        else (
                            f"SECRET-"
                            f"{question.source_id}-"
                            f"{option_key}"
                        )
                    ),
                    is_correct=(
                        option_key == correct_key
                    ),
                    display_order=display_order,
                )

    def setUp(self):
        started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        self.session = AssessmentSession.objects.get(
            pk=started.session_id
        )

        added_1 = add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.question_1.pk,
        )

        added_2 = add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.question_2.pk,
        )

        added_3 = add_question_to_session(
            user=self.user,
            session_id=self.session.pk,
            question_id=self.question_3.pk,
        )

        self.sq_1 = SessionQuestion.objects.get(
            pk=added_1.session_question_id
        )

        self.sq_2 = SessionQuestion.objects.get(
            pk=added_2.session_question_id
        )

        self.sq_3 = SessionQuestion.objects.get(
            pk=added_3.session_question_id
        )

        self.url = reverse(
            "learning_engine:assessment-result",
            kwargs={
                "session_id": self.session.pk,
            },
        )

    def _login(self):
        self.client.force_login(
            self.user
        )

    def _prepare_mixed_result(
        self,
    ):
        """
        Produce canonical mixed evidence:

        Q1:
            answer B (wrong)
            revise to A (correct)
            -> final attempt correct

        Q2:
            viewed, unanswered
            -> incorrect evidence

        Q3:
            never viewed
            -> excluded
        """

        first_attempt = save_option_answer(
            user=self.user,
            session_question_id=self.sq_1.pk,
            selected_option_key="B",
            response_time_ms=1500,
        )

        final_attempt = save_option_answer(
            user=self.user,
            session_question_id=self.sq_1.pk,
            selected_option_key="A",
            response_time_ms=900,
        )

        mark_session_question_viewed(
            user=self.user,
            session_question_id=self.sq_2.pk,
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        self.session.refresh_from_db()

        return (
            first_attempt,
            final_attempt,
        )

    def test_authentication_is_required(
        self,
    ):
        self._prepare_mixed_result()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            302,
        )

    def test_completed_result_uses_final_attempt_and_canonical_evidence_summary(
        self,
    ):
        (
            first_attempt,
            final_attempt,
        ) = self._prepare_mixed_result()

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["assessment"]["session_id"],
            self.session.pk,
        )

        self.assertEqual(
            payload["assessment"]["mode"],
            "adaptive",
        )

        self.assertEqual(
            payload["assessment"]["status"],
            "completed",
        )

        self.assertIsNotNone(
            payload["assessment"]["ended_at"]
        )

        summary = payload[
            "summary"
        ]

        self.assertEqual(
            summary["question_count"],
            3,
        )

        self.assertEqual(
            summary["viewed_count"],
            2,
        )

        self.assertEqual(
            summary["answered_count"],
            1,
        )

        # Q1's old wrong revision must NOT count.
        self.assertEqual(
            summary["correct_count"],
            1,
        )

        self.assertEqual(
            summary[
                "incorrect_answer_count"
            ],
            0,
        )

        self.assertEqual(
            summary[
                "viewed_unanswered_count"
            ],
            1,
        )

        self.assertEqual(
            summary[
                "never_viewed_count"
            ],
            1,
        )

        self.assertEqual(
            summary[
                "evidence_question_count"
            ],
            2,
        )

        self.assertEqual(
            summary[
                "evidence_incorrect_count"
            ],
            1,
        )

        self.assertEqual(
            summary["evidence_accuracy"],
            "0.5000",
        )

        questions = payload[
            "questions"
        ]

        self.assertEqual(
            [
                row["sequence_number"]
                for row in questions
            ],
            [
                1,
                2,
                3,
            ],
        )

        # --------------------------------------------------
        # Q1: final revision only
        # --------------------------------------------------

        q1 = questions[0]

        self.assertEqual(
            q1["result_state"],
            "correct",
        )

        self.assertEqual(
            q1["evidence_outcome"],
            "correct",
        )

        self.assertEqual(
            q1["final_attempt"][
                "attempt_id"
            ],
            final_attempt.attempt_id,
        )

        self.assertNotEqual(
            q1["final_attempt"][
                "attempt_id"
            ],
            first_attempt.attempt_id,
        )

        self.assertEqual(
            q1["final_attempt"][
                "attempt_number"
            ],
            2,
        )

        self.assertEqual(
            q1["final_attempt"][
                "selected_option_key"
            ],
            "A",
        )

        self.assertTrue(
            q1["final_attempt"][
                "is_correct"
            ]
        )

        # --------------------------------------------------
        # Q2: viewed, unanswered
        # --------------------------------------------------

        q2 = questions[1]

        self.assertEqual(
            q2["result_state"],
            "viewed_unanswered",
        )

        self.assertEqual(
            q2["evidence_outcome"],
            "incorrect",
        )

        self.assertIsNone(
            q2["final_attempt"]
        )

        # --------------------------------------------------
        # Q3: never viewed
        # --------------------------------------------------

        q3 = questions[2]

        self.assertEqual(
            q3["result_state"],
            "never_viewed",
        )

        self.assertEqual(
            q3["evidence_outcome"],
            "excluded",
        )

        self.assertIsNone(
            q3["final_attempt"]
        )

    def test_completed_result_exposes_grading_snapshot_but_not_current_answer_key(
        self,
    ):
        self._prepare_mixed_result()

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        q1 = response.json()[
            "questions"
        ][0]

        # Completed result may expose the immutable
        # attempt grading snapshot.
        self.assertTrue(
            q1["final_attempt"]["is_correct"]
        )

        question_payload = q1[
            "question"
        ]

        # But these are current Question Bank fields,
        # not historical assessment snapshots.
        self.assertNotIn(
            "correct_answer",
            question_payload,
        )

        self.assertNotIn(
            "accepted_answers",
            question_payload,
        )

        self.assertNotIn(
            "explanation",
            question_payload,
        )

        for option in question_payload[
            "options"
        ]:
            self.assertNotIn(
                "is_correct",
                option,
            )

            self.assertNotIn(
                "misconception_code",
                option,
            )

        # Current DB genuinely contains those values,
        # so this test is meaningful.
        self.assertEqual(
            self.question_1.correct_answer,
            "A",
        )

        self.assertEqual(
            self.question_1.explanation,
            "Secret explanation one.",
        )

        self.assertTrue(
            QuestionOption.objects.get(
                question=self.question_1,
                option_key="A",
            ).is_correct
        )

        # We also deliberately do not fabricate
        # mastery before/after deltas.
        payload = response.json()

        self.assertNotIn(
            "mastery_change",
            payload,
        )

        self.assertNotIn(
            "mastery_before",
            payload,
        )

        self.assertNotIn(
            "mastery_after",
            payload,
        )

    def test_affected_skills_are_read_from_historical_skill_snapshot(
        self,
    ):
        # SessionQuestion snapshots have already
        # been created by setUp().
        #
        # Change CURRENT QuestionSkill taxonomy
        # afterwards:
        # Q1 secondary Skill 2 -> Skill 3.
        QuestionSkill.objects.filter(
            question=self.question_1,
            skill=self.skill_2,
        ).update(
            skill=self.skill_3,
        )

        # Complete manually in this particular test
        # so current taxonomy changes do not invoke
        # unrelated mastery-recalculation concerns.
        self.session.status = (
            AssessmentSession.Status.COMPLETED
        )

        self.session.ended_at = (
            timezone.now()
        )

        self.session.full_clean()

        self.session.save(
            update_fields=[
                "status",
                "ended_at",
            ]
        )

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        affected_skills = (
            response.json()[
                "affected_skills"
            ]
        )

        by_code = {
            row["skill_code"]: row
            for row in affected_skills
        }

        # Historical snapshot:
        #
        # Q1 -> Skill 1 primary + Skill 2 secondary
        # Q2 -> Skill 1 primary
        # Q3 -> Skill 2 primary
        self.assertEqual(
            set(by_code),
            {
                "RESULT-A1-01",
                "RESULT-A1-02",
            },
        )

        self.assertEqual(
            by_code[
                "RESULT-A1-01"
            ][
                "question_count"
            ],
            2,
        )

        self.assertEqual(
            by_code[
                "RESULT-A1-01"
            ][
                "primary_question_count"
            ],
            2,
        )

        self.assertEqual(
            by_code[
                "RESULT-A1-02"
            ][
                "question_count"
            ],
            2,
        )

        self.assertEqual(
            by_code[
                "RESULT-A1-02"
            ][
                "primary_question_count"
            ],
            1,
        )

        # Skill 3 exists only in CURRENT taxonomy,
        # not in historical assessment snapshots.
        self.assertNotIn(
            "RESULT-A1-03",
            by_code,
        )

    def test_active_and_abandoned_assessments_return_409(
        self,
    ):
        self._login()

        # Current session is ACTIVE.
        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            409,
        )

        self.assertEqual(
            response.json()["error"],
            "assessment_result_unavailable",
        )

        # Separate abandoned session.
        abandoned_started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.DIAGNOSTIC,
        )

        abandon_session(
            user=self.user,
            session_id=(
                abandoned_started.session_id
            ),
        )

        abandoned_url = reverse(
            "learning_engine:assessment-result",
            kwargs={
                "session_id": (
                    abandoned_started.session_id
                ),
            },
        )

        response = self.client.get(
            abandoned_url
        )

        self.assertEqual(
            response.status_code,
            409,
        )

        self.assertEqual(
            response.json()["error"],
            "assessment_result_unavailable",
        )

    def test_other_user_and_missing_assessments_return_404(
        self,
    ):
        other_started = start_session(
            user=self.other_user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        other_session = AssessmentSession.objects.get(
            pk=other_started.session_id
        )

        other_session.status = (
            AssessmentSession.Status.COMPLETED
        )

        other_session.ended_at = (
            timezone.now()
        )

        other_session.full_clean()

        other_session.save(
            update_fields=[
                "status",
                "ended_at",
            ]
        )

        self._login()

        other_url = reverse(
            "learning_engine:assessment-result",
            kwargs={
                "session_id": other_session.pk,
            },
        )

        response = self.client.get(
            other_url
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        missing_url = reverse(
            "learning_engine:assessment-result",
            kwargs={
                "session_id": 999999999,
            },
        )

        response = self.client.get(
            missing_url
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_result_get_is_read_only(
        self,
    ):
        self._prepare_mixed_result()

        original_session_status = (
            self.session.status
        )

        original_ended_at = (
            self.session.ended_at
        )

        original_attempts = list(
            QuestionAttempt.objects
            .filter(
                session_question__session=(
                    self.session
                ),
            )
            .order_by(
                "attempt_id"
            )
            .values_list(
                "attempt_id",
                "attempt_number",
                "selected_option_key",
                "is_correct",
            )
        )

        original_session_questions = list(
            SessionQuestion.objects
            .filter(
                session=self.session,
            )
            .order_by(
                "sequence_number"
            )
            .values_list(
                "session_question_id",
                "first_viewed_at",
                "active_response_time_ms",
            )
        )

        self._login()

        first_response = self.client.get(
            self.url
        )

        second_response = self.client.get(
            self.url
        )

        self.assertEqual(
            first_response.status_code,
            200,
        )

        self.assertEqual(
            second_response.status_code,
            200,
        )

        self.session.refresh_from_db()

        self.assertEqual(
            self.session.status,
            original_session_status,
        )

        self.assertEqual(
            self.session.ended_at,
            original_ended_at,
        )

        self.assertEqual(
            list(
                QuestionAttempt.objects
                .filter(
                    session_question__session=(
                        self.session
                    ),
                )
                .order_by(
                    "attempt_id"
                )
                .values_list(
                    "attempt_id",
                    "attempt_number",
                    "selected_option_key",
                    "is_correct",
                )
            ),
            original_attempts,
        )

        self.assertEqual(
            list(
                SessionQuestion.objects
                .filter(
                    session=self.session,
                )
                .order_by(
                    "sequence_number"
                )
                .values_list(
                    "session_question_id",
                    "first_viewed_at",
                    "active_response_time_ms",
                )
            ),
            original_session_questions,
        )

        # Deterministic repeated GET.
        self.assertEqual(
            first_response.json(),
            second_response.json(),
        )

class AssessmentResultCurrentMasteryAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="result-mastery@example.com",
            password="test-password-123",
            prior_russian_experience=False,
        )

        cls.topic = Topic.objects.create(
            topic_name="Result Current Mastery Topic",
            category="grammar",
            display_order=31000,
            is_active=True,
        )

        cls.historical_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="RESULT-MASTERY-A1",
            skill_name="Result Historical Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.replacement_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="RESULT-MASTERY-NEW",
            skill_name="Result Replacement Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=2,
            is_active=True,
        )

        cls.questions = []

        for index, difficulty in enumerate(
            (
                "0.2500",
                "0.5000",
                "0.7500",
                "0.5000",
            ),
            start=1,
        ):
            question = Question.objects.create(
                source_id=(
                    f"RESULT-MASTERY-Q{index:03d}"
                ),
                question_type="multiple_choice",
                item_format="single_choice",
                question_text=(
                    f"Result mastery question {index}."
                ),
                cefr_level="A1",
                is_extension=False,
                difficulty_score=Decimal(
                    difficulty
                ),
                correct_answer="A",
                accepted_answers=[
                    "A",
                ],
                explanation=(
                    f"Explanation {index}."
                ),
                audio_url=None,
                is_active=True,
            )

            QuestionSkill.objects.create(
                question=question,
                skill=cls.historical_skill,
                is_primary=True,
                weight=Decimal("1.00000"),
            )

            QuestionOption.objects.create(
                question=question,
                option_key="A",
                option_text="Correct option",
                misconception_code=None,
                is_correct=True,
                display_order=1,
            )

            QuestionOption.objects.create(
                question=question,
                option_key="B",
                option_text="Wrong option",
                misconception_code=(
                    f"RESULT-MASTERY-WRONG-{index}"
                ),
                is_correct=False,
                display_order=2,
            )

            QuestionOption.objects.create(
                question=question,
                option_key="C",
                option_text="Other wrong option",
                misconception_code=(
                    f"RESULT-MASTERY-WRONG2-{index}"
                ),
                is_correct=False,
                display_order=3,
            )

            cls.questions.append(
                question
            )

    def setUp(self):
        started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        self.session = AssessmentSession.objects.get(
            pk=started.session_id
        )

        self.session_questions = []

        # First assessment contains three observations
        # for the same historical Skill.
        for question in self.questions[:3]:
            added = add_question_to_session(
                user=self.user,
                session_id=self.session.pk,
                question_id=question.pk,
            )

            self.session_questions.append(
                SessionQuestion.objects.get(
                    pk=added.session_question_id
                )
            )

        self.url = reverse(
            "learning_engine:assessment-result",
            kwargs={
                "session_id": self.session.pk,
            },
        )

    def _login(self):
        self.client.force_login(
            self.user
        )

    def _complete_primary_assessment(
        self,
    ):
        """
        Three valid observations:

        Q1 -> correct
        Q2 -> correct
        Q3 -> incorrect

        Therefore:
        observation_count = 3
        correct_attempts = 2

        Exact mastery_score remains owned by the
        canonical mastery engine.
        """

        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_questions[0].pk
            ),
            selected_option_key="A",
            response_time_ms=1000,
        )

        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_questions[1].pk
            ),
            selected_option_key="A",
            response_time_ms=1100,
        )

        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_questions[2].pk
            ),
            selected_option_key="B",
            response_time_ms=1200,
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        self.session.refresh_from_db()

    def test_current_mastery_matches_canonical_persisted_state(
        self,
    ):
        self._complete_primary_assessment()

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        current_mastery = response.json()[
            "current_mastery"
        ]

        self.assertEqual(
            current_mastery["scope"],
            "current",
        )

        self.assertFalse(
            current_mastery[
                "is_historical_assessment_snapshot"
            ]
        )

        self.assertEqual(
            len(
                current_mastery["skills"]
            ),
            1,
        )

        mastery_payload = (
            current_mastery["skills"][0]
        )

        self.assertEqual(
            mastery_payload["skill_id"],
            self.historical_skill.pk,
        )

        self.assertEqual(
            mastery_payload["skill_code"],
            "RESULT-MASTERY-A1",
        )

        state = SkillMastery.objects.get(
            user=self.user,
            skill=self.historical_skill,
        )

        # Canonical evidence count.
        self.assertEqual(
            state.observation_count,
            3,
        )

        self.assertEqual(
            state.correct_attempts,
            2,
        )

        # Three observations satisfy the configured
        # minimum for a persisted mastery score.
        self.assertIsNotNone(
            state.mastery_score
        )

        self.assertEqual(
            mastery_payload["mastery_score"],
            str(
                state.mastery_score
            ),
        )

        self.assertEqual(
            mastery_payload["observation_count"],
            state.observation_count,
        )

        self.assertEqual(
            mastery_payload["correct_attempts"],
            state.correct_attempts,
        )

        self.assertEqual(
            mastery_payload["algorithm_version"],
            state.algorithm_version,
        )

        self.assertEqual(
            mastery_payload["has_sufficient_data"],
            state.has_sufficient_data,
        )

        self.assertEqual(
            mastery_payload["last_evidence_at"],
            (
                state.last_evidence_at.isoformat()
                if state.last_evidence_at
                is not None
                else None
            ),
        )

        self.assertEqual(
            mastery_payload["calculated_at"],
            (
                state.calculated_at.isoformat()
                if state.calculated_at
                is not None
                else None
            ),
        )

    def test_result_never_claims_mastery_before_after_or_delta(
        self,
    ):
        self._complete_primary_assessment()

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        response_text = json.dumps(
            response.json()
        )

        forbidden_fields = (
            "mastery_before",
            "mastery_after",
            "mastery_change",
        )

        for field in forbidden_fields:
            self.assertNotIn(
                field,
                response_text,
            )

        self.assertEqual(
            response.json()[
                "current_mastery"
            ][
                "scope"
            ],
            "current",
        )

    def test_old_result_projects_newer_current_mastery(
        self,
    ):
        """
        Demonstrate explicitly that current_mastery
        is CURRENT state, not a frozen historical
        assessment snapshot.
        """

        self._complete_primary_assessment()

        self._login()

        first_response = self.client.get(
            self.url
        )

        self.assertEqual(
            first_response.status_code,
            200,
        )

        first_mastery = (
            first_response.json()[
                "current_mastery"
            ][
                "skills"
            ][0]
        )

        self.assertEqual(
            first_mastery[
                "observation_count"
            ],
            3,
        )

        # --------------------------------------------------
        # Later assessment adds one more real observation.
        # --------------------------------------------------

        later_started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        later_added = add_question_to_session(
            user=self.user,
            session_id=(
                later_started.session_id
            ),
            question_id=(
                self.questions[3].pk
            ),
        )

        save_option_answer(
            user=self.user,
            session_question_id=(
                later_added.session_question_id
            ),
            selected_option_key="A",
            response_time_ms=900,
        )

        complete_session(
            user=self.user,
            session_id=(
                later_started.session_id
            ),
        )

        # --------------------------------------------------
        # Read the OLD assessment again.
        #
        # Historical result itself is unchanged, but
        # current_mastery must now reflect all current
        # finalized evidence.
        # --------------------------------------------------

        second_response = self.client.get(
            self.url
        )

        self.assertEqual(
            second_response.status_code,
            200,
        )

        second_payload = (
            second_response.json()
        )

        second_mastery = (
            second_payload[
                "current_mastery"
            ][
                "skills"
            ][0]
        )

        self.assertEqual(
            second_mastery[
                "observation_count"
            ],
            4,
        )

        self.assertEqual(
            second_mastery[
                "correct_attempts"
            ],
            3,
        )

        self.assertFalse(
            second_payload[
                "current_mastery"
            ][
                "is_historical_assessment_snapshot"
            ]
        )

        # Historical assessment summary remains tied
        # to the original three-question session.
        self.assertEqual(
            second_payload[
                "summary"
            ][
                "question_count"
            ],
            3,
        )

        self.assertEqual(
            second_payload[
                "summary"
            ][
                "answered_count"
            ],
            3,
        )

    def test_next_action_matches_current_learner_entry_state(
        self,
    ):
        self._complete_primary_assessment()

        # Create a NEW active adaptive assessment.
        #
        # Therefore the current continuation route
        # must be resume_adaptive, not merely adaptive.
        next_started = start_session(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )

        state = resolve_learner_entry_state(
            user=self.user,
        )

        self.assertEqual(
            state.route,
            LearnerEntryRoute.RESUME_ADAPTIVE,
        )

        self.assertEqual(
            state.active_session_id,
            next_started.session_id,
        )

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        next_action = response.json()[
            "next_action"
        ]

        self.assertEqual(
            next_action["route"],
            state.route.value,
        )

        self.assertEqual(
            next_action[
                "active_assessment"
            ],
            {
                "session_id": (
                    state.active_session_id
                ),
                "mode": (
                    state.active_assessment_mode
                ),
            },
        )

        self.assertIsNone(
            next_action[
                "beginner_lesson"
            ]
        )

    def test_completion_recalculates_historical_snapshot_skill_not_current_mapping(
        self,
    ):
        """
        Critical historical-integrity regression.

        SessionQuestions already froze Skill mappings
        during add_question_to_session().

        Changing CURRENT QuestionSkill rows afterwards
        must not redirect completion/mastery to the new
        taxonomy.
        """

        # --------------------------------------------------
        # Change current Question Bank taxonomy AFTER
        # SessionQuestion snapshots have been created.
        # --------------------------------------------------

        for question in self.questions[:3]:
            QuestionSkill.objects.filter(
                question=question,
                skill=self.historical_skill,
            ).update(
                skill=self.replacement_skill,
            )

        # --------------------------------------------------
        # Assessment answers still belong historically
        # to historical_skill through skill_snapshot.
        # --------------------------------------------------

        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_questions[0].pk
            ),
            selected_option_key="A",
        )

        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_questions[1].pk
            ),
            selected_option_key="A",
        )

        save_option_answer(
            user=self.user,
            session_question_id=(
                self.session_questions[2].pk
            ),
            selected_option_key="B",
        )

        complete_session(
            user=self.user,
            session_id=self.session.pk,
        )

        historical_state = (
            SkillMastery.objects.get(
                user=self.user,
                skill=self.historical_skill,
            )
        )

        self.assertEqual(
            historical_state.observation_count,
            3,
        )

        self.assertEqual(
            historical_state.correct_attempts,
            2,
        )

        # Completion must NOT have created mastery
        # for the replacement/current taxonomy Skill.
        self.assertFalse(
            SkillMastery.objects.filter(
                user=self.user,
                skill=self.replacement_skill,
            ).exists()
        )

        self._login()

        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        affected_codes = {
            row["skill_code"]
            for row
            in payload[
                "affected_skills"
            ]
        }

        mastery_codes = {
            row["skill_code"]
            for row
            in payload[
                "current_mastery"
            ][
                "skills"
            ]
        }

        self.assertEqual(
            affected_codes,
            {
                "RESULT-MASTERY-A1",
            },
        )

        self.assertEqual(
            mastery_codes,
            {
                "RESULT-MASTERY-A1",
            },
        )

        self.assertNotIn(
            "RESULT-MASTERY-NEW",
            affected_codes,
        )

        self.assertNotIn(
            "RESULT-MASTERY-NEW",
            mastery_codes,
        )