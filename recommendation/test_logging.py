from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from curriculum.models import Lesson, LessonProgress
from learning_engine.models import AssessmentSession
from recommendation.candidates import (
    RecommendationCandidate,
    RecommendationCandidateType,
)
from recommendation.explanations import (
    ExplainedRecommendation,
    RecommendationExplanationReason,
)
from recommendation.features import (
    FEATURE_VERSION,
    RecommendationFeatureVector,
)
from recommendation.logging import (
    RecommendationLoggingResult,
    generate_logged_recommendations,
    log_explained_recommendations,
)
from recommendation.models import RecommendationBatch, RecommendationDecision
from recommendation.ranking import (
    RankedRecommendation,
    RecommendationScoreComponent,
    ScoredRecommendation,
)


class RecommendationLoggingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="r7@example.com",
            password="password123",
            prior_russian_experience=True,
        )
        self.other_user = get_user_model().objects.create_user(
            email="r7-other@example.com",
            password="password123",
            prior_russian_experience=True,
        )
        self.as_of = timezone.now().replace(microsecond=0)

    def _component(
        self,
        feature_name="weakness",
        *,
        value="0.80000",
        configured_weight="0.25000",
        effective_weight="1.00000",
        contribution="0.80000",
    ):
        return RecommendationScoreComponent(
            feature_name=feature_name,
            value=Decimal(value),
            configured_weight=Decimal(configured_weight),
            effective_weight=Decimal(effective_weight),
            contribution=Decimal(contribution),
        )

    def _reason(
        self,
        feature_name="weakness",
        *,
        value="0.80000",
        contribution="0.80000",
    ):
        return RecommendationExplanationReason(
            code="target_skill_needs_strengthening",
            message="Kỹ năng liên quan cần được củng cố.",
            feature_name=feature_name,
            value=Decimal(value),
            contribution=Decimal(contribution),
        )

    def _explained(
        self,
        *,
        rank=1,
        key="lesson:start:1",
        candidate_type=RecommendationCandidateType.START_LESSON,
        score="0.80000",
        scoring_version="recommendation_ranking_v1",
        explanation_version="recommendation_explanation_v1",
        feature_version=FEATURE_VERSION,
        headline="Học bài: Test",
        lesson_id=1,
        lesson_progress_id=None,
        assessment_session_id=None,
        assessment_mode=None,
        vocabulary_due_count=0,
        vocabulary_oldest_due_at=None,
        components=None,
        reasons=None,
    ):
        candidate = RecommendationCandidate(
            candidate_key=key,
            candidate_type=candidate_type,
            lesson_id=lesson_id,
            lesson_progress_id=lesson_progress_id,
            lesson_title="Test",
            lesson_type="grammar" if lesson_id is not None else None,
            lesson_display_order=1 if lesson_id is not None else None,
            assessment_session_id=assessment_session_id,
            assessment_mode=assessment_mode,
            skill_ids=(11, 12) if lesson_id is not None else (),
            vocabulary_due_count=vocabulary_due_count,
            vocabulary_oldest_due_at=vocabulary_oldest_due_at,
        )
        vector = RecommendationFeatureVector(
            candidate=candidate,
            feature_version=feature_version,
            skill_scope_ids=(11, 12) if lesson_id is not None else (),
            weakness=Decimal("0.80000") if lesson_id is not None else None,
            recurring_errors=Decimal("0.20000") if lesson_id is not None else None,
            review_urgency=(
                Decimal("0.90000")
                if candidate_type == RecommendationCandidateType.VOCABULARY_REVIEW
                else Decimal("0.30000") if lesson_id is not None else None
            ),
            uncertainty=Decimal("0.10000") if lesson_id is not None else None,
            recent_learning=Decimal("0.40000") if lesson_id is not None else None,
            prerequisite_readiness=Decimal("1.00000") if lesson_id is not None else None,
            continuity=(
                Decimal("1.00000")
                if candidate_type in {
                    RecommendationCandidateType.CONTINUE_LESSON,
                    RecommendationCandidateType.RESUME_ASSESSMENT,
                }
                else Decimal("0.00000")
            ),
            novelty=(
                Decimal("0.00000")
                if candidate_type == RecommendationCandidateType.VOCABULARY_REVIEW
                else Decimal("1.00000")
            ),
            difficulty_fit=None,
        )
        scored = ScoredRecommendation(
            feature_vector=vector,
            scoring_version=scoring_version,
            score=Decimal(score),
            configured_weight_sum=Decimal("0.25000"),
            components=tuple(components or (self._component(),)),
        )
        ranked = RankedRecommendation(rank=rank, scored=scored)
        return ExplainedRecommendation(
            ranked=ranked,
            explanation_version=explanation_version,
            headline=headline,
            reasons=tuple(reasons or (self._reason(),)),
        )

    def _log(self, rows=None, **kwargs):
        return log_explained_recommendations(
            user=kwargs.pop("user", self.user),
            explained_recommendations=(rows or (self._explained(),)),
            request_id=kwargs.pop("request_id", "req-r7-1"),
            surface=kwargs.pop("surface", "dashboard"),
            context_at=kwargs.pop("context_at", self.as_of),
            **kwargs,
        )

    def test_logs_batch_and_decision(self):
        batch = self._log()

        self.assertEqual(RecommendationBatch.objects.count(), 1)
        self.assertEqual(RecommendationDecision.objects.count(), 1)
        self.assertEqual(batch.user, self.user)
        self.assertEqual(batch.request_id, "req-r7-1")
        self.assertEqual(batch.surface, "dashboard")
        self.assertEqual(batch.item_count, 1)
        self.assertEqual(batch.context_at, self.as_of)
        self.assertEqual(batch.feature_version, FEATURE_VERSION)
        self.assertEqual(batch.scoring_version, "recommendation_ranking_v1")
        self.assertEqual(
            batch.explanation_version,
            "recommendation_explanation_v1",
        )

    def test_persists_rank_score_and_headline(self):
        self._log()
        decision = RecommendationDecision.objects.get()

        self.assertEqual(decision.rank, 1)
        self.assertEqual(decision.score, Decimal("0.80000"))
        self.assertEqual(decision.candidate_key, "lesson:start:1")
        self.assertEqual(decision.candidate_type, "start_lesson")
        self.assertEqual(decision.headline, "Học bài: Test")

    def test_candidate_snapshot_is_historical_and_json_safe(self):
        due_at = self.as_of - timedelta(days=2)
        row = self._explained(
            key="vocabulary:review",
            candidate_type=RecommendationCandidateType.VOCABULARY_REVIEW,
            lesson_id=None,
            vocabulary_due_count=12,
            vocabulary_oldest_due_at=due_at,
            headline="Ôn 12 từ vựng đến hạn",
        )
        self._log(rows=(row,))

        snapshot = RecommendationDecision.objects.get().candidate_snapshot
        self.assertEqual(snapshot["candidate_key"], "vocabulary:review")
        self.assertEqual(snapshot["vocabulary_due_count"], 12)
        self.assertEqual(snapshot["vocabulary_oldest_due_at"], due_at.isoformat())
        self.assertIsInstance(snapshot["skill_ids"], list)

    def test_feature_snapshot_preserves_decimal_text(self):
        self._log()
        snapshot = RecommendationDecision.objects.get().feature_snapshot

        self.assertEqual(snapshot["feature_version"], FEATURE_VERSION)
        self.assertEqual(snapshot["skill_scope_ids"], [11, 12])
        self.assertEqual(snapshot["weakness"], "0.80000")
        self.assertEqual(snapshot["difficulty_fit"], None)

    def test_component_snapshot_preserves_weights_and_contribution(self):
        self._log()
        component = RecommendationDecision.objects.get().score_components[0]

        self.assertEqual(component["feature_name"], "weakness")
        self.assertEqual(component["configured_weight"], "0.25000")
        self.assertEqual(component["effective_weight"], "1.00000")
        self.assertEqual(component["contribution"], "0.80000")

    def test_explanation_reason_snapshot_is_traceable(self):
        self._log()
        reason = RecommendationDecision.objects.get().explanation_reasons[0]

        self.assertEqual(reason["code"], "target_skill_needs_strengthening")
        self.assertEqual(reason["feature_name"], "weakness")
        self.assertEqual(reason["value"], "0.80000")
        self.assertEqual(reason["contribution"], "0.80000")

    def test_multiple_rows_are_persisted_in_rank_order(self):
        rows = (
            self._explained(rank=2, key="lesson:start:2", lesson_id=2),
            self._explained(rank=1, key="lesson:start:1", lesson_id=1),
        )
        batch = self._log(rows=rows)

        decisions = list(batch.decisions.all())
        self.assertEqual([row.rank for row in decisions], [1, 2])
        self.assertEqual(
            [row.candidate_key for row in decisions],
            ["lesson:start:1", "lesson:start:2"],
        )

    def test_same_request_and_payload_is_idempotent(self):
        first = self._log()
        second = self._log(context_at=self.as_of + timedelta(minutes=5))

        self.assertEqual(second.pk, first.pk)
        self.assertEqual(RecommendationBatch.objects.count(), 1)
        self.assertEqual(RecommendationDecision.objects.count(), 1)
        self.assertEqual(second.context_at, self.as_of)

    def test_same_request_with_changed_payload_is_rejected(self):
        self._log()
        changed = self._explained(score="0.70000")

        with self.assertRaisesRegex(ValueError, "different recommendation payload"):
            self._log(rows=(changed,))

    def test_same_request_id_is_allowed_for_different_users(self):
        first = self._log(request_id="shared-request")
        second = self._log(
            user=self.other_user,
            request_id="shared-request",
        )

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(RecommendationBatch.objects.count(), 2)

    def test_empty_batch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            log_explained_recommendations(
                user=self.user,
                explained_recommendations=(),
                request_id="empty",
                context_at=self.as_of,
            )

    def test_unsaved_user_is_rejected(self):
        user = get_user_model()(email="unsaved-r7@example.com")

        with self.assertRaisesRegex(ValueError, "saved"):
            log_explained_recommendations(
                user=user,
                explained_recommendations=(self._explained(),),
                request_id="unsaved",
                context_at=self.as_of,
            )

    def test_naive_context_at_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self._log(context_at=self.as_of.replace(tzinfo=None))

    def test_request_id_must_be_non_empty_and_bounded(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self._log(request_id="   ")
        with self.assertRaisesRegex(ValueError, "128"):
            self._log(request_id="x" * 129)

    def test_surface_must_be_non_empty_and_bounded(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self._log(surface="   ")
        with self.assertRaisesRegex(ValueError, "64"):
            self._log(surface="x" * 65)

    def test_rank_gaps_are_rejected(self):
        rows = (
            self._explained(rank=1, key="lesson:start:1", lesson_id=1),
            self._explained(rank=3, key="lesson:start:3", lesson_id=3),
        )
        with self.assertRaisesRegex(ValueError, "contiguous"):
            self._log(rows=rows)

    def test_duplicate_rank_is_rejected(self):
        rows = (
            self._explained(rank=1, key="lesson:start:1", lesson_id=1),
            self._explained(rank=1, key="lesson:start:2", lesson_id=2),
        )
        with self.assertRaisesRegex(ValueError, "ranks must be unique"):
            self._log(rows=rows)

    def test_duplicate_candidate_key_is_rejected(self):
        rows = (
            self._explained(rank=1, key="same", lesson_id=1),
            self._explained(rank=2, key="same", lesson_id=2),
        )
        with self.assertRaisesRegex(ValueError, "candidate keys must be unique"):
            self._log(rows=rows)

    def test_mixed_scoring_versions_are_rejected(self):
        rows = (
            self._explained(rank=1, key="a", lesson_id=1),
            self._explained(
                rank=2,
                key="b",
                lesson_id=2,
                scoring_version="recommendation_ranking_v2",
            ),
        )
        with self.assertRaisesRegex(ValueError, "one scoring version"):
            self._log(rows=rows)

    def test_mixed_explanation_versions_are_rejected(self):
        rows = (
            self._explained(rank=1, key="a", lesson_id=1),
            self._explained(
                rank=2,
                key="b",
                lesson_id=2,
                explanation_version="recommendation_explanation_v2",
            ),
        )
        with self.assertRaisesRegex(ValueError, "one explanation version"):
            self._log(rows=rows)

    def test_non_current_feature_version_is_rejected(self):
        row = self._explained(feature_version="recommendation_features_old")
        with self.assertRaisesRegex(ValueError, "current feature version"):
            self._log(rows=(row,))

    def test_lesson_progress_must_belong_to_logging_user(self):
        lesson = Lesson.objects.create(
            title="R7 ownership",
            lesson_type="grammar",
            content={},
            display_order=9991,
            is_active=True,
        )
        progress = LessonProgress.objects.create(
            user=self.other_user,
            lesson=lesson,
            status=LessonProgress.Status.IN_PROGRESS,
        )
        row = self._explained(
            key=f"lesson:continue:{lesson.pk}",
            candidate_type=RecommendationCandidateType.CONTINUE_LESSON,
            lesson_id=lesson.pk,
            lesson_progress_id=progress.pk,
        )

        with self.assertRaisesRegex(ValueError, "lesson_progress"):
            self._log(rows=(row,))

    def test_assessment_session_must_belong_to_logging_user(self):
        session = AssessmentSession.objects.create(
            user=self.other_user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )
        row = self._explained(
            key=f"assessment:resume:{session.pk}",
            candidate_type=RecommendationCandidateType.RESUME_ASSESSMENT,
            lesson_id=None,
            assessment_session_id=session.pk,
            assessment_mode=AssessmentSession.Mode.ADAPTIVE,
        )

        with self.assertRaisesRegex(ValueError, "assessment_session"):
            self._log(rows=(row,))

    def test_batch_is_immutable_after_creation(self):
        batch = self._log()
        batch.surface = "changed"

        with self.assertRaisesRegex(ValidationError, "immutable"):
            batch.save()

    def test_decision_is_immutable_after_creation(self):
        self._log()
        decision = RecommendationDecision.objects.get()
        decision.headline = "changed"

        with self.assertRaisesRegex(ValidationError, "immutable"):
            decision.save()

    def test_database_rejects_zero_item_batch(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RecommendationBatch.objects.create(
                    user=self.user,
                    request_id="zero-items",
                    surface="dashboard",
                    feature_version=FEATURE_VERSION,
                    scoring_version="recommendation_ranking_v1",
                    explanation_version="recommendation_explanation_v1",
                    context_at=self.as_of,
                    item_count=0,
                    payload_hash="0" * 64,
                )

    def test_database_rejects_duplicate_user_request(self):
        self._log(request_id="db-duplicate")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RecommendationBatch.objects.create(
                    user=self.user,
                    request_id="db-duplicate",
                    surface="dashboard",
                    feature_version=FEATURE_VERSION,
                    scoring_version="recommendation_ranking_v1",
                    explanation_version="recommendation_explanation_v1",
                    context_at=self.as_of,
                    item_count=1,
                    payload_hash="f" * 64,
                )

    def test_transaction_rolls_back_batch_if_decision_write_fails(self):
        first = self._explained(rank=1, key="a", lesson_id=1)
        second = self._explained(rank=2, key="b", lesson_id=2)
        original_create = RecommendationDecision.objects.create
        calls = {"count": 0}

        def fail_second(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("forced decision failure")
            return original_create(*args, **kwargs)

        with patch.object(
            RecommendationDecision.objects,
            "create",
            side_effect=fail_second,
        ):
            with self.assertRaisesRegex(RuntimeError, "forced"):
                self._log(
                    rows=(first, second),
                    request_id="rollback-request",
                )

        self.assertFalse(
            RecommendationBatch.objects.filter(
                user=self.user,
                request_id="rollback-request",
            ).exists()
        )
        self.assertEqual(RecommendationDecision.objects.count(), 0)

    def test_generate_logged_recommendations_uses_same_context_time(self):
        row = self._explained()

        with patch(
            "recommendation.logging.explain_recommendations",
            return_value=(row,),
        ) as explain:
            result = generate_logged_recommendations(
                user=self.user,
                request_id="pipeline",
                surface="dashboard",
                as_of=self.as_of,
                limit=1,
                max_reasons=2,
            )

        self.assertIsInstance(result, RecommendationLoggingResult)
        self.assertEqual(result.recommendations, (row,))
        self.assertEqual(result.batch.context_at, self.as_of)
        explain.assert_called_once_with(
            user=self.user,
            candidates=None,
            as_of=self.as_of,
            limit=1,
            max_reasons=2,
        )

    def test_generate_logged_recommendations_rejects_empty_display(self):
        with patch(
            "recommendation.logging.explain_recommendations",
            return_value=(),
        ):
            with self.assertRaisesRegex(ValueError, "No eligible"):
                generate_logged_recommendations(
                    user=self.user,
                    request_id="no-display",
                    as_of=self.as_of,
                )

        self.assertEqual(RecommendationBatch.objects.count(), 0)

    def test_logging_does_not_mutate_explained_objects(self):
        row = self._explained()
        before_score = row.score
        before_headline = row.headline
        before_reasons = row.reasons

        self._log(rows=(row,))

        self.assertEqual(row.score, before_score)
        self.assertEqual(row.headline, before_headline)
        self.assertEqual(row.reasons, before_reasons)
