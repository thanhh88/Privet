from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from curriculum.models import Lesson, LessonProgress
from learning_engine.models import AssessmentSession
from recommendation.actions import (
    RecommendationActionError,
    RecommendationActivityKind,
    complete_recommendation_activity,
    open_recommendation_activity,
)
from recommendation.models import (
    RecommendationBatch,
    RecommendationDecision,
    RecommendationOutcomeEvent,
)
from recommendation.outcomes import RecommendationOutcomeStatus
from vocabulary.models import (
    VocabularyItem,
    VocabularyReviewLog,
    VocabularySRSState,
)
from vocabulary.views import receipt


class R9IntegrationBase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="r9@example.com",
            password="password123",
            prior_russian_experience=True,
        )
        self.other_user = get_user_model().objects.create_user(
            email="r9-other@example.com",
            password="password123",
            prior_russian_experience=True,
        )
        self.now = timezone.now()
        self.client.force_login(self.user)

    def _batch(self, *, user=None, request_id=None, surface="dashboard"):
        return RecommendationBatch.objects.create(
            user=user or self.user,
            request_id=request_id or f"r9-batch-{uuid4()}",
            surface=surface,
            feature_version="recommendation_features_v1",
            scoring_version="recommendation_ranking_v1",
            explanation_version="recommendation_explanation_v1",
            context_at=self.now,
            item_count=1,
            payload_hash="a" * 64,
        )

    def _decision(
        self,
        *,
        candidate_type="start_lesson",
        snapshot=None,
        user=None,
        headline="Học bài: R9",
        rank=1,
    ):
        batch = self._batch(user=user)
        snapshot = snapshot or {
            "candidate_key": "lesson:start:1",
            "candidate_type": candidate_type,
            "lesson_id": 1,
            "lesson_progress_id": None,
            "lesson_title": "R9",
            "lesson_type": "grammar",
            "lesson_display_order": 1,
            "assessment_session_id": None,
            "assessment_mode": None,
            "skill_ids": [],
            "vocabulary_due_count": 0,
            "vocabulary_oldest_due_at": None,
        }
        return RecommendationDecision.objects.create(
            batch=batch,
            candidate_key=snapshot["candidate_key"],
            candidate_type=candidate_type,
            rank=rank,
            score=Decimal("0.75000"),
            headline=headline,
            candidate_snapshot=snapshot,
            feature_snapshot={
                "feature_version": "recommendation_features_v1",
                "skill_scope_ids": [],
            },
            score_components=[],
            explanation_reasons=[
                {
                    "code": "available_now",
                    "message": "Hoạt động này hiện khả dụng.",
                    "feature_name": None,
                    "value": None,
                    "contribution": None,
                }
            ],
        )

    def _lesson(self):
        return Lesson.objects.create(
            title="A1 R9 lesson",
            lesson_type="grammar",
            content={
                "objective_vi": "Mục tiêu R9",
                "theory": ["Quy tắc R9"],
                "examples": [{"ru": "Пример.", "vi": "Ví dụ."}],
                "practice": [
                    {
                        "id": "R9-P1",
                        "type": "single_choice",
                        "prompt": "Chọn đáp án",
                        "options": [
                            {"key": "A", "text": "один"},
                            {"key": "B", "text": "два"},
                        ],
                        "correct_option_key": "B",
                        "feedback_vi": "SECRET_FEEDBACK_R9",
                    }
                ],
            },
            display_order=1,
            is_active=True,
        )

    def _lesson_decision(self, lesson, *, user=None):
        return self._decision(
            user=user,
            candidate_type="start_lesson",
            snapshot={
                "candidate_key": f"lesson:start:{lesson.pk}",
                "candidate_type": "start_lesson",
                "lesson_id": lesson.pk,
                "lesson_progress_id": None,
                "lesson_title": lesson.title,
                "lesson_type": lesson.lesson_type,
                "lesson_display_order": lesson.display_order,
                "assessment_session_id": None,
                "assessment_mode": None,
                "skill_ids": [],
                "vocabulary_due_count": 0,
                "vocabulary_oldest_due_at": None,
            },
            headline=f"Học bài: {lesson.title}",
        )

    def _resume_decision(self, session, *, user=None):
        return self._decision(
            user=user,
            candidate_type="resume_assessment",
            snapshot={
                "candidate_key": f"assessment:resume:{session.pk}",
                "candidate_type": "resume_assessment",
                "lesson_id": None,
                "lesson_progress_id": None,
                "lesson_title": None,
                "lesson_type": None,
                "lesson_display_order": None,
                "assessment_session_id": session.pk,
                "assessment_mode": session.mode,
                "skill_ids": [],
                "vocabulary_due_count": 0,
                "vocabulary_oldest_due_at": None,
            },
            headline="Tiếp tục bài đánh giá",
        )

    def _vocabulary_decision(self, *, user=None):
        return self._decision(
            user=user,
            candidate_type="vocabulary_review",
            snapshot={
                "candidate_key": "vocabulary:review",
                "candidate_type": "vocabulary_review",
                "lesson_id": None,
                "lesson_progress_id": None,
                "lesson_title": None,
                "lesson_type": None,
                "lesson_display_order": None,
                "assessment_session_id": None,
                "assessment_mode": None,
                "skill_ids": [],
                "vocabulary_due_count": 3,
                "vocabulary_oldest_due_at": self.now.isoformat(),
            },
            headline="Ôn 3 từ vựng đến hạn",
        )


