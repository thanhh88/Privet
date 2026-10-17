from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from curriculum.models import (
    Lesson,
    LessonSkill,
    Skill,
    Topic,
)
from learning_engine.models import (
    AssessmentSession,
    SkillMastery,
)
from tracking.models import (
    StudyEvent,
    StudySession,
)
from tracking.services import (
    TrackingServiceError,
    record_learning_completion_event,
)


User = get_user_model()


class LearningCompletionEventTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="tracking-user@example.com",
            password="test-password-123",
        )

        cls.other_user = User.objects.create_user(
            email="tracking-other@example.com",
            password="test-password-123",
        )

        cls.topic = Topic.objects.create(
            topic_name="Tracking Test Topic",
            category="grammar",
            display_order=999,
            is_active=True,
        )

        cls.skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="TRACK-01",
            skill_name="Tracking Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=1,
            is_active=True,
        )

        cls.unrelated_skill = Skill.objects.create(
            topic=cls.topic,
            skill_code="TRACK-02",
            skill_name="Unrelated Tracking Skill",
            cefr_level="A1",
            is_extension=False,
            display_order=2,
            is_active=True,
        )

        cls.lesson = Lesson.objects.create(
            title="Tracking Test Lesson",
            lesson_type="grammar",
            content={
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=999,
            is_active=True,
        )

        LessonSkill.objects.create(
            lesson=cls.lesson,
            skill=cls.skill,
        )

    def create_completed_session(
        self,
        *,
        activity_type,
        ended_at=None,
    ):
        if ended_at is None:
            ended_at = timezone.now()

        return StudySession.objects.create(
            user=self.user,
            lesson=self.lesson,
            assessment_session=None,
            activity_type=activity_type,
            status=StudySession.Status.COMPLETED,
            ended_at=ended_at,
            last_activity_at=ended_at,
        )

    def test_lesson_completion_creates_event_and_recency(
        self,
    ):
        ended_at = timezone.now()

        session = self.create_completed_session(
            activity_type=(
                StudySession.ActivityType.LESSON
            ),
            ended_at=ended_at,
        )

        result = record_learning_completion_event(
            user=self.user,
            study_session_id=session.pk,
        )

        self.assertTrue(
            result.created
        )

        self.assertEqual(
            result.event_type,
            StudyEvent.EventType.LESSON_COMPLETED,
        )

        state = SkillMastery.objects.get(
            user=self.user,
            skill=self.skill,
        )

        self.assertEqual(
            state.last_learning_activity_at,
            ended_at,
        )

        # Learning activity must not create
        # assessment evidence.
        self.assertEqual(
            state.observation_count,
            0,
        )

        self.assertEqual(
            state.correct_attempts,
            0,
        )

        self.assertIsNone(
            state.mastery_score
        )

        self.assertIsNone(
            state.recent_accuracy
        )

        self.assertIsNone(
            state.historical_accuracy
        )

        self.assertEqual(
            state.consecutive_errors,
            0,
        )

        self.assertFalse(
            SkillMastery.objects.filter(
                user=self.user,
                skill=self.unrelated_skill,
            ).exists()
        )

    def test_practice_completion_preserves_assessment_metrics(
        self,
    ):
        calculated_at = (
            timezone.now()
            - timedelta(days=1)
        )

        state = SkillMastery.objects.create(
            user=self.user,
            skill=self.skill,
            mastery_score=Decimal("0.80000"),
            observation_count=3,
            correct_attempts=2,
            recent_accuracy=Decimal("0.66667"),
            historical_accuracy=Decimal("0.66667"),
            avg_response_time_ms=Decimal("1500.00"),
            consecutive_errors=1,
            last_evidence_at=calculated_at,
            calculated_at=calculated_at,
            algorithm_version="rule_v1",
        )

        ended_at = timezone.now()

        session = self.create_completed_session(
            activity_type=(
                StudySession.ActivityType.PRACTICE
            ),
            ended_at=ended_at,
        )

        result = record_learning_completion_event(
            user=self.user,
            study_session_id=session.pk,
        )

        self.assertEqual(
            result.event_type,
            StudyEvent.EventType.PRACTICE_COMPLETED,
        )

        state.refresh_from_db()

        self.assertEqual(
            state.last_learning_activity_at,
            ended_at,
        )

        self.assertEqual(
            state.mastery_score,
            Decimal("0.80000"),
        )

        self.assertEqual(
            state.observation_count,
            3,
        )

        self.assertEqual(
            state.correct_attempts,
            2,
        )

        self.assertEqual(
            state.recent_accuracy,
            Decimal("0.66667"),
        )

        self.assertEqual(
            state.historical_accuracy,
            Decimal("0.66667"),
        )

        self.assertEqual(
            state.avg_response_time_ms,
            Decimal("1500.00"),
        )

        self.assertEqual(
            state.consecutive_errors,
            1,
        )

        self.assertEqual(
            state.last_evidence_at,
            calculated_at,
        )

        self.assertEqual(
            state.calculated_at,
            calculated_at,
        )

    def test_completion_event_is_idempotent(self):
        session = self.create_completed_session(
            activity_type=(
                StudySession.ActivityType.LESSON
            ),
        )

        first = record_learning_completion_event(
            user=self.user,
            study_session_id=session.pk,
        )

        second = record_learning_completion_event(
            user=self.user,
            study_session_id=session.pk,
        )

        self.assertTrue(
            first.created
        )

        self.assertFalse(
            second.created
        )

        self.assertEqual(
            first.study_event_id,
            second.study_event_id,
        )

        self.assertEqual(
            StudyEvent.objects.filter(
                session=session,
                event_type=(
                    StudyEvent.EventType.LESSON_COMPLETED
                ),
            ).count(),
            1,
        )

    def test_active_study_session_is_rejected(self):
        session = StudySession.objects.create(
            user=self.user,
            lesson=self.lesson,
            assessment_session=None,
            activity_type=(
                StudySession.ActivityType.LESSON
            ),
            status=StudySession.Status.ACTIVE,
        )

        with self.assertRaises(
            TrackingServiceError
        ):
            record_learning_completion_event(
                user=self.user,
                study_session_id=session.pk,
            )

        self.assertEqual(
            StudyEvent.objects.count(),
            0,
        )

        self.assertFalse(
            SkillMastery.objects.filter(
                user=self.user,
                skill=self.skill,
            ).exists()
        )

    def test_other_user_cannot_record_completion_event(
        self,
    ):
        session = self.create_completed_session(
            activity_type=(
                StudySession.ActivityType.LESSON
            ),
        )

        with self.assertRaises(
            TrackingServiceError
        ):
            record_learning_completion_event(
                user=self.other_user,
                study_session_id=session.pk,
            )

        self.assertEqual(
            StudyEvent.objects.count(),
            0,
        )

        self.assertFalse(
            SkillMastery.objects.filter(
                user=self.user,
                skill=self.skill,
            ).exists()
        )


class AssessmentTrackingDeletionTests(TestCase):
    """
    Regression coverage for A2.

    Assessment-linked StudySession and StudyEvent rows use
    CASCADE rather than SET_NULL. This prevents the tracking
    target CHECK constraints from being violated during
    AssessmentSession or User deletion.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="tracking-delete@example.com",
            password="test-password-123",
        )

    def create_assessment_tracking(self):
        now = timezone.now()

        assessment = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

        study_session = StudySession.objects.create(
            user=self.user,
            lesson=None,
            assessment_session=assessment,
            activity_type=StudySession.ActivityType.TEST,
            status=StudySession.Status.ACTIVE,
            last_activity_at=now,
            active_seconds=0,
        )

        study_event = StudyEvent.objects.create(
            session=study_session,
            user=self.user,
            event_type=StudyEvent.EventType.TEST_STARTED,
            lesson=None,
            assessment_session=assessment,
            occurred_at=now,
        )

        return (
            assessment,
            study_session,
            study_event,
        )

    def test_deleting_assessment_cascades_test_tracking(
        self,
    ):
        (
            assessment,
            study_session,
            study_event,
        ) = self.create_assessment_tracking()

        assessment_id = assessment.pk
        study_session_id = study_session.pk
        study_event_id = study_event.pk
        user_id = self.user.pk

        # Before A2 this deletion could fail when Django
        # SET_NULLed assessment_session while the tracking
        # target CHECK still required it for test activity.
        assessment.delete()

        self.assertFalse(
            AssessmentSession.objects.filter(
                pk=assessment_id,
            ).exists()
        )

        self.assertFalse(
            StudySession.objects.filter(
                pk=study_session_id,
            ).exists()
        )

        self.assertFalse(
            StudyEvent.objects.filter(
                pk=study_event_id,
            ).exists()
        )

        # Deleting one assessment must not delete its user.
        self.assertTrue(
            User.objects.filter(
                pk=user_id,
            ).exists()
        )

    def test_deleting_user_with_assessment_tracking_succeeds(
        self,
    ):
        (
            assessment,
            study_session,
            study_event,
        ) = self.create_assessment_tracking()

        user_id = self.user.pk
        assessment_id = assessment.pk
        study_session_id = study_session.pk
        study_event_id = study_event.pk

        # This is the exact A2 regression scenario:
        # deleting a User with assessment tracking must
        # cascade cleanly instead of violating the tracking
        # target CHECK constraints.
        self.user.delete()

        self.assertFalse(
            User.objects.filter(
                pk=user_id,
            ).exists()
        )

        self.assertFalse(
            AssessmentSession.objects.filter(
                pk=assessment_id,
            ).exists()
        )

        self.assertFalse(
            StudySession.objects.filter(
                pk=study_session_id,
            ).exists()
        )

        self.assertFalse(
            StudyEvent.objects.filter(
                pk=study_event_id,
            ).exists()
        )
