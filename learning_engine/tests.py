from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from curriculum.models import (
    Lesson,
    Skill,
    Topic,
)
from learning_engine.models import (
    PracticeSession,
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
    start_session,
    submit_option_answer,
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
)
from datetime import timedelta
from decimal import Decimal

User = get_user_model()


class SubmitOptionAnswerTests(TestCase):
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
        self.session = PracticeSession.objects.create(
            user=self.user,
            mode=PracticeSession.Mode.ADAPTIVE,
            status=PracticeSession.Status.ACTIVE,
        )

        self.session_question = (
            SessionQuestion.objects.create(
                session=self.session,
                question=self.question,
                sequence_number=1,
            )
        )

    # =================================================
    # Submit / grading
    # =================================================

    def test_correct_answer_is_graded_and_saved(self):
        result = submit_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="B",
            response_time_ms=1500,
        )

        self.assertTrue(
            result.is_correct
        )
        self.assertEqual(
            result.correct_option_key,
            "B",
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
        self.assertTrue(
            attempt.is_correct
        )
        self.assertEqual(
            attempt.response_time_ms,
            1500,
        )

    def test_wrong_answer_is_graded_and_saved(self):
        result = submit_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="A",
        )

        self.assertFalse(
            result.is_correct
        )
        self.assertEqual(
            result.correct_option_key,
            "B",
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

    def test_attempt_number_increments_on_revision(self):
        first = submit_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="A",
        )

        second = submit_option_answer(
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

        self.assertFalse(
            first.is_correct
        )
        self.assertTrue(
            second.is_correct
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
            submit_option_answer(
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
            submit_option_answer(
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
            PracticeSession.Status.COMPLETED
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
            submit_option_answer(
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
            submit_option_answer(
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
                    submit_option_answer(
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
        result = submit_option_answer(
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
            submit_option_answer(
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
                submit_option_answer(
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

        # submit_option_answer() sets viewed before
        # creating the Attempt, but the whole transaction
        # must roll back if Attempt validation fails.
        self.assertIsNone(
            self.session_question.first_viewed_at
        )

    def test_lesson_session_does_not_accept_assessment_answer(self):
        lesson = Lesson.objects.create(
            title="Practice-only lesson",
            lesson_type="grammar",
            content={
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=999,
            is_active=True,
        )

        self.session.mode = (
            PracticeSession.Mode.LESSON
        )
        self.session.lesson = lesson

        self.session.save(
            update_fields=[
                "mode",
                "lesson",
            ]
        )

        with self.assertRaises(
            LearningServiceError
        ):
            submit_option_answer(
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

    def test_lesson_session_cannot_add_assessment_question(self):
        lesson = Lesson.objects.create(
            title="Lesson without assessment items",
            lesson_type="grammar",
            content={
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=998,
            is_active=True,
        )

        self.session.mode = (
            PracticeSession.Mode.LESSON
        )
        self.session.lesson = lesson

        self.session.save(
            update_fields=[
                "mode",
                "lesson",
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

    def test_submit_marks_question_as_viewed(self):
        self.assertIsNone(
            self.session_question.first_viewed_at
        )

        submit_option_answer(
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
            PracticeSession.Status.COMPLETED
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
        first = submit_option_answer(
            user=self.user,
            session_question_id=(
                self.session_question.pk
            ),
            selected_option_key="A",
        )

        second = submit_option_answer(
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
        submit_option_answer(
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
        submit_option_answer(
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
        result = submit_option_answer(
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
        submit_option_answer(
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
        submit_option_answer(
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
            session = PracticeSession.objects.create(
                user=self.user,
                mode=PracticeSession.Mode.ADAPTIVE,
                status=PracticeSession.Status.ACTIVE,
            )

            session_question = (
                SessionQuestion.objects.create(
                    session=session,
                    question=self.question,
                    sequence_number=1,
                )
            )

            submit_option_answer(
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

        PracticeSession.objects.filter(
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
        submit_option_answer(
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
        submit_option_answer(
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
        submit_option_answer(
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
        submit_option_answer(
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
            PracticeSession.Status.ACTIVE,
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
        submit_option_answer(
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

        submit_option_answer(
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


class PracticeSessionServiceTests(TestCase):
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

        cls.lesson = Lesson.objects.create(
            title="Test Grammar Lesson",
            lesson_type="grammar",
            content={
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=1,
            is_active=True,
        )

    # =================================================
    # Start
    # =================================================

    def test_start_lesson_session(self):
        result = start_session(
            user=self.user,
            mode=PracticeSession.Mode.LESSON,
            lesson_id=self.lesson.pk,
        )

        self.assertEqual(
            result.mode,
            PracticeSession.Mode.LESSON,
        )
        self.assertEqual(
            result.status,
            PracticeSession.Status.ACTIVE,
        )
        self.assertEqual(
            result.lesson_id,
            self.lesson.pk,
        )

        session = PracticeSession.objects.get(
            pk=result.session_id
        )

        self.assertEqual(
            session.user,
            self.user,
        )
        self.assertEqual(
            session.lesson,
            self.lesson,
        )
        self.assertEqual(
            session.status,
            PracticeSession.Status.ACTIVE,
        )
        self.assertIsNone(
            session.ended_at
        )

    def test_start_adaptive_session(self):
        result = start_session(
            user=self.user,
            mode=PracticeSession.Mode.ADAPTIVE,
        )

        self.assertEqual(
            result.mode,
            PracticeSession.Mode.ADAPTIVE,
        )
        self.assertEqual(
            result.status,
            PracticeSession.Status.ACTIVE,
        )
        self.assertIsNone(
            result.lesson_id
        )

        session = PracticeSession.objects.get(
            pk=result.session_id
        )

        self.assertIsNone(
            session.lesson_id
        )
        self.assertIsNone(
            session.ended_at
        )

    def test_start_diagnostic_session(self):
        result = start_session(
            user=self.user,
            mode=PracticeSession.Mode.DIAGNOSTIC,
        )

        self.assertEqual(
            result.mode,
            PracticeSession.Mode.DIAGNOSTIC,
        )
        self.assertEqual(
            result.status,
            PracticeSession.Status.ACTIVE,
        )
        self.assertIsNone(
            result.lesson_id
        )

    def test_lesson_mode_requires_lesson_id(self):
        with self.assertRaises(
            LearningServiceError
        ):
            start_session(
                user=self.user,
                mode=PracticeSession.Mode.LESSON,
            )

        self.assertEqual(
            PracticeSession.objects.count(),
            0,
        )

    def test_nonexistent_lesson_is_rejected(self):
        with self.assertRaises(
            LearningServiceError
        ):
            start_session(
                user=self.user,
                mode=PracticeSession.Mode.LESSON,
                lesson_id=999999,
            )

        self.assertEqual(
            PracticeSession.objects.count(),
            0,
        )

    def test_inactive_lesson_is_rejected(self):
        self.lesson.is_active = False

        self.lesson.save(
            update_fields=[
                "is_active",
            ]
        )

        with self.assertRaises(
            LearningServiceError
        ):
            start_session(
                user=self.user,
                mode=PracticeSession.Mode.LESSON,
                lesson_id=self.lesson.pk,
            )

        self.assertEqual(
            PracticeSession.objects.count(),
            0,
        )

    def test_non_lesson_modes_reject_lesson_id(self):
        for mode in [
            PracticeSession.Mode.ADAPTIVE,
            PracticeSession.Mode.DIAGNOSTIC,
        ]:
            with self.subTest(
                mode=mode
            ):
                with self.assertRaises(
                    LearningServiceError
                ):
                    start_session(
                        user=self.user,
                        mode=mode,
                        lesson_id=self.lesson.pk,
                    )

        self.assertEqual(
            PracticeSession.objects.count(),
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
            PracticeSession.objects.count(),
            0,
        )

    # =================================================
    # Finish
    # =================================================

    def test_complete_active_session(self):
        started = start_session(
            user=self.user,
            mode=PracticeSession.Mode.ADAPTIVE,
        )

        result = complete_session(
            user=self.user,
            session_id=started.session_id,
        )

        self.assertEqual(
            result.status,
            PracticeSession.Status.COMPLETED,
        )

        session = PracticeSession.objects.get(
            pk=started.session_id
        )

        self.assertEqual(
            session.status,
            PracticeSession.Status.COMPLETED,
        )
        self.assertIsNotNone(
            session.ended_at
        )

    def test_lesson_session_can_be_completed(self):
        started = start_session(
            user=self.user,
            mode=PracticeSession.Mode.LESSON,
            lesson_id=self.lesson.pk,
        )

        result = complete_session(
            user=self.user,
            session_id=started.session_id,
        )

        self.assertEqual(
            result.status,
            PracticeSession.Status.COMPLETED,
        )

        session = PracticeSession.objects.get(
            pk=started.session_id
        )

        self.assertEqual(
            session.status,
            PracticeSession.Status.COMPLETED,
        )
        self.assertIsNotNone(
            session.ended_at
        )

    def test_abandon_active_session(self):
        started = start_session(
            user=self.user,
            mode=PracticeSession.Mode.ADAPTIVE,
        )

        result = abandon_session(
            user=self.user,
            session_id=started.session_id,
        )

        self.assertEqual(
            result.status,
            PracticeSession.Status.ABANDONED,
        )

        session = PracticeSession.objects.get(
            pk=started.session_id
        )

        self.assertEqual(
            session.status,
            PracticeSession.Status.ABANDONED,
        )
        self.assertIsNotNone(
            session.ended_at
        )

    def test_other_user_cannot_finish_session(self):
        started = start_session(
            user=self.user,
            mode=PracticeSession.Mode.ADAPTIVE,
        )

        with self.assertRaises(
            LearningServiceError
        ):
            complete_session(
                user=self.other_user,
                session_id=started.session_id,
            )

        session = PracticeSession.objects.get(
            pk=started.session_id
        )

        self.assertEqual(
            session.status,
            PracticeSession.Status.ACTIVE,
        )
        self.assertIsNone(
            session.ended_at
        )

    def test_finished_session_cannot_transition_again(self):
        started = start_session(
            user=self.user,
            mode=PracticeSession.Mode.ADAPTIVE,
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

        session = PracticeSession.objects.get(
            pk=started.session_id
        )

        self.assertEqual(
            session.status,
            PracticeSession.Status.COMPLETED,
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

    def test_half_life_halves_evidence_weight(self):
        evidence = [
            self.make_evidence(
                index=1,
                is_correct=True,
                difficulty="0.50",
                days_ago=21,
            ),
        ]

        result = calculate_mastery(
            evidence,
            as_of=self.as_of,
        )

        # After exactly one half-life:
        # effective weight = 0.5
        #
        # (1 + 0.5) / (2 + 0.5) = 0.6
        self.assertAlmostEqual(
            result.mastery_estimate,
            Decimal("0.600000"),
            places=6,
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