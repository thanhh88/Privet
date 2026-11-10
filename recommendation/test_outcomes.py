from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from curriculum.models import Lesson, LessonProgress, Skill, Topic
from learning_engine.models import AssessmentSession
from recommendation.candidates import RecommendationCandidateType
from recommendation.models import (
    RecommendationBatch,
    RecommendationDecision,
    RecommendationOutcomeEvent,
)
from recommendation.outcomes import (
    OUTCOME_VERSION,
    RecommendationOutcomeStatus,
    get_recommendation_outcome_state,
    record_recommendation_accepted,
    record_recommendation_activity_abandoned,
    record_recommendation_activity_completed,
    record_recommendation_activity_started,
    record_recommendation_dismissed,
)
from vocabulary.models import (
    VocabularyItem,
    VocabularyReviewLog,
    VocabularySRSState,
)


class RecommendationOutcomeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="r8@example.com",
            password="password123",
            prior_russian_experience=True,
        )
        self.other_user = get_user_model().objects.create_user(
            email="r8-other@example.com",
            password="password123",
            prior_russian_experience=True,
        )
        self.topic = Topic.objects.create(
            topic_name="R8 Topic",
            category="grammar",
        )
        self.skill = Skill.objects.create(
            skill_code="R8-SKILL",
            topic=self.topic,
            skill_name="R8 Skill",
            cefr_level=Skill.CefrLevel.A1,
        )
        self.lesson = Lesson.objects.create(
            title="R8 Lesson",
            lesson_type="grammar",
            content={},
            display_order=1,
        )
        self.now = timezone.now().replace(microsecond=0)
        self._batch_counter = 0

    def _decision(
        self,
        *,
        candidate_type=RecommendationCandidateType.START_LESSON,
        user=None,
        candidate_snapshot=None,
        skill_ids=None,
    ):
        user = user or self.user
        self._batch_counter += 1
        batch = RecommendationBatch.objects.create(
            user=user,
            request_id=f"r8-batch-{self._batch_counter}",
            surface="dashboard",
            feature_version="recommendation_features_v1",
            scoring_version="recommendation_ranking_v1",
            explanation_version="recommendation_explanation_v1",
            context_at=self.now,
            item_count=1,
            payload_hash=("a" * 63) + str(self._batch_counter % 10),
        )

        if candidate_snapshot is None:
            candidate_snapshot = {
                "candidate_key": f"r8:{self._batch_counter}",
                "candidate_type": candidate_type.value,
                "lesson_id": self.lesson.pk,
                "lesson_progress_id": None,
                "lesson_title": self.lesson.title,
                "lesson_type": self.lesson.lesson_type,
                "lesson_display_order": self.lesson.display_order,
                "assessment_session_id": None,
                "assessment_mode": None,
                "skill_ids": [self.skill.pk],
                "vocabulary_due_count": 0,
                "vocabulary_oldest_due_at": None,
            }

        return RecommendationDecision.objects.create(
            batch=batch,
            candidate_key=candidate_snapshot["candidate_key"],
            candidate_type=candidate_type.value,
            rank=1,
            score=Decimal("0.80000"),
            headline="R8 recommendation",
            candidate_snapshot=candidate_snapshot,
            feature_snapshot={
                "feature_version": "recommendation_features_v1",
                "skill_scope_ids": list(
                    [self.skill.pk] if skill_ids is None else skill_ids
                ),
            },
            score_components=[],
            explanation_reasons=[],
        )

    def _accept(self, decision, *, request_id="accept-1", at=None):
        return record_recommendation_accepted(
            user=self.user,
            decision=decision,
            request_id=request_id,
            occurred_at=at or self.now,
        )

    def _lesson_progress(self, *, user=None, status=LessonProgress.Status.IN_PROGRESS):
        user = user or self.user
        kwargs = {
            "user": user,
            "lesson": self.lesson,
            "status": status,
            "completion_ratio": Decimal("0.0000"),
            "last_accessed_at": self.now,
        }
        if status == LessonProgress.Status.COMPLETED:
            kwargs["completion_ratio"] = Decimal("1.0000")
            kwargs["completed_at"] = self.now
        return LessonProgress.objects.create(**kwargs)

    def _complete_progress(self, progress, *, at=None):
        at = at or (self.now + timedelta(minutes=10))
        progress.status = LessonProgress.Status.COMPLETED
        progress.completion_ratio = Decimal("1.0000")
        progress.completed_at = at
        progress.last_accessed_at = at
        progress.save(
            update_fields=[
                "status",
                "completion_ratio",
                "completed_at",
                "last_accessed_at",
            ]
        )
        return progress

    def _assessment_decision(self, *, resume=False, session=None):
        candidate_type = (
            RecommendationCandidateType.RESUME_ASSESSMENT
            if resume
            else RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT
        )
        snapshot = {
            "candidate_key": (
                f"assessment:resume:{session.pk}"
                if resume
                else "assessment:start:adaptive"
            ),
            "candidate_type": candidate_type.value,
            "lesson_id": None,
            "lesson_progress_id": None,
            "lesson_title": None,
            "lesson_type": None,
            "lesson_display_order": None,
            "assessment_session_id": session.pk if resume else None,
            "assessment_mode": AssessmentSession.Mode.ADAPTIVE,
            "skill_ids": [],
            "vocabulary_due_count": 0,
            "vocabulary_oldest_due_at": None,
        }
        return self._decision(
            candidate_type=candidate_type,
            candidate_snapshot=snapshot,
        )

    def _vocabulary_decision(self):
        snapshot = {
            "candidate_key": "vocabulary:review",
            "candidate_type": RecommendationCandidateType.VOCABULARY_REVIEW.value,
            "lesson_id": None,
            "lesson_progress_id": None,
            "lesson_title": None,
            "lesson_type": None,
            "lesson_display_order": None,
            "assessment_session_id": None,
            "assessment_mode": None,
            "skill_ids": [],
            "vocabulary_due_count": 5,
            "vocabulary_oldest_due_at": (self.now - timedelta(days=2)).isoformat(),
        }
        return self._decision(
            candidate_type=RecommendationCandidateType.VOCABULARY_REVIEW,
            candidate_snapshot=snapshot,
            skill_ids=[],
        )

    def _vocab_log(self, *, user=None, reviewed_at=None, rating="remembered", response_time_ms=1000):
        user = user or self.user
        item = VocabularyItem.objects.create(
            source_id=f"r8-vocab-{uuid4()}",
            lemma="слово",
            meaning_vi="từ",
            cefr_level="A1",
        )
        state = VocabularySRSState.objects.create(
            user=user,
            item=item,
            box=2,
            version=2,
            lapses=0,
            learned_at=self.now - timedelta(days=10),
            next_review_at=self.now + timedelta(days=3),
            last_reviewed_at=reviewed_at or self.now,
        )
        return VocabularyReviewLog.objects.create(
            request_id=uuid4(),
            user=user,
            item=item,
            state=state,
            rating=rating,
            box_before=1,
            box_after=2 if rating == "remembered" else 1,
            interval_days_applied=3 if rating == "remembered" else 1,
            state_version_before=1,
            state_version_after=2,
            lapses_after=0 if rating == "remembered" else 1,
            reviewed_at=reviewed_at or self.now,
            next_review_at=self.now + timedelta(days=3),
            srs_version="leitner_v1",
            response_time_ms=response_time_ms,
        )

    def test_new_decision_projects_to_shown(self):
        decision = self._decision()
        state = get_recommendation_outcome_state(decision=decision)
        self.assertEqual(state.status, RecommendationOutcomeStatus.SHOWN)
        self.assertIsNone(state.accepted_at)
        self.assertIsNone(state.started_at)
        self.assertIsNone(state.ended_at)

    def test_acceptance_is_append_only_event(self):
        decision = self._decision()
        event = self._accept(decision)
        self.assertEqual(event.event_type, RecommendationOutcomeEvent.EventType.ACCEPTED)
        self.assertEqual(event.outcome_version, OUTCOME_VERSION)
        self.assertEqual(event.user, self.user)
        self.assertEqual(len(event.payload_hash), 64)
        self.assertEqual(event.activity_snapshot, {})

    def test_acceptance_retry_with_same_request_is_idempotent(self):
        decision = self._decision()
        first = self._accept(decision, request_id="same")
        second = self._accept(
            decision,
            request_id="same",
            at=self.now + timedelta(minutes=1),
        )
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(RecommendationOutcomeEvent.objects.count(), 1)
        self.assertEqual(second.occurred_at, self.now)

    def test_same_request_id_cannot_change_event_type(self):
        decision = self._decision()
        self._accept(decision, request_id="shared")
        with self.assertRaisesRegex(ValueError, "different outcome event"):
            record_recommendation_activity_started(
                user=self.user,
                decision=decision,
                request_id="shared",
                lesson_progress=self._lesson_progress(),
                occurred_at=self.now + timedelta(minutes=1),
            )

    def test_duplicate_semantic_event_requires_original_request_id(self):
        decision = self._decision()
        self._accept(decision, request_id="first")
        with self.assertRaisesRegex(ValueError, "original request_id"):
            self._accept(decision, request_id="second")

    def test_dismissed_is_terminal_before_acceptance(self):
        decision = self._decision()
        event = record_recommendation_dismissed(
            user=self.user,
            decision=decision,
            request_id="dismiss",
            occurred_at=self.now,
        )
        self.assertEqual(event.event_type, "dismissed")
        state = get_recommendation_outcome_state(decision=decision)
        self.assertEqual(state.status, RecommendationOutcomeStatus.DISMISSED)
        self.assertEqual(state.ended_at, self.now)
        with self.assertRaisesRegex(ValueError, "first"):
            self._accept(decision, request_id="accept-late")

    def test_activity_start_requires_acceptance(self):
        decision = self._decision()
        with self.assertRaisesRegex(ValueError, "accepted"):
            record_recommendation_activity_started(
                user=self.user,
                decision=decision,
                request_id="start",
                lesson_progress=self._lesson_progress(),
                occurred_at=self.now,
            )

    def test_lesson_activity_start_snapshots_progress_and_mastery(self):
        decision = self._decision()
        self._accept(decision)
        progress = self._lesson_progress()
        event = record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            lesson_progress=progress,
            occurred_at=self.now + timedelta(seconds=1),
        )
        self.assertEqual(event.activity_snapshot["kind"], "lesson")
        self.assertEqual(event.activity_snapshot["lesson_progress_id"], progress.pk)
        self.assertEqual(event.mastery_snapshot[0]["skill_id"], self.skill.pk)
        self.assertEqual(event.mastery_snapshot[0]["observation_count"], 0)
        self.assertIsNone(event.mastery_snapshot[0]["mastery_score"])

    def test_continue_lesson_requires_snapshot_progress_id(self):
        progress = self._lesson_progress()
        snapshot = {
            "candidate_key": f"lesson:continue:{self.lesson.pk}",
            "candidate_type": RecommendationCandidateType.CONTINUE_LESSON.value,
            "lesson_id": self.lesson.pk,
            "lesson_progress_id": progress.pk,
            "lesson_title": self.lesson.title,
            "lesson_type": self.lesson.lesson_type,
            "lesson_display_order": 1,
            "assessment_session_id": None,
            "assessment_mode": None,
            "skill_ids": [self.skill.pk],
            "vocabulary_due_count": 0,
            "vocabulary_oldest_due_at": None,
        }
        decision = self._decision(
            candidate_type=RecommendationCandidateType.CONTINUE_LESSON,
            candidate_snapshot=snapshot,
        )
        self._accept(decision)
        other_lesson = Lesson.objects.create(
            title="Other",
            lesson_type="grammar",
            content={},
            display_order=2,
        )
        other_progress = LessonProgress.objects.create(
            user=self.user,
            lesson=other_lesson,
            status=LessonProgress.Status.IN_PROGRESS,
            completion_ratio=Decimal("0.0"),
            last_accessed_at=self.now,
        )
        with self.assertRaisesRegex(ValueError, "recommended lesson"):
            record_recommendation_activity_started(
                user=self.user,
                decision=decision,
                request_id="wrong-progress",
                lesson_progress=other_progress,
                occurred_at=self.now + timedelta(seconds=1),
            )

    def test_lesson_completion_requires_completed_progress(self):
        decision = self._decision()
        self._accept(decision)
        progress = self._lesson_progress()
        record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            lesson_progress=progress,
            occurred_at=self.now + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ValueError, "must be completed"):
            record_recommendation_activity_completed(
                user=self.user,
                decision=decision,
                request_id="complete",
                lesson_progress=progress,
                occurred_at=self.now + timedelta(minutes=1),
            )

    def test_lesson_completion_persists_result_and_gain_schema(self):
        decision = self._decision()
        self._accept(decision)
        progress = self._lesson_progress()
        record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            lesson_progress=progress,
            occurred_at=self.now + timedelta(seconds=1),
        )
        self._complete_progress(progress)
        event = record_recommendation_activity_completed(
            user=self.user,
            decision=decision,
            request_id="complete",
            lesson_progress=progress,
            occurred_at=self.now + timedelta(minutes=10),
        )
        self.assertEqual(event.result_snapshot["kind"], "lesson")
        self.assertEqual(event.result_snapshot["completion_ratio"], "1.0000")
        self.assertEqual(event.learning_gain_snapshot[0]["skill_id"], self.skill.pk)
        self.assertIsNone(event.learning_gain_snapshot[0]["mastery_delta"])
        self.assertEqual(
            get_recommendation_outcome_state(decision=decision).status,
            RecommendationOutcomeStatus.COMPLETED,
        )

    def test_learning_gain_is_calculated_from_started_and_completed_snapshots(self):
        decision = self._decision()
        self._accept(decision)
        progress = self._lesson_progress()
        before = SimpleNamespace(
            mastery_score=Decimal("0.40000"),
            observation_count=4,
            correct_attempts=2,
            recent_accuracy=Decimal("0.50000"),
            historical_accuracy=Decimal("0.50000"),
            consecutive_errors=1,
            algorithm_version="rule_v3",
            last_evidence_at=self.now - timedelta(days=1),
        )
        after = SimpleNamespace(
            mastery_score=Decimal("0.55000"),
            observation_count=5,
            correct_attempts=3,
            recent_accuracy=Decimal("0.60000"),
            historical_accuracy=Decimal("0.60000"),
            consecutive_errors=0,
            algorithm_version="rule_v3",
            last_evidence_at=self.now + timedelta(minutes=10),
        )
        with patch("recommendation.outcomes.ensure_skill_mastery_current", return_value=before):
            record_recommendation_activity_started(
                user=self.user,
                decision=decision,
                request_id="start",
                lesson_progress=progress,
                occurred_at=self.now + timedelta(seconds=1),
            )
        self._complete_progress(progress)
        with patch("recommendation.outcomes.ensure_skill_mastery_current", return_value=after):
            completed = record_recommendation_activity_completed(
                user=self.user,
                decision=decision,
                request_id="complete",
                lesson_progress=progress,
                occurred_at=self.now + timedelta(minutes=10),
            )
        gain = completed.learning_gain_snapshot[0]
        self.assertEqual(gain["mastery_before"], "0.40000")
        self.assertEqual(gain["mastery_after"], "0.55000")
        self.assertEqual(gain["mastery_delta"], "0.15000")
        self.assertEqual(gain["observation_delta"], 1)

    def test_start_adaptive_assessment_requires_adaptive_mode(self):
        decision = self._assessment_decision()
        self._accept(decision)
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.DIAGNOSTIC,
            status=AssessmentSession.Status.ACTIVE,
        )
        with self.assertRaisesRegex(ValueError, "adaptive mode|mode"):
            record_recommendation_activity_started(
                user=self.user,
                decision=decision,
                request_id="start",
                assessment_session=session,
                occurred_at=self.now + timedelta(seconds=1),
            )

    def test_resume_assessment_requires_original_session(self):
        original = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )
        decision = self._assessment_decision(resume=True, session=original)
        self._accept(decision)
        # Unique active constraint requires finishing the original before making another.
        original.status = AssessmentSession.Status.ABANDONED
        original.ended_at = self.now
        original.save(update_fields=["status", "ended_at"])
        other = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )
        with self.assertRaisesRegex(ValueError, "snapshot"):
            record_recommendation_activity_started(
                user=self.user,
                decision=decision,
                request_id="start",
                assessment_session=other,
                occurred_at=self.now + timedelta(seconds=1),
            )

    def test_assessment_completion_persists_canonical_summary(self):
        decision = self._assessment_decision()
        self._accept(decision)
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )
        record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            assessment_session=session,
            occurred_at=self.now + timedelta(seconds=1),
        )
        session.status = AssessmentSession.Status.COMPLETED
        session.ended_at = self.now + timedelta(minutes=5)
        session.save(update_fields=["status", "ended_at"])
        event = record_recommendation_activity_completed(
            user=self.user,
            decision=decision,
            request_id="complete",
            assessment_session=session,
            occurred_at=self.now + timedelta(minutes=5),
        )
        self.assertEqual(event.result_snapshot["kind"], "assessment")
        self.assertEqual(event.result_snapshot["session_id"], session.pk)
        self.assertEqual(event.result_snapshot["summary"]["question_count"], 0)
        self.assertIsNone(event.result_snapshot["summary"]["evidence_accuracy"])

    def test_assessment_abandonment_is_terminal(self):
        decision = self._assessment_decision()
        self._accept(decision)
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )
        record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            assessment_session=session,
            occurred_at=self.now + timedelta(seconds=1),
        )
        session.status = AssessmentSession.Status.ABANDONED
        session.ended_at = self.now + timedelta(minutes=2)
        session.save(update_fields=["status", "ended_at"])
        event = record_recommendation_activity_abandoned(
            user=self.user,
            decision=decision,
            request_id="abandon",
            assessment_session=session,
            occurred_at=self.now + timedelta(minutes=2),
        )
        self.assertEqual(event.event_type, "activity_abandoned")
        self.assertEqual(
            get_recommendation_outcome_state(decision=decision).status,
            RecommendationOutcomeStatus.ABANDONED,
        )

    def test_non_assessment_cannot_use_abandoned_api(self):
        decision = self._decision()
        self._accept(decision)
        progress = self._lesson_progress()
        record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            lesson_progress=progress,
            occurred_at=self.now + timedelta(seconds=1),
        )
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ABANDONED,
            ended_at=self.now + timedelta(minutes=2),
        )
        with self.assertRaisesRegex(ValueError, "not an assessment"):
            record_recommendation_activity_abandoned(
                user=self.user,
                decision=decision,
                request_id="abandon",
                assessment_session=session,
                occurred_at=self.now + timedelta(minutes=2),
            )

    def test_vocabulary_start_has_no_mastery_scope(self):
        decision = self._vocabulary_decision()
        self._accept(decision)
        with patch(
            "recommendation.outcomes.ensure_skill_mastery_current",
            side_effect=AssertionError("Vocabulary must not touch mastery"),
        ):
            event = record_recommendation_activity_started(
                user=self.user,
                decision=decision,
                request_id="start",
                occurred_at=self.now + timedelta(seconds=1),
            )
        self.assertEqual(event.activity_snapshot["kind"], "vocabulary")
        self.assertEqual(event.activity_snapshot["due_count_at_recommendation"], 5)
        self.assertEqual(event.mastery_snapshot, [])

    def test_vocabulary_completion_requires_review_logs(self):
        decision = self._vocabulary_decision()
        self._accept(decision)
        record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            occurred_at=self.now,
        )
        with self.assertRaisesRegex(ValueError, "at least one"):
            record_recommendation_activity_completed(
                user=self.user,
                decision=decision,
                request_id="complete",
                occurred_at=self.now + timedelta(minutes=5),
            )

    def test_vocabulary_completion_summarizes_srs_logs_without_mastery_gain(self):
        decision = self._vocabulary_decision()
        self._accept(decision)
        start_at = self.now
        record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            occurred_at=start_at,
        )
        first = self._vocab_log(
            reviewed_at=self.now + timedelta(minutes=1),
            rating="remembered",
            response_time_ms=1200,
        )
        second = self._vocab_log(
            reviewed_at=self.now + timedelta(minutes=2),
            rating="forgot",
            response_time_ms=800,
        )
        with patch(
            "recommendation.outcomes.ensure_skill_mastery_current",
            side_effect=AssertionError("Vocabulary must not touch mastery"),
        ):
            event = record_recommendation_activity_completed(
                user=self.user,
                decision=decision,
                request_id="complete",
                occurred_at=self.now + timedelta(minutes=3),
                vocabulary_review_logs=(second, first),
            )
        self.assertEqual(event.result_snapshot["review_count"], 2)
        self.assertEqual(event.result_snapshot["remembered_count"], 1)
        self.assertEqual(event.result_snapshot["forgot_count"], 1)
        self.assertEqual(event.result_snapshot["remembered_rate"], "0.5000")
        self.assertEqual(event.result_snapshot["response_time_ms_total"], 2000)
        self.assertEqual(event.mastery_snapshot, [])
        self.assertEqual(event.learning_gain_snapshot, [])
        self.assertEqual(
            event.activity_snapshot["review_log_ids"],
            [first.pk, second.pk],
        )

    def test_vocabulary_completion_rejects_other_users_log(self):
        decision = self._vocabulary_decision()
        self._accept(decision)
        record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            occurred_at=self.now,
        )
        log = self._vocab_log(
            user=self.other_user,
            reviewed_at=self.now + timedelta(minutes=1),
        )
        with self.assertRaisesRegex(ValueError, "does not belong"):
            record_recommendation_activity_completed(
                user=self.user,
                decision=decision,
                request_id="complete",
                occurred_at=self.now + timedelta(minutes=2),
                vocabulary_review_logs=(log,),
            )

    def test_vocabulary_completion_rejects_log_outside_window(self):
        decision = self._vocabulary_decision()
        self._accept(decision)
        record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            occurred_at=self.now,
        )
        log = self._vocab_log(reviewed_at=self.now - timedelta(minutes=1))
        with self.assertRaisesRegex(ValueError, "outside"):
            record_recommendation_activity_completed(
                user=self.user,
                decision=decision,
                request_id="complete",
                occurred_at=self.now + timedelta(minutes=2),
                vocabulary_review_logs=(log,),
            )

    def test_completion_cannot_precede_start(self):
        decision = self._decision()
        self._accept(decision, at=self.now)
        progress = self._lesson_progress()
        record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            lesson_progress=progress,
            occurred_at=self.now + timedelta(minutes=2),
        )
        self._complete_progress(progress, at=self.now + timedelta(minutes=3))
        with self.assertRaisesRegex(ValueError, "before activity start"):
            record_recommendation_activity_completed(
                user=self.user,
                decision=decision,
                request_id="complete",
                lesson_progress=progress,
                occurred_at=self.now + timedelta(minutes=1),
            )

    def test_wrong_user_cannot_record_outcome(self):
        decision = self._decision()
        with self.assertRaisesRegex(ValueError, "does not belong"):
            record_recommendation_accepted(
                user=self.other_user,
                decision=decision,
                request_id="wrong-user",
                occurred_at=self.now,
            )

    def test_unsaved_user_is_rejected(self):
        decision = self._decision()
        unsaved = get_user_model()(email="unsaved-r8@example.com")
        with self.assertRaisesRegex(ValueError, "saved"):
            record_recommendation_accepted(
                user=unsaved,
                decision=decision,
                request_id="unsaved",
                occurred_at=self.now,
            )

    def test_naive_occurred_at_is_rejected(self):
        decision = self._decision()
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            record_recommendation_accepted(
                user=self.user,
                decision=decision,
                request_id="naive",
                occurred_at=self.now.replace(tzinfo=None),
            )

    def test_blank_and_long_request_ids_are_rejected(self):
        decision = self._decision()
        with self.assertRaisesRegex(ValueError, "non-empty"):
            record_recommendation_accepted(
                user=self.user,
                decision=decision,
                request_id="   ",
                occurred_at=self.now,
            )
        with self.assertRaisesRegex(ValueError, "128"):
            record_recommendation_accepted(
                user=self.user,
                decision=decision,
                request_id="x" * 129,
                occurred_at=self.now,
            )

    def test_outcome_event_is_immutable(self):
        decision = self._decision()
        event = self._accept(decision)
        event.event_type = RecommendationOutcomeEvent.EventType.DISMISSED
        with self.assertRaisesRegex(ValidationError, "immutable"):
            event.save()

    def test_database_rejects_duplicate_event_type(self):
        decision = self._decision()
        self._accept(decision, request_id="one")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RecommendationOutcomeEvent.objects.create(
                    decision=decision,
                    user=self.user,
                    request_id="two",
                    event_type=RecommendationOutcomeEvent.EventType.ACCEPTED,
                    outcome_version=OUTCOME_VERSION,
                    occurred_at=self.now,
                    activity_snapshot={},
                    result_snapshot={},
                    mastery_snapshot=[],
                    learning_gain_snapshot=[],
                    payload_hash="b" * 64,
                )

    def test_database_rejects_duplicate_request_id(self):
        decision = self._decision()
        self._accept(decision, request_id="same")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RecommendationOutcomeEvent.objects.create(
                    decision=decision,
                    user=self.user,
                    request_id="same",
                    event_type=RecommendationOutcomeEvent.EventType.DISMISSED,
                    outcome_version=OUTCOME_VERSION,
                    occurred_at=self.now,
                    activity_snapshot={},
                    result_snapshot={},
                    mastery_snapshot=[],
                    learning_gain_snapshot=[],
                    payload_hash="c" * 64,
                )

    def test_started_state_projection_contains_timestamps(self):
        decision = self._decision()
        accepted_at = self.now
        started_at = self.now + timedelta(seconds=5)
        self._accept(decision, at=accepted_at)
        progress = self._lesson_progress()
        record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            lesson_progress=progress,
            occurred_at=started_at,
        )
        state = get_recommendation_outcome_state(decision=decision)
        self.assertEqual(state.status, RecommendationOutcomeStatus.STARTED)
        self.assertEqual(state.accepted_at, accepted_at)
        self.assertEqual(state.started_at, started_at)
        self.assertIsNone(state.ended_at)

    def test_completed_event_retry_is_idempotent(self):
        decision = self._decision()
        self._accept(decision)
        progress = self._lesson_progress()
        record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            lesson_progress=progress,
            occurred_at=self.now + timedelta(seconds=1),
        )
        self._complete_progress(progress)
        first = record_recommendation_activity_completed(
            user=self.user,
            decision=decision,
            request_id="complete",
            lesson_progress=progress,
            occurred_at=self.now + timedelta(minutes=10),
        )
        second = record_recommendation_activity_completed(
            user=self.user,
            decision=decision,
            request_id="complete",
            lesson_progress=progress,
            occurred_at=self.now + timedelta(minutes=20),
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            RecommendationOutcomeEvent.objects.filter(
                decision=decision,
                event_type=RecommendationOutcomeEvent.EventType.ACTIVITY_COMPLETED,
            ).count(),
            1,
        )

    def test_completion_after_abandonment_is_rejected(self):
        decision = self._assessment_decision()
        self._accept(decision)
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )
        record_recommendation_activity_started(
            user=self.user,
            decision=decision,
            request_id="start",
            assessment_session=session,
            occurred_at=self.now + timedelta(seconds=1),
        )
        session.status = AssessmentSession.Status.ABANDONED
        session.ended_at = self.now + timedelta(minutes=2)
        session.save(update_fields=["status", "ended_at"])
        record_recommendation_activity_abandoned(
            user=self.user,
            decision=decision,
            request_id="abandon",
            assessment_session=session,
            occurred_at=self.now + timedelta(minutes=2),
        )
        session.status = AssessmentSession.Status.COMPLETED
        session.ended_at = self.now + timedelta(minutes=3)
        session.save(update_fields=["status", "ended_at"])
        with self.assertRaisesRegex(ValueError, "Abandoned"):
            record_recommendation_activity_completed(
                user=self.user,
                decision=decision,
                request_id="complete",
                assessment_session=session,
                occurred_at=self.now + timedelta(minutes=3),
            )

    def test_outcome_version_comes_from_settings_policy(self):
        self.assertEqual(OUTCOME_VERSION, "recommendation_outcome_v1")
        decision = self._decision()
        event = self._accept(decision)
        self.assertEqual(event.outcome_version, OUTCOME_VERSION)
