import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

from curriculum.models import Lesson, Skill, Topic
from learning_engine.models import SkillMastery
from tracking.models import StudyEvent, StudySession
from vocabulary.models import (
    VocabularyItem,
    VocabularyItemTopic,
    VocabularyReviewLog,
    VocabularySRSState,
    VocabularySavedWord,
    VocabularyTopic,
)
from vocabulary.services import (
    VocabularyError,
    start_learning,
    submit_review,
)


TEST_SRS_POLICY = {
    "version": "leitner_v1",
    "intervals": {
        1: 1,
        2: 3,
        3: 7,
        4: 14,
        5: 30,
    },
    "max_new_per_day": 15,
    "max_due_per_request": 50,
}


class VocabularyStabilizationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="vocab-stable@example.com",
            password="password123",
        )

        self.curriculum_topic = Topic.objects.create(
            topic_name="Vocabulary stabilization",
            category="vocabulary",
        )

        self.skill = Skill.objects.create(
            skill_code="VOC-STABLE-A1",
            topic=self.curriculum_topic,
            skill_name="Vocabulary stabilization skill",
            cefr_level="A1",
        )

        self.vocabulary_topic = VocabularyTopic.objects.create(
            code="stable-a1",
            title_vi="Ổn định từ vựng",
            title_ru="Стабилизация словаря",
            cefr_level="A1",
            assessment_skill=self.skill,
        )

        self.item = self._create_item(
            source_id="stable-item-001",
            lemma="мама",
            meaning_vi="mẹ",
            display_order=1,
        )

        self.client.force_login(self.user)

    def _create_item(
        self,
        *,
        source_id,
        lemma,
        meaning_vi,
        display_order,
    ):
        item = VocabularyItem.objects.create(
            source_id=source_id,
            lemma=lemma,
            meaning_vi=meaning_vi,
            meaning_en="",
            cefr_level="A1",
        )

        VocabularyItemTopic.objects.create(
            item=item,
            topic=self.vocabulary_topic,
            display_order=display_order,
        )

        return item

    def _make_due(self, state, *, box=None, version=None):
        updates = {
            "next_review_at": (
                timezone.now() - timedelta(seconds=1)
            ),
        }

        if box is not None:
            updates["box"] = box

        if version is not None:
            updates["version"] = version

        VocabularySRSState.objects.filter(
            pk=state.pk
        ).update(**updates)

        state.refresh_from_db()
        return state

    def _submit(
        self,
        state,
        *,
        rating="remembered",
        request_id=None,
        expected_version=None,
        response_time_ms=2500,
    ):
        return submit_review(
            user=self.user,
            item=self.item,
            request_id=request_id or uuid4(),
            rating=rating,
            expected_version=(
                state.version
                if expected_version is None
                else expected_version
            ),
            response_time_ms=response_time_ms,
        )

    def _create_mastery(self):
        return SkillMastery.objects.create(
            user=self.user,
            skill=self.skill,
            mastery_score=Decimal("0.60000"),
            observation_count=3,
            correct_attempts=2,
            recent_accuracy=Decimal("0.60000"),
            historical_accuracy=Decimal("0.60000"),
            consecutive_errors=0,
            last_evidence_at=timezone.now(),
            last_review_evidence_at=timezone.now(),
            calculated_at=timezone.now(),
        )

    def _mastery_snapshot(self, mastery):
        mastery.refresh_from_db()
        return {
            "mastery_score": mastery.mastery_score,
            "observation_count": mastery.observation_count,
            "correct_attempts": mastery.correct_attempts,
            "recent_accuracy": mastery.recent_accuracy,
            "historical_accuracy": mastery.historical_accuracy,
            "consecutive_errors": mastery.consecutive_errors,
            "last_learning_activity_at": (
                mastery.last_learning_activity_at
            ),
            "last_evidence_at": mastery.last_evidence_at,
            "last_review_evidence_at": (
                mastery.last_review_evidence_at
            ),
            "calculated_at": mastery.calculated_at,
            "algorithm_version": mastery.algorithm_version,
        }

    def test_start_learning_is_idempotent_and_does_not_reset_state(self):
        state, created = start_learning(
            user=self.user,
            item=self.item,
        )

        self.assertTrue(created)
        self.assertEqual(state.box, 1)
        self.assertEqual(state.version, 1)

        VocabularySRSState.objects.filter(pk=state.pk).update(
            box=3,
            version=4,
            lapses=2,
        )

        state_again, created_again = start_learning(
            user=self.user,
            item=self.item,
        )

        self.assertFalse(created_again)
        self.assertEqual(state_again.pk, state.pk)
        self.assertEqual(state_again.box, 3)
        self.assertEqual(state_again.version, 4)
        self.assertEqual(state_again.lapses, 2)
        self.assertEqual(VocabularySRSState.objects.count(), 1)

    def test_remembered_review_advances_box_and_writes_snapshot(self):
        state, _ = start_learning(
            user=self.user,
            item=self.item,
        )
        self._make_due(state)

        log = self._submit(state, rating="remembered")
        state.refresh_from_db()

        self.assertEqual(state.box, 2)
        self.assertEqual(state.version, 2)
        self.assertEqual(state.lapses, 0)
        self.assertEqual(log.box_before, 1)
        self.assertEqual(log.box_after, 2)
        self.assertEqual(log.interval_days_applied, 3)
        self.assertEqual(log.state_version_before, 1)
        self.assertEqual(log.state_version_after, 2)
        self.assertEqual(log.srs_version, "leitner_v1")

    def test_forgot_review_resets_to_box_one_and_increments_lapses(self):
        state, _ = start_learning(
            user=self.user,
            item=self.item,
        )
        self._make_due(state, box=4)

        log = self._submit(state, rating="forgot")
        state.refresh_from_db()

        self.assertEqual(state.box, 1)
        self.assertEqual(state.version, 2)
        self.assertEqual(state.lapses, 1)
        self.assertEqual(log.box_before, 4)
        self.assertEqual(log.box_after, 1)
        self.assertEqual(log.interval_days_applied, 1)

    def test_review_rejects_item_that_is_not_due(self):
        state, _ = start_learning(
            user=self.user,
            item=self.item,
        )

        with self.assertRaises(VocabularyError) as ctx:
            self._submit(state)

        self.assertEqual(ctx.exception.code, "not_due")
        self.assertEqual(VocabularyReviewLog.objects.count(), 0)
        self.assertEqual(StudySession.objects.count(), 0)

    def test_review_rejects_stale_state_version(self):
        state, _ = start_learning(
            user=self.user,
            item=self.item,
        )
        self._make_due(state)

        with self.assertRaises(VocabularyError) as ctx:
            self._submit(
                state,
                expected_version=state.version + 1,
            )

        self.assertEqual(ctx.exception.code, "stale_state")
        self.assertEqual(VocabularyReviewLog.objects.count(), 0)
        self.assertEqual(StudySession.objects.count(), 0)

    def test_idempotent_retry_does_not_duplicate_tracking(self):
        state, _ = start_learning(
            user=self.user,
            item=self.item,
        )
        self._make_due(state)
        request_id = uuid4()

        first = self._submit(
            state,
            request_id=request_id,
        )
        second = self._submit(
            state,
            request_id=request_id,
            expected_version=1,
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(VocabularyReviewLog.objects.count(), 1)
        self.assertEqual(StudySession.objects.count(), 1)
        self.assertEqual(StudyEvent.objects.count(), 2)

    def test_conflicting_idempotent_retry_is_rejected(self):
        state, _ = start_learning(
            user=self.user,
            item=self.item,
        )
        self._make_due(state)
        request_id = uuid4()

        self._submit(
            state,
            request_id=request_id,
            rating="remembered",
        )

        with self.assertRaises(VocabularyError) as ctx:
            self._submit(
                state,
                request_id=request_id,
                rating="forgot",
                expected_version=1,
            )

        self.assertEqual(
            ctx.exception.code,
            "idempotency_conflict",
        )
        self.assertEqual(VocabularyReviewLog.objects.count(), 1)
        self.assertEqual(StudySession.objects.count(), 1)
        self.assertEqual(StudyEvent.objects.count(), 2)

    def test_review_creates_vocabulary_session_and_two_events(self):
        state, _ = start_learning(
            user=self.user,
            item=self.item,
        )
        self._make_due(state)

        self._submit(
            state,
            response_time_ms=2500,
        )

        session = StudySession.objects.get()
        self.assertEqual(
            session.activity_type,
            StudySession.ActivityType.VOCABULARY,
        )
        self.assertEqual(
            session.status,
            StudySession.Status.COMPLETED,
        )
        self.assertIsNone(session.lesson_id)
        self.assertIsNone(session.assessment_session_id)
        self.assertEqual(session.active_seconds, 2)
        self.assertIsNotNone(session.ended_at)

        events = list(
            StudyEvent.objects.order_by(
                "study_event_id"
            ).values_list(
                "event_type",
                "lesson_id",
                "assessment_session_id",
            )
        )

        self.assertEqual(
            events,
            [
                (
                    StudyEvent.EventType.VOCABULARY_REVIEW_STARTED,
                    None,
                    None,
                ),
                (
                    StudyEvent.EventType.VOCABULARY_REVIEW_COMPLETED,
                    None,
                    None,
                ),
            ],
        )

    def test_review_log_snapshot_is_not_changed_by_later_review(self):
        state, _ = start_learning(
            user=self.user,
            item=self.item,
        )
        self._make_due(state)

        first = self._submit(state)
        first_snapshot = {
            "box_before": first.box_before,
            "box_after": first.box_after,
            "state_version_before": first.state_version_before,
            "state_version_after": first.state_version_after,
            "next_review_at": first.next_review_at,
        }

        state.refresh_from_db()
        self._make_due(state)
        self._submit(state)

        first.refresh_from_db()
        self.assertEqual(
            {
                "box_before": first.box_before,
                "box_after": first.box_after,
                "state_version_before": first.state_version_before,
                "state_version_after": first.state_version_after,
                "next_review_at": first.next_review_at,
            },
            first_snapshot,
        )

    def test_vocabulary_review_does_not_change_skill_mastery(self):
        mastery = self._create_mastery()
        before = self._mastery_snapshot(mastery)

        state, _ = start_learning(
            user=self.user,
            item=self.item,
        )
        self._make_due(state)
        self._submit(state)

        self.assertEqual(
            SkillMastery.objects.filter(
                user=self.user,
                skill=self.skill,
            ).count(),
            1,
        )
        self.assertEqual(
            self._mastery_snapshot(mastery),
            before,
        )

    def test_saved_word_add_remove_does_not_start_srs_or_change_mastery(self):
        mastery = self._create_mastery()
        before = self._mastery_snapshot(mastery)
        endpoint = reverse("vocab:saved_toggle")

        response = self.client.post(
            endpoint,
            data=json.dumps({
                "source_id": self.item.source_id,
                "saved": True,
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["saved"])
        self.assertTrue(
            VocabularySavedWord.objects.filter(
                user=self.user,
                item=self.item,
            ).exists()
        )
        self.assertFalse(
            VocabularySRSState.objects.filter(
                user=self.user,
                item=self.item,
            ).exists()
        )
        self.assertEqual(
            self._mastery_snapshot(mastery),
            before,
        )

        response = self.client.post(
            endpoint,
            data=json.dumps({
                "source_id": self.item.source_id,
                "saved": False,
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["saved"])
        self.assertFalse(
            VocabularySavedWord.objects.filter(
                user=self.user,
                item=self.item,
            ).exists()
        )
        self.assertFalse(
            VocabularySRSState.objects.filter(
                user=self.user,
                item=self.item,
            ).exists()
        )
        self.assertEqual(
            self._mastery_snapshot(mastery),
            before,
        )

    def test_vocabulary_study_session_validation_rejects_lesson_target(self):
        lesson = Lesson.objects.create(
            title="Not a vocabulary target",
            lesson_type="grammar",
            content={},
        )
        session = StudySession(
            user=self.user,
            lesson=lesson,
            assessment_session=None,
            activity_type=StudySession.ActivityType.VOCABULARY,
            status=StudySession.Status.COMPLETED,
            ended_at=timezone.now(),
        )

        with self.assertRaises(ValidationError) as ctx:
            session.full_clean()

        self.assertIn("lesson", ctx.exception.message_dict)

    def test_vocabulary_event_validation_requires_vocabulary_session(self):
        lesson = Lesson.objects.create(
            title="Lesson session",
            lesson_type="grammar",
            content={},
        )
        session = StudySession.objects.create(
            user=self.user,
            lesson=lesson,
            assessment_session=None,
            activity_type=StudySession.ActivityType.LESSON,
            status=StudySession.Status.COMPLETED,
            ended_at=timezone.now(),
        )

        event = StudyEvent(
            session=session,
            user=self.user,
            event_type=(
                StudyEvent.EventType.VOCABULARY_REVIEW_STARTED
            ),
            lesson=None,
            assessment_session=None,
        )

        with self.assertRaises(ValidationError) as ctx:
            event.full_clean()

        self.assertIn("session", ctx.exception.message_dict)

    @override_settings(
        VOCABULARY_SRS={
            **TEST_SRS_POLICY,
            "max_new_per_day": 2,
        }
    )
    def test_daily_new_limit_is_enforced_without_resetting_existing_items(self):
        second = self._create_item(
            source_id="stable-item-002",
            lemma="папа",
            meaning_vi="bố",
            display_order=2,
        )
        third = self._create_item(
            source_id="stable-item-003",
            lemma="дом",
            meaning_vi="nhà",
            display_order=3,
        )

        first_state, _ = start_learning(
            user=self.user,
            item=self.item,
        )
        start_learning(user=self.user, item=second)

        existing, created = start_learning(
            user=self.user,
            item=self.item,
        )
        self.assertFalse(created)
        self.assertEqual(existing.pk, first_state.pk)

        with self.assertRaises(VocabularyError) as ctx:
            start_learning(
                user=self.user,
                item=third,
            )

        self.assertEqual(ctx.exception.code, "daily_new_limit")
        self.assertEqual(VocabularySRSState.objects.count(), 2)