class RecommendationActionBridgeTests(R9IntegrationBase):
    def test_open_lesson_accepts_starts_and_creates_progress(self):
        lesson = self._lesson()
        decision = self._lesson_decision(lesson)

        result = open_recommendation_activity(
            user=self.user,
            decision=decision,
            request_id="open-lesson",
        )

        self.assertEqual(result.activity_kind, RecommendationActivityKind.LESSON)
        self.assertEqual(result.outcome_status, RecommendationOutcomeStatus.STARTED)
        self.assertEqual(result.lesson_id, lesson.pk)
        progress = LessonProgress.objects.get(pk=result.lesson_progress_id)
        self.assertEqual(progress.status, LessonProgress.Status.IN_PROGRESS)
        self.assertEqual(decision.outcome_events.count(), 2)

    def test_open_lesson_retry_does_not_duplicate_lifecycle(self):
        lesson = self._lesson()
        decision = self._lesson_decision(lesson)
        first = open_recommendation_activity(
            user=self.user, decision=decision, request_id="open-one"
        )
        second = open_recommendation_activity(
            user=self.user, decision=decision, request_id="open-two"
        )
        self.assertEqual(first.lesson_progress_id, second.lesson_progress_id)
        self.assertEqual(decision.outcome_events.count(), 2)
        self.assertEqual(LessonProgress.objects.count(), 1)

    def test_complete_lesson_closes_real_activity_and_outcome(self):
        lesson = self._lesson()
        decision = self._lesson_decision(lesson)
        open_recommendation_activity(
            user=self.user, decision=decision, request_id="open-complete"
        )
        result = complete_recommendation_activity(
            user=self.user, decision=decision, request_id="complete-lesson"
        )
        self.assertEqual(result.outcome_status, RecommendationOutcomeStatus.COMPLETED)
        progress = LessonProgress.objects.get(pk=result.lesson_progress_id)
        self.assertEqual(progress.status, LessonProgress.Status.COMPLETED)
        self.assertEqual(decision.outcome_events.count(), 3)

    def test_open_resume_assessment_records_started_target(self):
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )
        decision = self._resume_decision(session)
        result = open_recommendation_activity(
            user=self.user,
            decision=decision,
            request_id="open-assessment",
        )
        self.assertEqual(result.activity_kind, RecommendationActivityKind.ASSESSMENT)
        self.assertEqual(result.assessment_session_id, session.pk)
        self.assertEqual(decision.outcome_events.count(), 2)

    def test_open_vocabulary_records_started_without_mastery_target(self):
        decision = self._vocabulary_decision()
        result = open_recommendation_activity(
            user=self.user,
            decision=decision,
            request_id="open-vocab",
        )
        self.assertEqual(result.activity_kind, RecommendationActivityKind.VOCABULARY)
        started = decision.outcome_events.get(
            event_type=RecommendationOutcomeEvent.EventType.ACTIVITY_STARTED
        )
        self.assertEqual(started.mastery_snapshot, [])

    def test_dismissed_recommendation_cannot_be_opened(self):
        decision = self._vocabulary_decision()
        RecommendationOutcomeEvent.objects.create(
            decision=decision,
            user=self.user,
            request_id="dismissed-direct",
            event_type=RecommendationOutcomeEvent.EventType.DISMISSED,
            outcome_version="recommendation_outcome_v1",
            occurred_at=self.now,
            activity_snapshot={},
            result_snapshot={},
            mastery_snapshot=[],
            learning_gain_snapshot=[],
            payload_hash="b" * 64,
        )
        with self.assertRaises(RecommendationActionError):
            open_recommendation_activity(
                user=self.user,
                decision=decision,
                request_id="cannot-open",
            )

    def test_other_users_decision_is_rejected(self):
        decision = self._vocabulary_decision(user=self.other_user)
        with self.assertRaises(RecommendationActionError):
            open_recommendation_activity(
                user=self.user,
                decision=decision,
                request_id="wrong-owner",
            )

    def test_request_id_too_long_is_rejected(self):
        decision = self._vocabulary_decision()
        with self.assertRaises(RecommendationActionError):
            open_recommendation_activity(
                user=self.user,
                decision=decision,
                request_id="x" * 97,
            )

    def test_vocabulary_completion_rejects_duplicate_log_ids(self):
        decision = self._vocabulary_decision()
        open_recommendation_activity(
            user=self.user, decision=decision, request_id="open-vocab-dup"
        )
        with self.assertRaises(RecommendationActionError):
            complete_recommendation_activity(
                user=self.user,
                decision=decision,
                request_id="complete-vocab-dup",
                vocabulary_review_log_ids=(1, 1),
            )


class RecommendationAPITests(R9IntegrationBase):
    def test_impression_endpoint_requires_authentication(self):
        self.client.logout()
        response = self.client.post(
            reverse("recommendation:impression-create"),
            data='{"request_id":"x"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)

    def test_impression_endpoint_serializes_persisted_decision(self):
        decision = self._vocabulary_decision()
        result = SimpleNamespace(
            batch=decision.batch,
            recommendations=(SimpleNamespace(rank=1),),
        )
        with patch(
            "recommendation.views.generate_logged_recommendations",
            return_value=result,
        ):
            response = self.client.post(
                reverse("recommendation:impression-create"),
                data='{"request_id":"api-impression","surface":"dashboard"}',
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["recommendations"][0]["decision_id"], decision.pk)
        self.assertEqual(payload["recommendations"][0]["outcome"]["status"], "shown")

    def test_impression_endpoint_returns_empty_when_pipeline_has_none(self):
        with patch(
            "recommendation.views.generate_logged_recommendations",
            side_effect=ValueError("No eligible recommendations were produced; there is no displayed batch to log."),
        ):
            response = self.client.post(
                reverse("recommendation:impression-create"),
                data='{"request_id":"api-empty"}',
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["recommendations"], [])

    def test_open_endpoint_starts_lesson_and_returns_ui_redirect(self):
        lesson = self._lesson()
        decision = self._lesson_decision(lesson)
        response = self.client.post(
            reverse("recommendation:decision-open", args=[decision.pk]),
            data='{"request_id":"api-open-lesson"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["outcome"], "started")
        self.assertIn(f"/learning/lessons/{lesson.pk}/", payload["redirect_url"])

    def test_open_endpoint_hides_other_users_decision_as_404(self):
        decision = self._vocabulary_decision(user=self.other_user)
        response = self.client.post(
            reverse("recommendation:decision-open", args=[decision.pk]),
            data='{"request_id":"wrong"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_state_endpoint_projects_shown_then_started(self):
        decision = self._vocabulary_decision()
        url = reverse("recommendation:decision-state", args=[decision.pk])
        self.assertEqual(self.client.get(url).json()["outcome"]["status"], "shown")
        open_recommendation_activity(
            user=self.user, decision=decision, request_id="state-open"
        )
        self.assertEqual(self.client.get(url).json()["outcome"]["status"], "started")

    def test_dismiss_endpoint_records_explicit_dismissal(self):
        decision = self._vocabulary_decision()
        response = self.client.post(
            reverse("recommendation:decision-dismiss", args=[decision.pk]),
            data='{"request_id":"api-dismiss"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["outcome"]["status"], "dismissed")

    def test_complete_endpoint_completes_started_lesson(self):
        lesson = self._lesson()
        decision = self._lesson_decision(lesson)
        open_recommendation_activity(
            user=self.user, decision=decision, request_id="api-before-complete"
        )
        response = self.client.post(
            reverse("recommendation:decision-complete", args=[decision.pk]),
            data='{"request_id":"api-complete"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["outcome"], "completed")
        self.assertEqual(
            LessonProgress.objects.get(user=self.user, lesson=lesson).status,
            LessonProgress.Status.COMPLETED,
        )

    def test_complete_endpoint_requires_started_activity(self):
        decision = self._vocabulary_decision()
        response = self.client.post(
            reverse("recommendation:decision-complete", args=[decision.pk]),
            data='{"request_id":"too-early","vocabulary_review_log_ids":[]}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)

    def test_open_endpoint_rejects_extra_json_fields(self):
        decision = self._vocabulary_decision()
        response = self.client.post(
            reverse("recommendation:decision-open", args=[decision.pk]),
            data='{"request_id":"x","extra":1}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)


class RecommendationUITests(R9IntegrationBase):
    def _fake_dashboard_result(self, decision):
        return SimpleNamespace(
            batch=decision.batch,
            recommendations=(SimpleNamespace(rank=1),),
        )

    def test_dashboard_renders_logged_recommendation_not_old_demo_lesson(self):
        decision = self._vocabulary_decision()
        with patch(
            "UI.views.generate_logged_recommendations",
            return_value=self._fake_dashboard_result(decision),
        ):
            response = self.client.get(reverse("UI:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ôn 3 từ vựng đến hạn")
        self.assertNotContains(response, "Глаголы движения")

    def test_dashboard_no_recommendation_does_not_crash(self):
        with patch(
            "UI.views.generate_logged_recommendations",
            side_effect=ValueError("No eligible recommendations were produced; there is no displayed batch to log."),
        ):
            response = self.client.get(reverse("UI:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hiện chưa có hoạt động mới cần đề xuất")

    def test_ui_open_lesson_redirects_to_real_recommended_lesson(self):
        lesson = self._lesson()
        decision = self._lesson_decision(lesson)
        response = self.client.post(
            reverse("UI:recommendation_open", args=[decision.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/learning/lessons/{lesson.pk}/", response["Location"])

    def test_recommended_lesson_page_does_not_expose_answer_feedback(self):
        lesson = self._lesson()
        decision = self._lesson_decision(lesson)
        open_recommendation_activity(
            user=self.user, decision=decision, request_id="lesson-page-open"
        )
        response = self.client.get(
            reverse("UI:recommended_lesson", args=[lesson.pk]),
            {"decision": decision.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mục tiêu R9")
        self.assertNotContains(response, "SECRET_FEEDBACK_R9")

    def test_recommended_lesson_page_rejects_mismatched_decision(self):
        lesson = self._lesson()
        other = Lesson.objects.create(
            title="Other", lesson_type="grammar", content={}, display_order=2
        )
        decision = self._lesson_decision(lesson)
        open_recommendation_activity(
            user=self.user, decision=decision, request_id="mismatch-open"
        )
        response = self.client.get(
            reverse("UI:recommended_lesson", args=[other.pk]),
            {"decision": decision.pk},
        )
        self.assertEqual(response.status_code, 404)

    def test_recommended_lesson_completion_records_outcome(self):
        lesson = self._lesson()
        decision = self._lesson_decision(lesson)
        open_recommendation_activity(
            user=self.user, decision=decision, request_id="ui-complete-open"
        )
        response = self.client.post(
            reverse("UI:recommended_lesson_complete", args=[lesson.pk]),
            {"decision": decision.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            decision.outcome_events.filter(
                event_type=RecommendationOutcomeEvent.EventType.ACTIVITY_COMPLETED
            ).exists()
        )

    def test_assessment_session_page_requires_matching_started_decision(self):
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
        )
        decision = self._resume_decision(session)
        response = self.client.get(
            reverse("UI:assessment_session", args=[session.pk]),
            {"decision": decision.pk},
        )
        self.assertEqual(response.status_code, 404)
        open_recommendation_activity(
            user=self.user, decision=decision, request_id="assessment-page-open"
        )
        response = self.client.get(
            reverse("UI:assessment_session", args=[session.pk]),
            {"decision": decision.pk},
        )
        self.assertEqual(response.status_code, 200)

    def test_vocabulary_review_page_requires_started_vocabulary_decision(self):
        decision = self._vocabulary_decision()
        response = self.client.get(
            reverse("UI:vocabulary_review"), {"decision": decision.pk}
        )
        self.assertEqual(response.status_code, 404)
        open_recommendation_activity(
            user=self.user, decision=decision, request_id="vocab-page-open"
        )
        response = self.client.get(
            reverse("UI:vocabulary_review"), {"decision": decision.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "LEITNER SRS")

    def test_ui_dismiss_redirects_and_records_event(self):
        decision = self._vocabulary_decision()
        response = self.client.post(
            reverse("UI:recommendation_dismiss", args=[decision.pk])
        )
        self.assertRedirects(response, reverse("UI:dashboard"), fetch_redirect_response=False)
        self.assertTrue(
            decision.outcome_events.filter(
                event_type=RecommendationOutcomeEvent.EventType.DISMISSED
            ).exists()
        )


class VocabularyReceiptR9Tests(R9IntegrationBase):
    def test_review_receipt_exposes_log_id_for_r8_completion_bridge(self):
        item = VocabularyItem.objects.create(
            source_id="r9-receipt",
            lemma="слово",
            meaning_vi="từ",
            cefr_level="A1",
        )
        state = VocabularySRSState.objects.create(
            user=self.user,
            item=item,
            box=2,
            version=2,
            learned_at=self.now,
            last_reviewed_at=self.now,
            next_review_at=self.now,
        )
        log = VocabularyReviewLog.objects.create(
            request_id=uuid4(),
            user=self.user,
            item=item,
            state=state,
            rating=VocabularyReviewLog.Rating.REMEMBERED,
            box_before=1,
            box_after=2,
            interval_days_applied=3,
            state_version_before=1,
            state_version_after=2,
            lapses_after=0,
            reviewed_at=self.now,
            next_review_at=self.now,
            srs_version="leitner_v1",
            response_time_ms=500,
        )
        self.assertEqual(receipt(log)["review_log_id"], log.pk)
