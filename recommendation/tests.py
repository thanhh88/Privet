from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from curriculum.models import (
    Lesson,
    LessonProgress,
    LessonSkill,
    Skill,
    SkillPrerequisite,
    Topic,
)
from learning_engine.models import AssessmentSession, SkillMastery
from recommendation.candidates import (
    RecommendationCandidate,
    RecommendationCandidateType,
    generate_recommendation_candidates,
)
from recommendation.eligibility import (
    PrerequisiteBlockerReason,
    RecommendationEligibilityReason,
    evaluate_recommendation_eligibility,
    filter_eligible_recommendation_candidates,
)
from recommendation.features import (
    FEATURE_VERSION,
    RecommendationFeatureVector,
    build_recommendation_features,
)
from recommendation.ranking import (
    RankedRecommendation,
    RecommendationScoreComponent,
    ScoredRecommendation,
    rank_recommendation_features,
    rank_recommendations,
    score_recommendation_features,
)
from recommendation.explanations import (
    ExplainedRecommendation,
    RecommendationExplanationReason,
    explain_ranked_recommendations,
    explain_recommendations,
)
from vocabulary.models import (
    VocabularyItem,
    VocabularyItemTopic,
    VocabularySRSState,
    VocabularyTopic,
)


class RecommendationCandidateGenerationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="r2-candidates@example.com",
            password="password123",
        )

        self.topic = Topic.objects.create(
            topic_name="R2 candidate generation",
            category="grammar",
        )

        self.skill_a = self._skill(
            "R2-A",
            "R2 Skill A",
            1,
        )
        self.skill_b = self._skill(
            "R2-B",
            "R2 Skill B",
            2,
        )

        self.as_of = timezone.now()

    def _skill(self, code, name, order, *, is_active=True):
        return Skill.objects.create(
            skill_code=code,
            topic=self.topic,
            skill_name=name,
            cefr_level=Skill.CefrLevel.A1,
            display_order=order,
            is_active=is_active,
        )

    def _lesson(
        self,
        title,
        order,
        *,
        skills=None,
        is_active=True,
    ):
        lesson = Lesson.objects.create(
            title=title,
            lesson_type="grammar",
            content={
                "objective_vi": "R2",
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=order,
            is_active=is_active,
        )

        for skill in skills or []:
            LessonSkill.objects.create(
                lesson=lesson,
                skill=skill,
            )

        return lesson

    def _types(self, candidates):
        return [
            candidate.candidate_type
            for candidate in candidates
        ]

    def _by_key(self, candidates):
        return {
            candidate.candidate_key: candidate
            for candidate in candidates
        }

    def _vocabulary_state(
        self,
        *,
        source_id="r2-vocab-1",
        due_at=None,
        item_active=True,
        topic_active=True,
        extra_active_topic=False,
    ):
        vocabulary_topic = VocabularyTopic.objects.create(
            code=f"{source_id}-topic",
            title_vi="R2 từ vựng",
            title_ru="R2 словарь",
            cefr_level="A1",
            is_active=topic_active,
        )

        item = VocabularyItem.objects.create(
            source_id=source_id,
            lemma="слово",
            meaning_vi="từ",
            cefr_level="A1",
            is_active=item_active,
        )

        VocabularyItemTopic.objects.create(
            item=item,
            topic=vocabulary_topic,
        )

        if extra_active_topic:
            another = VocabularyTopic.objects.create(
                code=f"{source_id}-topic-extra",
                title_vi="R2 thêm",
                title_ru="R2 дополнительный",
                cefr_level="A1",
                is_active=True,
            )
            VocabularyItemTopic.objects.create(
                item=item,
                topic=another,
            )

        return VocabularySRSState.objects.create(
            user=self.user,
            item=item,
            next_review_at=(
                due_at
                if due_at is not None
                else self.as_of - timedelta(days=1)
            ),
        )

    def test_unsaved_user_is_rejected(self):
        user = get_user_model()(email="unsaved@example.com")

        with self.assertRaises(ValueError):
            generate_recommendation_candidates(
                user=user,
                as_of=self.as_of,
            )

    def test_naive_as_of_is_rejected(self):
        naive = self.as_of.replace(tzinfo=None)

        with self.assertRaises(ValueError):
            generate_recommendation_candidates(
                user=self.user,
                as_of=naive,
            )

    def test_empty_learning_data_still_has_raw_adaptive_start(self):
        candidates = generate_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(
            candidate.candidate_type,
            RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT,
        )
        self.assertEqual(
            candidate.candidate_key,
            "assessment:start:adaptive",
        )

    def test_active_mapped_lesson_becomes_start_candidate(self):
        lesson = self._lesson(
            "Start me",
            1,
            skills=[self.skill_a],
        )

        candidates = self._by_key(
            generate_recommendation_candidates(
                user=self.user,
                as_of=self.as_of,
            )
        )

        candidate = candidates[f"lesson:start:{lesson.pk}"]
        self.assertEqual(
            candidate.candidate_type,
            RecommendationCandidateType.START_LESSON,
        )
        self.assertEqual(candidate.lesson_id, lesson.pk)
        self.assertEqual(candidate.skill_ids, (self.skill_a.pk,))

    def test_in_progress_lesson_becomes_continue_not_start(self):
        lesson = self._lesson(
            "Continue me",
            1,
            skills=[self.skill_a],
        )
        progress = LessonProgress.objects.create(
            user=self.user,
            lesson=lesson,
            status=LessonProgress.Status.IN_PROGRESS,
        )

        candidates = self._by_key(
            generate_recommendation_candidates(
                user=self.user,
                as_of=self.as_of,
            )
        )

        self.assertNotIn(f"lesson:start:{lesson.pk}", candidates)
        candidate = candidates[f"lesson:continue:{lesson.pk}"]
        self.assertEqual(candidate.lesson_progress_id, progress.pk)

    def test_completed_lesson_is_not_generated(self):
        lesson = self._lesson(
            "Completed",
            1,
            skills=[self.skill_a],
        )
        LessonProgress.objects.create(
            user=self.user,
            lesson=lesson,
            status=LessonProgress.Status.COMPLETED,
            completion_ratio=1,
            completed_at=self.as_of,
        )

        candidates = generate_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )

        self.assertFalse(
            any(
                candidate.lesson_id == lesson.pk
                for candidate in candidates
            )
        )

    def test_inactive_lesson_is_excluded(self):
        lesson = self._lesson(
            "Inactive lesson",
            1,
            skills=[self.skill_a],
            is_active=False,
        )

        candidates = generate_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )

        self.assertFalse(
            any(candidate.lesson_id == lesson.pk for candidate in candidates)
        )

    def test_unmapped_lesson_is_excluded(self):
        lesson = self._lesson(
            "No skill mapping",
            1,
            skills=[],
        )

        candidates = generate_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )

        self.assertFalse(
            any(candidate.lesson_id == lesson.pk for candidate in candidates)
        )

    def test_lesson_with_any_inactive_mapped_skill_is_excluded(self):
        inactive = self._skill(
            "R2-INACTIVE",
            "Inactive skill",
            3,
            is_active=False,
        )
        lesson = self._lesson(
            "Mixed mapping",
            1,
            skills=[self.skill_a, inactive],
        )

        candidates = generate_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )

        self.assertFalse(
            any(candidate.lesson_id == lesson.pk for candidate in candidates)
        )

    def test_lesson_candidate_carries_sorted_skill_ids(self):
        lesson = self._lesson(
            "Two skills",
            1,
            skills=[self.skill_b, self.skill_a],
        )

        candidates = self._by_key(
            generate_recommendation_candidates(
                user=self.user,
                as_of=self.as_of,
            )
        )

        self.assertEqual(
            candidates[f"lesson:start:{lesson.pk}"].skill_ids,
            tuple(sorted((self.skill_a.pk, self.skill_b.pk))),
        )

    def test_active_adaptive_assessment_generates_resume_candidate(self):
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

        candidates = self._by_key(
            generate_recommendation_candidates(
                user=self.user,
                as_of=self.as_of,
            )
        )

        candidate = candidates[f"assessment:resume:{session.pk}"]
        self.assertEqual(
            candidate.candidate_type,
            RecommendationCandidateType.RESUME_ASSESSMENT,
        )
        self.assertEqual(
            candidate.assessment_mode,
            AssessmentSession.Mode.ADAPTIVE,
        )

    def test_active_diagnostic_assessment_generates_resume_candidate(self):
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.DIAGNOSTIC,
            status=AssessmentSession.Status.ACTIVE,
        )

        candidates = self._by_key(
            generate_recommendation_candidates(
                user=self.user,
                as_of=self.as_of,
            )
        )

        self.assertEqual(
            candidates[
                f"assessment:resume:{session.pk}"
            ].assessment_mode,
            AssessmentSession.Mode.DIAGNOSTIC,
        )

    def test_raw_adaptive_start_is_kept_even_with_active_assessment(self):
        AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

        candidates = self._by_key(
            generate_recommendation_candidates(
                user=self.user,
                as_of=self.as_of,
            )
        )

        self.assertIn("assessment:start:adaptive", candidates)
        self.assertTrue(
            any(
                key.startswith("assessment:resume:")
                for key in candidates
            )
        )

    def test_due_vocabulary_generates_one_aggregate_candidate(self):
        oldest = self._vocabulary_state(
            source_id="r2-vocab-oldest",
            due_at=self.as_of - timedelta(days=3),
        )
        self._vocabulary_state(
            source_id="r2-vocab-newer",
            due_at=self.as_of - timedelta(hours=1),
        )

        candidates = self._by_key(
            generate_recommendation_candidates(
                user=self.user,
                as_of=self.as_of,
            )
        )

        candidate = candidates["vocabulary:review"]
        self.assertEqual(
            candidate.candidate_type,
            RecommendationCandidateType.VOCABULARY_REVIEW,
        )
        self.assertEqual(candidate.vocabulary_due_count, 2)
        self.assertEqual(
            candidate.vocabulary_oldest_due_at,
            oldest.next_review_at,
        )

    def test_not_due_vocabulary_does_not_generate_candidate(self):
        self._vocabulary_state(
            due_at=self.as_of + timedelta(days=1),
        )

        candidates = self._by_key(
            generate_recommendation_candidates(
                user=self.user,
                as_of=self.as_of,
            )
        )

        self.assertNotIn("vocabulary:review", candidates)

    def test_inactive_vocabulary_item_is_not_due_candidate(self):
        self._vocabulary_state(
            source_id="r2-vocab-inactive-item",
            item_active=False,
        )

        candidates = self._by_key(
            generate_recommendation_candidates(
                user=self.user,
                as_of=self.as_of,
            )
        )

        self.assertNotIn("vocabulary:review", candidates)

    def test_vocabulary_item_with_no_active_topic_is_not_candidate(self):
        self._vocabulary_state(
            source_id="r2-vocab-inactive-topic",
            topic_active=False,
        )

        candidates = self._by_key(
            generate_recommendation_candidates(
                user=self.user,
                as_of=self.as_of,
            )
        )

        self.assertNotIn("vocabulary:review", candidates)

    def test_multiple_active_topics_do_not_double_count_due_state(self):
        self._vocabulary_state(
            source_id="r2-vocab-multi-topic",
            extra_active_topic=True,
        )

        candidates = self._by_key(
            generate_recommendation_candidates(
                user=self.user,
                as_of=self.as_of,
            )
        )

        self.assertEqual(
            candidates["vocabulary:review"].vocabulary_due_count,
            1,
        )

    def test_candidate_keys_are_unique_and_output_is_deterministic(self):
        self._lesson("Lesson 2", 2, skills=[self.skill_b])
        self._lesson("Lesson 1", 1, skills=[self.skill_a])
        self._vocabulary_state()

        first = generate_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )
        second = generate_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )

        first_keys = [candidate.candidate_key for candidate in first]
        second_keys = [candidate.candidate_key for candidate in second]

        self.assertEqual(first_keys, sorted(first_keys))
        self.assertEqual(first_keys, second_keys)
        self.assertEqual(len(first_keys), len(set(first_keys)))

    def test_candidate_generation_performs_no_writes(self):
        lesson = self._lesson(
            "No writes",
            1,
            skills=[self.skill_a],
        )
        self._vocabulary_state()

        before_progress = LessonProgress.objects.count()
        before_sessions = AssessmentSession.objects.count()
        before_vocab = VocabularySRSState.objects.count()

        generate_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )

        self.assertEqual(LessonProgress.objects.count(), before_progress)
        self.assertEqual(AssessmentSession.objects.count(), before_sessions)
        self.assertEqual(VocabularySRSState.objects.count(), before_vocab)
        self.assertFalse(
            LessonProgress.objects.filter(
                user=self.user,
                lesson=lesson,
            ).exists()
        )


class RecommendationEligibilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="r3-eligibility@example.com",
            password="password123",
            prior_russian_experience=False,
        )
        self.topic = Topic.objects.create(
            topic_name="R3 eligibility",
            category="grammar",
        )
        self.as_of = timezone.now()

    def _skill(
        self,
        code,
        order,
        *,
        cefr_level=Skill.CefrLevel.A1,
        is_active=True,
    ):
        return Skill.objects.create(
            skill_code=code,
            topic=self.topic,
            skill_name=f"{code} skill",
            cefr_level=cefr_level,
            display_order=order,
            is_active=is_active,
        )

    def _lesson(self, title, order, *, skills):
        lesson = Lesson.objects.create(
            title=title,
            lesson_type="grammar",
            content={
                "objective_vi": "R3",
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=order,
            is_active=True,
        )
        for skill in skills:
            LessonSkill.objects.create(
                lesson=lesson,
                skill=skill,
            )
        return lesson

    def _complete_lesson(self, lesson):
        return LessonProgress.objects.create(
            user=self.user,
            lesson=lesson,
            status=LessonProgress.Status.COMPLETED,
            completion_ratio=Decimal("1.0000"),
            completed_at=self.as_of,
            last_accessed_at=self.as_of,
        )

    def _in_progress_lesson(self, lesson):
        return LessonProgress.objects.create(
            user=self.user,
            lesson=lesson,
            status=LessonProgress.Status.IN_PROGRESS,
            completion_ratio=Decimal("0.5000"),
            last_accessed_at=self.as_of,
        )

    def _mastery(self, skill, score, *, observation_count=3):
        return SkillMastery.objects.create(
            user=self.user,
            skill=skill,
            mastery_score=(
                None if score is None else Decimal(str(score))
            ),
            observation_count=observation_count,
            correct_attempts=0,
            recent_accuracy=(
                None if score is None else Decimal(str(score))
            ),
            historical_accuracy=(
                None if score is None else Decimal(str(score))
            ),
        )

    def _make_adaptive_route(self, *, skills=None):
        skills = list(skills or [self._skill("R3-BASE", 1)])
        beginner_lesson = self._lesson(
            "R3 completed beginner lesson",
            1,
            skills=skills,
        )
        self._complete_lesson(beginner_lesson)
        return beginner_lesson

    def _target_lesson(self, *, skill=None, order=20):
        if skill is None:
            skill = self._skill(
                f"R3-TARGET-{order}",
                order,
                cefr_level=Skill.CefrLevel.A2,
            )
        lesson = self._lesson(
            f"R3 target {order}",
            order,
            skills=[skill],
        )
        return lesson, skill

    def _candidate_by_key(self, key):
        candidates = generate_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )
        return {
            candidate.candidate_key: candidate
            for candidate in candidates
        }[key]

    def _decision_by_key(self, key):
        decisions = evaluate_recommendation_eligibility(
            user=self.user,
            as_of=self.as_of,
        )
        return {
            decision.candidate.candidate_key: decision
            for decision in decisions
        }[key]

    def _due_vocabulary(self, source_id="r3-vocab"):
        topic = VocabularyTopic.objects.create(
            code=f"{source_id}-topic",
            title_vi="R3 từ vựng",
            title_ru="R3 словарь",
            cefr_level="A1",
            is_active=True,
        )
        item = VocabularyItem.objects.create(
            source_id=source_id,
            lemma="слово",
            meaning_vi="từ",
            cefr_level="A1",
            is_active=True,
        )
        VocabularyItemTopic.objects.create(item=item, topic=topic)
        return VocabularySRSState.objects.create(
            user=self.user,
            item=item,
            next_review_at=self.as_of - timedelta(days=1),
        )

    def test_unsaved_user_is_rejected(self):
        user = get_user_model()(email="r3-unsaved@example.com")
        with self.assertRaises(ValueError):
            filter_eligible_recommendation_candidates(user=user)

    def test_duplicate_candidate_keys_are_rejected(self):
        candidate = RecommendationCandidate(
            candidate_key="duplicate",
            candidate_type=(
                RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT
            ),
        )
        with self.assertRaises(ValueError):
            evaluate_recommendation_eligibility(
                user=self.user,
                candidates=(candidate, candidate),
            )

    def test_non_candidate_input_is_rejected(self):
        with self.assertRaises(TypeError):
            evaluate_recommendation_eligibility(
                user=self.user,
                candidates=(object(),),
            )

    def test_unanswered_onboarding_blocks_all_recommendations(self):
        self.user.prior_russian_experience = None
        self.user.save(update_fields=["prior_russian_experience"])
        skill = self._skill("R3-ONBOARD", 1)
        self._lesson("Onboarding blocked lesson", 1, skills=[skill])

        decisions = evaluate_recommendation_eligibility(
            user=self.user,
            as_of=self.as_of,
        )

        self.assertTrue(decisions)
        self.assertTrue(all(not row.is_eligible for row in decisions))
        self.assertEqual(
            {row.reason for row in decisions},
            {RecommendationEligibilityReason.ONBOARDING_REQUIRED},
        )

    def test_start_diagnostic_route_blocks_recommender_actions(self):
        self.user.prior_russian_experience = True
        self.user.save(update_fields=["prior_russian_experience"])
        skill = self._skill("R3-DIAG", 1)
        self._lesson("Diagnostic blocked lesson", 1, skills=[skill])

        decisions = evaluate_recommendation_eligibility(
            user=self.user,
            as_of=self.as_of,
        )

        self.assertTrue(decisions)
        self.assertTrue(all(not row.is_eligible for row in decisions))
        self.assertEqual(
            {row.reason for row in decisions},
            {RecommendationEligibilityReason.DIAGNOSTIC_REQUIRED},
        )

    def test_active_diagnostic_allows_only_matching_resume(self):
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.DIAGNOSTIC,
            status=AssessmentSession.Status.ACTIVE,
        )
        skill = self._skill("R3-ACT-D", 1)
        self._lesson("Active diagnostic lesson", 1, skills=[skill])

        eligible = filter_eligible_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )

        self.assertEqual(len(eligible), 1)
        self.assertEqual(
            eligible[0].candidate_type,
            RecommendationCandidateType.RESUME_ASSESSMENT,
        )
        self.assertEqual(eligible[0].assessment_session_id, session.pk)
        self.assertEqual(eligible[0].assessment_mode, session.mode)

    def test_active_adaptive_allows_only_matching_resume(self):
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )
        self._due_vocabulary("r3-active-a")

        eligible = filter_eligible_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )

        self.assertEqual(len(eligible), 1)
        self.assertEqual(
            eligible[0].candidate_key,
            f"assessment:resume:{session.pk}",
        )

    def test_beginner_allows_only_canonical_next_lesson(self):
        first_skill = self._skill("R3-BEG-1", 1)
        second_skill = self._skill("R3-BEG-2", 2)
        first = self._lesson("Beginner first", 1, skills=[first_skill])
        second = self._lesson("Beginner second", 2, skills=[second_skill])

        eligible = filter_eligible_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )
        keys = {candidate.candidate_key for candidate in eligible}

        self.assertEqual(keys, {f"lesson:start:{first.pk}"})
        self.assertNotIn(f"lesson:start:{second.pk}", keys)
        self.assertNotIn("assessment:start:adaptive", keys)

    def test_beginner_resumes_in_progress_lesson_before_new_lesson(self):
        first_skill = self._skill("R3-BEG-R1", 1)
        second_skill = self._skill("R3-BEG-R2", 2)
        first = self._lesson("Beginner new", 1, skills=[first_skill])
        second = self._lesson("Beginner resume", 2, skills=[second_skill])
        self._in_progress_lesson(second)

        eligible = filter_eligible_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )
        keys = {candidate.candidate_key for candidate in eligible}

        self.assertEqual(keys, {f"lesson:continue:{second.pk}"})
        self.assertNotIn(f"lesson:start:{first.pk}", keys)

    def test_beginner_can_receive_due_vocabulary_alongside_lesson(self):
        skill = self._skill("R3-BEG-V", 1)
        lesson = self._lesson("Beginner vocab lesson", 1, skills=[skill])
        self._due_vocabulary()

        eligible = filter_eligible_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )
        keys = {candidate.candidate_key for candidate in eligible}

        self.assertEqual(
            keys,
            {f"lesson:start:{lesson.pk}", "vocabulary:review"},
        )

    def test_beginner_sequence_ignores_mastery_prerequisite_gate(self):
        prerequisite = self._skill("R3-BEG-PREQ", 1)
        target = self._skill("R3-BEG-TARGET", 2)
        SkillPrerequisite.objects.create(
            skill=target,
            prerequisite_skill=prerequisite,
            required_mastery=Decimal("0.9000"),
        )
        lesson = self._lesson(
            "Beginner prerequisite lesson",
            1,
            skills=[target],
        )

        decision = self._decision_by_key(f"lesson:start:{lesson.pk}")

        self.assertTrue(decision.is_eligible)
        self.assertEqual(
            decision.reason,
            RecommendationEligibilityReason.ELIGIBLE,
        )
        self.assertEqual(decision.prerequisite_blockers, ())

    def test_adaptive_route_allows_start_assessment_when_scope_exists(self):
        base_skill = self._skill("R3-ADAPT-BASE", 1)
        self._make_adaptive_route(skills=[base_skill])

        decision = self._decision_by_key("assessment:start:adaptive")

        self.assertTrue(decision.is_eligible)
        self.assertEqual(
            decision.reason,
            RecommendationEligibilityReason.ELIGIBLE,
        )

    def test_adaptive_route_allows_due_vocabulary(self):
        self._make_adaptive_route()
        self._due_vocabulary("r3-adaptive-vocab")

        decision = self._decision_by_key("vocabulary:review")

        self.assertTrue(decision.is_eligible)

    def test_adaptive_start_lesson_without_prerequisites_is_eligible(self):
        self._make_adaptive_route()
        target_lesson, _ = self._target_lesson(order=21)

        decision = self._decision_by_key(
            f"lesson:start:{target_lesson.pk}"
        )

        self.assertTrue(decision.is_eligible)
        self.assertEqual(decision.prerequisite_blockers, ())

    def test_exact_required_mastery_satisfies_prerequisite(self):
        prerequisite = self._skill("R3-EXACT-P", 1)
        self._make_adaptive_route(skills=[prerequisite])
        target_lesson, target = self._target_lesson(order=22)
        SkillPrerequisite.objects.create(
            skill=target,
            prerequisite_skill=prerequisite,
            required_mastery=Decimal("0.7000"),
        )
        self._mastery(prerequisite, "0.70000")

        decision = self._decision_by_key(
            f"lesson:start:{target_lesson.pk}"
        )

        self.assertTrue(decision.is_eligible)

    def test_missing_prerequisite_mastery_blocks_start_lesson(self):
        prerequisite = self._skill("R3-MISSING-P", 1)
        self._make_adaptive_route(skills=[prerequisite])
        target_lesson, target = self._target_lesson(order=23)
        SkillPrerequisite.objects.create(
            skill=target,
            prerequisite_skill=prerequisite,
            required_mastery=Decimal("0.7000"),
        )

        decision = self._decision_by_key(
            f"lesson:start:{target_lesson.pk}"
        )

        self.assertFalse(decision.is_eligible)
        self.assertEqual(
            decision.reason,
            RecommendationEligibilityReason.PREREQUISITE_NOT_READY,
        )
        self.assertEqual(len(decision.prerequisite_blockers), 1)
        blocker = decision.prerequisite_blockers[0]
        self.assertEqual(
            blocker.reason,
            PrerequisiteBlockerReason.MASTERY_MISSING,
        )
        self.assertIsNone(blocker.actual_mastery)

    def test_null_prerequisite_mastery_blocks_start_lesson(self):
        prerequisite = self._skill("R3-NULL-P", 1)
        self._make_adaptive_route(skills=[prerequisite])
        target_lesson, target = self._target_lesson(order=24)
        SkillPrerequisite.objects.create(
            skill=target,
            prerequisite_skill=prerequisite,
        )
        self._mastery(prerequisite, None, observation_count=0)

        decision = self._decision_by_key(
            f"lesson:start:{target_lesson.pk}"
        )

        self.assertFalse(decision.is_eligible)
        self.assertEqual(
            decision.prerequisite_blockers[0].reason,
            PrerequisiteBlockerReason.MASTERY_MISSING,
        )

    def test_below_threshold_prerequisite_blocks_start_lesson(self):
        prerequisite = self._skill("R3-LOW-P", 1)
        self._make_adaptive_route(skills=[prerequisite])
        target_lesson, target = self._target_lesson(order=25)
        SkillPrerequisite.objects.create(
            skill=target,
            prerequisite_skill=prerequisite,
            required_mastery=Decimal("0.8000"),
        )
        self._mastery(prerequisite, "0.65000")

        decision = self._decision_by_key(
            f"lesson:start:{target_lesson.pk}"
        )
        blocker = decision.prerequisite_blockers[0]

        self.assertFalse(decision.is_eligible)
        self.assertEqual(
            blocker.reason,
            PrerequisiteBlockerReason.MASTERY_BELOW_REQUIRED,
        )
        self.assertEqual(blocker.required_mastery, Decimal("0.8000"))
        self.assertEqual(blocker.actual_mastery, Decimal("0.65000"))

    def test_inactive_prerequisite_skill_blocks_start_lesson(self):
        self._make_adaptive_route()
        prerequisite = self._skill("R3-INACTIVE-P", 10)
        target_lesson, target = self._target_lesson(order=26)
        SkillPrerequisite.objects.create(
            skill=target,
            prerequisite_skill=prerequisite,
        )
        self._mastery(prerequisite, "0.95000")
        prerequisite.is_active = False
        prerequisite.save(update_fields=["is_active"])

        decision = self._decision_by_key(
            f"lesson:start:{target_lesson.pk}"
        )

        self.assertFalse(decision.is_eligible)
        self.assertEqual(
            decision.prerequisite_blockers[0].reason,
            PrerequisiteBlockerReason.PREREQUISITE_INACTIVE,
        )

    def test_inactive_prerequisite_edge_is_ignored(self):
        prerequisite = self._skill("R3-EDGE-OFF-P", 1)
        self._make_adaptive_route(skills=[prerequisite])
        target_lesson, target = self._target_lesson(order=27)
        SkillPrerequisite.objects.create(
            skill=target,
            prerequisite_skill=prerequisite,
            is_active=False,
        )

        decision = self._decision_by_key(
            f"lesson:start:{target_lesson.pk}"
        )

        self.assertTrue(decision.is_eligible)

    def test_transitive_prerequisites_are_enforced(self):
        deep = self._skill("R3-DEEP", 1)
        intermediate = self._skill("R3-MID", 2)
        self._make_adaptive_route(skills=[deep, intermediate])
        target_lesson, target = self._target_lesson(order=28)
        SkillPrerequisite.objects.create(
            skill=target,
            prerequisite_skill=intermediate,
            required_mastery=Decimal("0.6000"),
        )
        SkillPrerequisite.objects.create(
            skill=intermediate,
            prerequisite_skill=deep,
            required_mastery=Decimal("0.8500"),
        )
        self._mastery(intermediate, "0.90000")
        self._mastery(deep, "0.80000")

        decision = self._decision_by_key(
            f"lesson:start:{target_lesson.pk}"
        )

        self.assertFalse(decision.is_eligible)
        self.assertEqual(len(decision.prerequisite_blockers), 1)
        blocker = decision.prerequisite_blockers[0]
        self.assertEqual(blocker.dependent_skill_id, intermediate.pk)
        self.assertEqual(blocker.prerequisite_skill_id, deep.pk)
        self.assertEqual(
            blocker.reason,
            PrerequisiteBlockerReason.MASTERY_BELOW_REQUIRED,
        )

    def test_any_unmet_prerequisite_blocks_multi_skill_lesson(self):
        p1 = self._skill("R3-MULTI-P1", 1)
        p2 = self._skill("R3-MULTI-P2", 2)
        self._make_adaptive_route(skills=[p1, p2])
        target1 = self._skill(
            "R3-MULTI-T1", 30, cefr_level=Skill.CefrLevel.A2
        )
        target2 = self._skill(
            "R3-MULTI-T2", 31, cefr_level=Skill.CefrLevel.A2
        )
        lesson = self._lesson(
            "R3 multi skill target",
            30,
            skills=[target1, target2],
        )
        SkillPrerequisite.objects.create(
            skill=target1,
            prerequisite_skill=p1,
            required_mastery=Decimal("0.7000"),
        )
        SkillPrerequisite.objects.create(
            skill=target2,
            prerequisite_skill=p2,
            required_mastery=Decimal("0.7000"),
        )
        self._mastery(p1, "0.90000")
        self._mastery(p2, "0.50000")

        decision = self._decision_by_key(f"lesson:start:{lesson.pk}")

        self.assertFalse(decision.is_eligible)
        self.assertEqual(len(decision.prerequisite_blockers), 1)
        self.assertEqual(
            decision.prerequisite_blockers[0].prerequisite_skill_id,
            p2.pk,
        )

    def test_continue_lesson_is_not_reblocked_by_new_prerequisite(self):
        prerequisite = self._skill("R3-CONT-P", 1)
        self._make_adaptive_route(skills=[prerequisite])
        target_lesson, target = self._target_lesson(order=32)
        self._in_progress_lesson(target_lesson)
        SkillPrerequisite.objects.create(
            skill=target,
            prerequisite_skill=prerequisite,
            required_mastery=Decimal("0.9900"),
        )

        decision = self._decision_by_key(
            f"lesson:continue:{target_lesson.pk}"
        )

        self.assertTrue(decision.is_eligible)
        self.assertEqual(decision.prerequisite_blockers, ())

    def test_reachable_legacy_cycle_blocks_instead_of_recursing_forever(self):
        prerequisite = self._skill("R3-CYCLE-P", 1)
        self._make_adaptive_route(skills=[prerequisite])
        target_lesson, target = self._target_lesson(order=33)
        # Direct ORM create bypasses model full_clean(), simulating legacy or
        # imported data that predates R1 cycle validation.
        SkillPrerequisite.objects.create(
            skill=target,
            prerequisite_skill=prerequisite,
        )
        SkillPrerequisite.objects.create(
            skill=prerequisite,
            prerequisite_skill=target,
        )
        self._mastery(prerequisite, "0.95000")

        decision = self._decision_by_key(
            f"lesson:start:{target_lesson.pk}"
        )

        self.assertFalse(decision.is_eligible)
        self.assertIn(
            PrerequisiteBlockerReason.GRAPH_CYCLE,
            {row.reason for row in decision.prerequisite_blockers},
        )

    def test_filter_preserves_candidate_generation_order(self):
        self._make_adaptive_route()
        first, _ = self._target_lesson(order=40)
        second, _ = self._target_lesson(order=41)
        self._due_vocabulary("r3-order")

        raw = generate_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )
        eligible = filter_eligible_recommendation_candidates(
            user=self.user,
            candidates=raw,
        )

        expected = tuple(
            candidate.candidate_key
            for candidate in raw
            if candidate.candidate_key in {
                "assessment:start:adaptive",
                f"lesson:start:{first.pk}",
                f"lesson:start:{second.pk}",
                "vocabulary:review",
            }
        )
        self.assertEqual(
            tuple(row.candidate_key for row in eligible),
            expected,
        )

    def test_r3_evaluation_performs_no_writes(self):
        self._make_adaptive_route()
        self._target_lesson(order=42)
        self._due_vocabulary("r3-read-only")

        before = {
            "lessons": Lesson.objects.count(),
            "progress": LessonProgress.objects.count(),
            "sessions": AssessmentSession.objects.count(),
            "masteries": SkillMastery.objects.count(),
            "vocab_states": VocabularySRSState.objects.count(),
        }

        evaluate_recommendation_eligibility(
            user=self.user,
            as_of=self.as_of,
        )

        after = {
            "lessons": Lesson.objects.count(),
            "progress": LessonProgress.objects.count(),
            "sessions": AssessmentSession.objects.count(),
            "masteries": SkillMastery.objects.count(),
            "vocab_states": VocabularySRSState.objects.count(),
        }
        self.assertEqual(after, before)


class RecommendationFeatureCalculationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="r4-features@example.com",
            password="password123",
            prior_russian_experience=False,
        )
        self.topic = Topic.objects.create(
            topic_name="R4 features",
            category="grammar",
        )
        self.as_of = timezone.now()

    def _skill(
        self,
        code,
        order,
        *,
        cefr_level=Skill.CefrLevel.A1,
        is_active=True,
    ):
        return Skill.objects.create(
            skill_code=code,
            topic=self.topic,
            skill_name=f"{code} skill",
            cefr_level=cefr_level,
            display_order=order,
            is_active=is_active,
        )

    def _lesson(self, title, order, *, skills):
        lesson = Lesson.objects.create(
            title=title,
            lesson_type="grammar",
            content={
                "objective_vi": "R4",
                "theory": [],
                "examples": [],
                "practice": [],
            },
            display_order=order,
            is_active=True,
        )
        for skill in skills:
            LessonSkill.objects.create(lesson=lesson, skill=skill)
        return lesson

    def _complete_lesson(self, lesson):
        return LessonProgress.objects.create(
            user=self.user,
            lesson=lesson,
            status=LessonProgress.Status.COMPLETED,
            completion_ratio=Decimal("1.0000"),
            completed_at=self.as_of,
            last_accessed_at=self.as_of,
        )

    def _in_progress_lesson(self, lesson, *, ratio="0.5000"):
        return LessonProgress.objects.create(
            user=self.user,
            lesson=lesson,
            status=LessonProgress.Status.IN_PROGRESS,
            completion_ratio=Decimal(ratio),
            last_accessed_at=self.as_of,
        )

    def _make_adaptive_route(self, *, skills=None):
        skills = list(skills or [self._skill("R4-BASE", 1)])
        lesson = self._lesson(
            "R4 completed beginner lesson",
            1,
            skills=skills,
        )
        self._complete_lesson(lesson)
        return lesson

    def _mastery(self, skill, score, *, observation_count=3):
        return SkillMastery.objects.create(
            user=self.user,
            skill=skill,
            mastery_score=Decimal(str(score)),
            observation_count=observation_count,
            correct_attempts=0,
            recent_accuracy=Decimal(str(score)),
            historical_accuracy=Decimal(str(score)),
        )

    def _due_vocabulary(self, *, source_id="r4-vocab", due_at=None):
        vocabulary_topic = VocabularyTopic.objects.create(
            code=f"{source_id}-topic",
            title_vi="R4 từ vựng",
            title_ru="R4 словарь",
            cefr_level="A1",
            is_active=True,
        )
        item = VocabularyItem.objects.create(
            source_id=source_id,
            lemma="слово",
            meaning_vi="từ",
            cefr_level="A1",
            is_active=True,
        )
        VocabularyItemTopic.objects.create(
            item=item,
            topic=vocabulary_topic,
        )
        return VocabularySRSState.objects.create(
            user=self.user,
            item=item,
            next_review_at=(
                due_at
                if due_at is not None
                else self.as_of - timedelta(days=1)
            ),
        )

    def _candidate_by_key(self, key):
        candidates = generate_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )
        return {
            candidate.candidate_key: candidate
            for candidate in candidates
        }[key]

    def _feature_by_key(self, key, *, candidates=None):
        features = build_recommendation_features(
            user=self.user,
            candidates=candidates,
            as_of=self.as_of,
        )
        return {
            row.candidate.candidate_key: row
            for row in features
        }[key]

    def test_unsaved_user_is_rejected(self):
        user = get_user_model()(email="r4-unsaved@example.com")
        with self.assertRaises(ValueError):
            build_recommendation_features(user=user, as_of=self.as_of)

    def test_naive_as_of_is_rejected(self):
        with self.assertRaises(ValueError):
            build_recommendation_features(
                user=self.user,
                as_of=self.as_of.replace(tzinfo=None),
            )

    def test_non_candidate_input_is_rejected(self):
        with self.assertRaises(TypeError):
            build_recommendation_features(
                user=self.user,
                candidates=(object(),),
                as_of=self.as_of,
            )

    def test_duplicate_candidate_keys_are_rejected(self):
        candidate = RecommendationCandidate(
            candidate_key="duplicate-r4",
            candidate_type=RecommendationCandidateType.START_LESSON,
        )
        with self.assertRaises(ValueError):
            build_recommendation_features(
                user=self.user,
                candidates=(candidate, candidate),
                as_of=self.as_of,
            )

    def test_explicit_ineligible_candidate_is_rejected(self):
        candidate = RecommendationCandidate(
            candidate_key="assessment:start:adaptive",
            candidate_type=(
                RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT
            ),
            assessment_mode=AssessmentSession.Mode.ADAPTIVE,
        )

        with self.assertRaisesRegex(ValueError, "R3-eligible"):
            build_recommendation_features(
                user=self.user,
                candidates=(candidate,),
                as_of=self.as_of,
            )

    def test_default_pipeline_calculates_only_r3_eligible_candidates(self):
        first_skill = self._skill("R4-BEG-1", 1)
        second_skill = self._skill("R4-BEG-2", 2)
        first = self._lesson("R4 beginner first", 1, skills=[first_skill])
        second = self._lesson("R4 beginner second", 2, skills=[second_skill])
        self._due_vocabulary()

        features = build_recommendation_features(
            user=self.user,
            as_of=self.as_of,
        )
        keys = {row.candidate.candidate_key for row in features}

        self.assertEqual(
            keys,
            {f"lesson:start:{first.pk}", "vocabulary:review"},
        )
        self.assertNotIn(f"lesson:start:{second.pk}", keys)
        self.assertNotIn("assessment:start:adaptive", keys)

    def test_feature_vector_is_immutable_and_versioned(self):
        skill = self._skill("R4-VERSION", 1)
        lesson = self._lesson("R4 version lesson", 1, skills=[skill])

        row = self._feature_by_key(f"lesson:start:{lesson.pk}")

        self.assertIsInstance(row, RecommendationFeatureVector)
        self.assertEqual(row.feature_version, FEATURE_VERSION)
        with self.assertRaises(Exception):
            row.continuity = Decimal("0.50000")

    def test_beginner_start_lesson_has_novelty_without_prereq_feature(self):
        skill = self._skill("R4-BEG-N", 1)
        lesson = self._lesson("R4 beginner novelty", 1, skills=[skill])

        row = self._feature_by_key(f"lesson:start:{lesson.pk}")

        self.assertEqual(row.skill_scope_ids, (skill.pk,))
        self.assertEqual(row.continuity, Decimal("0.00000"))
        self.assertEqual(row.novelty, Decimal("1.00000"))
        self.assertIsNone(row.prerequisite_readiness)
        self.assertIsNone(row.difficulty_fit)

    def test_continue_lesson_has_continuity_and_no_novelty(self):
        skill = self._skill("R4-CONT", 1)
        lesson = self._lesson("R4 continue", 1, skills=[skill])
        self._in_progress_lesson(lesson)

        row = self._feature_by_key(f"lesson:continue:{lesson.pk}")

        self.assertEqual(row.continuity, Decimal("1.00000"))
        self.assertEqual(row.novelty, Decimal("0.00000"))
        self.assertIsNone(row.prerequisite_readiness)

    def test_resume_assessment_has_continuity_and_no_novelty(self):
        session = AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.ACTIVE,
        )

        row = self._feature_by_key(f"assessment:resume:{session.pk}")

        self.assertEqual(row.continuity, Decimal("1.00000"))
        self.assertEqual(row.novelty, Decimal("0.00000"))
        self.assertEqual(row.skill_scope_ids, ())
        self.assertIsNone(row.weakness)
        self.assertIsNone(row.review_urgency)

    def test_skill_need_features_are_bounded_for_real_cold_start_state(self):
        skill = self._skill("R4-BOUNDS", 1)
        lesson = self._lesson("R4 bounds", 1, skills=[skill])

        row = self._feature_by_key(f"lesson:start:{lesson.pk}")

        for value in (
            row.weakness,
            row.recurring_errors,
            row.review_urgency,
            row.uncertainty,
            row.recent_learning,
        ):
            self.assertIsNotNone(value)
            self.assertGreaterEqual(value, Decimal("0"))
            self.assertLessEqual(value, Decimal("1"))

    def test_multi_skill_need_features_use_peak_aggregation(self):
        self._make_adaptive_route()
        skill_a = self._skill(
            "R4-PEAK-A",
            20,
            cefr_level=Skill.CefrLevel.A2,
        )
        skill_b = self._skill(
            "R4-PEAK-B",
            21,
            cefr_level=Skill.CefrLevel.A2,
        )
        lesson = self._lesson(
            "R4 peak lesson",
            20,
            skills=[skill_a, skill_b],
        )
        candidate = self._candidate_by_key(f"lesson:start:{lesson.pk}")

        values = {
            skill_a.pk: SimpleNamespace(
                weakness=Decimal("0.20000"),
                recurring_errors=Decimal("0.60000"),
                review_due=Decimal("0.30000"),
                uncertainty=Decimal("0.40000"),
                recent_learning=Decimal("0.00000"),
            ),
            skill_b.pk: SimpleNamespace(
                weakness=Decimal("0.80000"),
                recurring_errors=Decimal("0.10000"),
                review_due=Decimal("0.70000"),
                uncertainty=Decimal("0.90000"),
                recent_learning=Decimal("1.00000"),
            ),
        }

        with patch(
            "recommendation.features.build_user_skill_priority",
            side_effect=lambda **kwargs: values[kwargs["skill_id"]],
        ):
            row = self._feature_by_key(
                candidate.candidate_key,
                candidates=(candidate,),
            )

        self.assertEqual(row.weakness, Decimal("0.80000"))
        self.assertEqual(row.recurring_errors, Decimal("0.60000"))
        self.assertEqual(row.review_urgency, Decimal("0.70000"))
        self.assertEqual(row.uncertainty, Decimal("0.90000"))
        self.assertEqual(row.recent_learning, Decimal("1.00000"))

    def test_adaptive_start_uses_current_adaptive_skill_scope(self):
        base = self._skill("R4-ADAPT-SCOPE", 1)
        self._make_adaptive_route(skills=[base])

        row = self._feature_by_key("assessment:start:adaptive")

        self.assertIn(base.pk, row.skill_scope_ids)
        self.assertIsNotNone(row.weakness)
        self.assertIsNotNone(row.uncertainty)

    def test_first_adaptive_assessment_has_full_novelty(self):
        base = self._skill("R4-ADAPT-FIRST", 1)
        self._make_adaptive_route(skills=[base])

        row = self._feature_by_key("assessment:start:adaptive")

        self.assertEqual(row.novelty, Decimal("1.00000"))
        self.assertEqual(row.continuity, Decimal("0.00000"))

    def test_adaptive_novelty_decreases_after_finished_sessions(self):
        base = self._skill("R4-ADAPT-REPEAT", 1)
        self._make_adaptive_route(skills=[base])
        AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.ADAPTIVE,
            status=AssessmentSession.Status.COMPLETED,
            ended_at=self.as_of - timedelta(days=1),
        )

        row = self._feature_by_key("assessment:start:adaptive")

        self.assertEqual(row.novelty, Decimal("0.50000"))

    def test_adaptive_start_lesson_without_prerequisites_is_fully_ready(self):
        self._make_adaptive_route()
        target = self._skill(
            "R4-READY-NONE",
            20,
            cefr_level=Skill.CefrLevel.A2,
        )
        lesson = self._lesson("R4 ready none", 20, skills=[target])

        row = self._feature_by_key(f"lesson:start:{lesson.pk}")

        self.assertEqual(
            row.prerequisite_readiness,
            Decimal("1.00000"),
        )

    def test_adaptive_prerequisite_readiness_uses_weakest_transitive_mastery(self):
        deep = self._skill("R4-READY-DEEP", 1)
        middle = self._skill("R4-READY-MID", 2)
        self._make_adaptive_route(skills=[deep, middle])
        target = self._skill(
            "R4-READY-TARGET",
            20,
            cefr_level=Skill.CefrLevel.A2,
        )
        lesson = self._lesson("R4 ready target", 20, skills=[target])
        SkillPrerequisite.objects.create(
            skill=target,
            prerequisite_skill=middle,
            required_mastery=Decimal("0.7000"),
        )
        SkillPrerequisite.objects.create(
            skill=middle,
            prerequisite_skill=deep,
            required_mastery=Decimal("0.6000"),
        )
        self._mastery(middle, "0.82000")
        self._mastery(deep, "0.76000")

        row = self._feature_by_key(f"lesson:start:{lesson.pk}")

        self.assertEqual(
            row.prerequisite_readiness,
            Decimal("0.76000"),
        )

    def test_vocabulary_review_uses_srs_urgency_not_skill_features(self):
        self._due_vocabulary(
            source_id="r4-vocab-urgency",
            due_at=self.as_of - timedelta(days=15),
        )

        row = self._feature_by_key("vocabulary:review")

        # Longest configured Leitner interval is 30 days, so 15 days late
        # yields 0.5 urgency and dominates 1/50 backlog pressure.
        self.assertEqual(row.review_urgency, Decimal("0.50000"))
        self.assertEqual(row.skill_scope_ids, ())
        self.assertIsNone(row.weakness)
        self.assertIsNone(row.recurring_errors)
        self.assertIsNone(row.uncertainty)
        self.assertIsNone(row.recent_learning)
        self.assertEqual(row.continuity, Decimal("0.00000"))
        self.assertEqual(row.novelty, Decimal("0.00000"))

    def test_vocabulary_backlog_saturates_review_urgency(self):
        candidate = RecommendationCandidate(
            candidate_key="vocabulary:review",
            candidate_type=RecommendationCandidateType.VOCABULARY_REVIEW,
            vocabulary_due_count=50,
            vocabulary_oldest_due_at=self.as_of,
        )

        row = self._feature_by_key(
            candidate.candidate_key,
            candidates=(candidate,),
        )

        self.assertEqual(row.review_urgency, Decimal("1.00000"))

    def test_invalid_vocabulary_due_metadata_is_rejected(self):
        candidate = RecommendationCandidate(
            candidate_key="vocabulary:review",
            candidate_type=RecommendationCandidateType.VOCABULARY_REVIEW,
            vocabulary_due_count=1,
            vocabulary_oldest_due_at=self.as_of + timedelta(days=1),
        )

        with self.assertRaisesRegex(ValueError, "future"):
            build_recommendation_features(
                user=self.user,
                candidates=(candidate,),
                as_of=self.as_of,
            )

    def test_explicit_candidate_order_is_preserved(self):
        skill = self._skill("R4-ORDER", 1)
        lesson = self._lesson("R4 order", 1, skills=[skill])
        self._due_vocabulary(source_id="r4-order-vocab")
        eligible = filter_eligible_recommendation_candidates(
            user=self.user,
            as_of=self.as_of,
        )
        by_key = {row.candidate_key: row for row in eligible}
        supplied = (
            by_key["vocabulary:review"],
            by_key[f"lesson:start:{lesson.pk}"],
        )

        features = build_recommendation_features(
            user=self.user,
            candidates=supplied,
            as_of=self.as_of,
        )

        self.assertEqual(
            tuple(row.candidate.candidate_key for row in features),
            tuple(row.candidate_key for row in supplied),
        )

    def test_r4_feature_calculation_performs_no_writes(self):
        skill = self._skill("R4-READONLY", 1)
        self._lesson("R4 read only", 1, skills=[skill])
        self._due_vocabulary(source_id="r4-readonly-vocab")

        before = {
            "lessons": Lesson.objects.count(),
            "progress": LessonProgress.objects.count(),
            "sessions": AssessmentSession.objects.count(),
            "masteries": SkillMastery.objects.count(),
            "vocab_states": VocabularySRSState.objects.count(),
        }

        build_recommendation_features(
            user=self.user,
            as_of=self.as_of,
        )

        after = {
            "lessons": Lesson.objects.count(),
            "progress": LessonProgress.objects.count(),
            "sessions": AssessmentSession.objects.count(),
            "masteries": SkillMastery.objects.count(),
            "vocab_states": VocabularySRSState.objects.count(),
        }
        self.assertEqual(after, before)


class RecommendationRankingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="r5-ranking@example.com",
            password="password123",
        )
        self.as_of = timezone.now()

    def _candidate(
        self,
        key,
        candidate_type=RecommendationCandidateType.START_LESSON,
        *,
        lesson_id=None,
        display_order=None,
        session_id=None,
    ):
        return RecommendationCandidate(
            candidate_key=key,
            candidate_type=candidate_type,
            lesson_id=lesson_id,
            lesson_display_order=display_order,
            assessment_session_id=session_id,
        )

    def _vector(
        self,
        key,
        candidate_type=RecommendationCandidateType.START_LESSON,
        *,
        lesson_id=None,
        display_order=None,
        session_id=None,
        feature_version=FEATURE_VERSION,
        weakness=None,
        recurring_errors=None,
        review_urgency=None,
        uncertainty=None,
        recent_learning=None,
        prerequisite_readiness=None,
        continuity=Decimal("0"),
        novelty=Decimal("0"),
        difficulty_fit=None,
    ):
        return RecommendationFeatureVector(
            candidate=self._candidate(
                key,
                candidate_type,
                lesson_id=lesson_id,
                display_order=display_order,
                session_id=session_id,
            ),
            feature_version=feature_version,
            skill_scope_ids=(),
            weakness=weakness,
            recurring_errors=recurring_errors,
            review_urgency=review_urgency,
            uncertainty=uncertainty,
            recent_learning=recent_learning,
            prerequisite_readiness=prerequisite_readiness,
            continuity=continuity,
            novelty=novelty,
            difficulty_fit=difficulty_fit,
        )

    def test_score_uses_versioned_default_policy(self):
        row = self._vector(
            "r5:score",
            weakness=Decimal("1"),
            recurring_errors=Decimal("0"),
            review_urgency=Decimal("0.5"),
            continuity=Decimal("0"),
            novelty=Decimal("1"),
        )

        scored = score_recommendation_features(
            feature_vectors=(row,),
        )[0]

        self.assertEqual(scored.scoring_version, "recommendation_ranking_v1")
        # numerator = .25 + .20*.5 + .03 = .38
        # denominator = .25 + .15 + .20 + .07 + .03 = .70
        self.assertEqual(scored.score, Decimal("0.54286"))
        self.assertEqual(
            scored.configured_weight_sum,
            Decimal("0.70000"),
        )

    def test_none_features_are_excluded_from_denominator(self):
        row = self._vector(
            "r5:none",
            candidate_type=RecommendationCandidateType.VOCABULARY_REVIEW,
            review_urgency=Decimal("1"),
            continuity=Decimal("0"),
            novelty=Decimal("0"),
        )

        scored = score_recommendation_features(
            feature_vectors=(row,),
        )[0]

        self.assertEqual(
            scored.configured_weight_sum,
            Decimal("0.30000"),
        )
        self.assertEqual(scored.score, Decimal("0.66667"))

    def test_real_zero_feature_remains_in_denominator(self):
        with_zero = self._vector(
            "r5:zero",
            weakness=Decimal("0"),
            review_urgency=Decimal("1"),
            continuity=Decimal("0"),
            novelty=Decimal("0"),
        )
        without_weakness = self._vector(
            "r5:none-weakness",
            weakness=None,
            review_urgency=Decimal("1"),
            continuity=Decimal("0"),
            novelty=Decimal("0"),
        )

        scored_zero = score_recommendation_features(
            feature_vectors=(with_zero,),
        )[0]
        scored_none = score_recommendation_features(
            feature_vectors=(without_weakness,),
        )[0]

        self.assertLess(scored_zero.score, scored_none.score)
        self.assertEqual(scored_zero.score, Decimal("0.36364"))
        self.assertEqual(scored_none.score, Decimal("0.66667"))

    def test_zero_weight_difficulty_is_ignored_even_when_present(self):
        base = self._vector(
            "r5:difficulty-a",
            review_urgency=Decimal("0.5"),
            continuity=Decimal("0"),
            novelty=Decimal("0"),
            difficulty_fit=Decimal("0"),
        )
        changed = self._vector(
            "r5:difficulty-b",
            review_urgency=Decimal("0.5"),
            continuity=Decimal("0"),
            novelty=Decimal("0"),
            difficulty_fit=Decimal("1"),
        )

        rows = score_recommendation_features(
            feature_vectors=(base, changed),
        )

        self.assertEqual(rows[0].score, rows[1].score)
        for row in rows:
            self.assertNotIn(
                "difficulty_fit",
                {component.feature_name for component in row.components},
            )

    def test_score_components_expose_effective_contributions(self):
        row = self._vector(
            "r5:components",
            review_urgency=Decimal("1"),
            continuity=Decimal("0"),
            novelty=Decimal("0"),
        )

        scored = score_recommendation_features(
            feature_vectors=(row,),
        )[0]
        components = {
            component.feature_name: component
            for component in scored.components
        }

        review = components["review_urgency"]
        self.assertIsInstance(review, RecommendationScoreComponent)
        self.assertEqual(review.value, Decimal("1.00000"))
        self.assertEqual(review.configured_weight, Decimal("0.20000"))
        self.assertEqual(review.effective_weight, Decimal("0.66667"))
        self.assertEqual(review.contribution, Decimal("0.66667"))

    def test_invalid_feature_value_is_rejected(self):
        row = self._vector(
            "r5:bad-value",
            weakness=Decimal("1.01"),
        )

        with self.assertRaisesRegex(ValueError, r"\[0, 1\]"):
            score_recommendation_features(feature_vectors=(row,))

    def test_wrong_feature_version_is_rejected(self):
        row = self._vector(
            "r5:old-feature-version",
            feature_version="recommendation_features_old",
        )

        with self.assertRaisesRegex(ValueError, "current R4 feature version"):
            score_recommendation_features(feature_vectors=(row,))

    def test_duplicate_candidate_keys_are_rejected(self):
        one = self._vector("r5:duplicate", weakness=Decimal("0.2"))
        two = self._vector("r5:duplicate", weakness=Decimal("0.9"))

        with self.assertRaisesRegex(ValueError, "unique"):
            score_recommendation_features(feature_vectors=(one, two))

    def test_non_feature_vector_is_rejected(self):
        with self.assertRaises(TypeError):
            score_recommendation_features(feature_vectors=(object(),))

    def test_empty_feature_set_returns_empty_ranking(self):
        self.assertEqual(
            rank_recommendation_features(feature_vectors=()),
            (),
        )

    def test_default_top_n_is_three(self):
        rows = tuple(
            self._vector(
                f"r5:top:{index}",
                weakness=Decimal(str(value)),
            )
            for index, value in enumerate(("0.1", "0.2", "0.3", "0.4", "0.5"))
        )

        ranked = rank_recommendation_features(feature_vectors=rows)

        self.assertEqual(len(ranked), 3)
        self.assertEqual([row.rank for row in ranked], [1, 2, 3])

    def test_explicit_limit_is_respected(self):
        rows = tuple(
            self._vector(
                f"r5:limit:{index}",
                weakness=Decimal(str(value)),
            )
            for index, value in enumerate(("0.1", "0.2", "0.3"))
        )

        ranked = rank_recommendation_features(
            feature_vectors=rows,
            limit=2,
        )

        self.assertEqual(len(ranked), 2)

    def test_invalid_limit_is_rejected(self):
        row = self._vector("r5:limit-invalid", weakness=Decimal("0.5"))

        for value in (0, -1, True, "2"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    rank_recommendation_features(
                        feature_vectors=(row,),
                        limit=value,
                    )

    def test_higher_score_is_primary_order(self):
        low = self._vector(
            "r5:low",
            candidate_type=RecommendationCandidateType.RESUME_ASSESSMENT,
            weakness=Decimal("0.1"),
            continuity=Decimal("0"),
        )
        high = self._vector(
            "r5:high",
            candidate_type=RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT,
            weakness=Decimal("0.9"),
            continuity=Decimal("0"),
        )

        ranked = rank_recommendation_features(
            feature_vectors=(low, high),
            limit=2,
        )

        self.assertEqual(ranked[0].candidate.candidate_key, "r5:high")
        self.assertGreater(ranked[0].score, ranked[1].score)

    def test_exact_score_tie_prefers_ongoing_work_type(self):
        resume = self._vector(
            "r5:resume",
            candidate_type=RecommendationCandidateType.RESUME_ASSESSMENT,
            weakness=Decimal("0.5"),
        )
        start = self._vector(
            "r5:start",
            candidate_type=RecommendationCandidateType.START_LESSON,
            weakness=Decimal("0.5"),
        )

        ranked = rank_recommendation_features(
            feature_vectors=(start, resume),
            limit=2,
        )

        self.assertEqual(ranked[0].candidate.candidate_key, "r5:resume")
        self.assertEqual(ranked[0].score, ranked[1].score)

    def test_equal_lesson_score_uses_display_order_tie_break(self):
        later = self._vector(
            "lesson:start:20",
            lesson_id=20,
            display_order=20,
            weakness=Decimal("0.5"),
        )
        earlier = self._vector(
            "lesson:start:10",
            lesson_id=10,
            display_order=10,
            weakness=Decimal("0.5"),
        )

        ranked = rank_recommendation_features(
            feature_vectors=(later, earlier),
            limit=2,
        )

        self.assertEqual(ranked[0].candidate.lesson_id, 10)

    def test_final_tie_break_is_candidate_key(self):
        zed = self._vector(
            "assessment:start:z",
            candidate_type=RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT,
            weakness=Decimal("0.5"),
        )
        alpha = self._vector(
            "assessment:start:a",
            candidate_type=RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT,
            weakness=Decimal("0.5"),
        )

        ranked = rank_recommendation_features(
            feature_vectors=(zed, alpha),
            limit=2,
        )

        self.assertEqual(
            [row.candidate.candidate_key for row in ranked],
            ["assessment:start:a", "assessment:start:z"],
        )

    def test_ranking_is_deterministic_across_input_order(self):
        rows = (
            self._vector("r5:c", weakness=Decimal("0.5"), display_order=3),
            self._vector("r5:a", weakness=Decimal("0.5"), display_order=1),
            self._vector("r5:b", weakness=Decimal("0.5"), display_order=2),
        )

        first = rank_recommendation_features(
            feature_vectors=rows,
            limit=3,
        )
        second = rank_recommendation_features(
            feature_vectors=tuple(reversed(rows)),
            limit=3,
        )

        self.assertEqual(
            [row.candidate.candidate_key for row in first],
            [row.candidate.candidate_key for row in second],
        )

    @override_settings(
        RECOMMENDATION_RANKING={
            "version": "bad",
            "default_top_n": 3,
            "weights": {"weakness": "1"},
        }
    )
    def test_policy_requires_exact_feature_schema(self):
        row = self._vector("r5:bad-schema", weakness=Decimal("0.5"))

        with self.assertRaisesRegex(ValueError, "exactly the R4 feature schema"):
            score_recommendation_features(feature_vectors=(row,))

    def test_negative_policy_weight_is_rejected(self):
        from django.conf import settings

        weights = dict(settings.RECOMMENDATION_RANKING["weights"])
        weights["weakness"] = "-0.01"
        policy = dict(settings.RECOMMENDATION_RANKING)
        policy["weights"] = weights

        with override_settings(RECOMMENDATION_RANKING=policy):
            row = self._vector("r5:negative-weight", weakness=Decimal("0.5"))
            with self.assertRaisesRegex(ValueError, "greater than or equal"):
                score_recommendation_features(feature_vectors=(row,))

    def test_policy_requires_at_least_one_positive_weight(self):
        from django.conf import settings

        policy = dict(settings.RECOMMENDATION_RANKING)
        policy["weights"] = {
            name: "0"
            for name in settings.RECOMMENDATION_RANKING["weights"]
        }

        with override_settings(RECOMMENDATION_RANKING=policy):
            row = self._vector("r5:zero-policy", weakness=Decimal("0.5"))
            with self.assertRaisesRegex(ValueError, "At least one"):
                score_recommendation_features(feature_vectors=(row,))

    def test_candidate_without_any_positive_weight_applicable_is_rejected(self):
        from django.conf import settings

        policy = dict(settings.RECOMMENDATION_RANKING)
        weights = {
            name: "0"
            for name in settings.RECOMMENDATION_RANKING["weights"]
        }
        weights["difficulty_fit"] = "1"
        policy["weights"] = weights

        with override_settings(RECOMMENDATION_RANKING=policy):
            row = self._vector(
                "r5:no-applicable",
                difficulty_fit=None,
            )
            with self.assertRaisesRegex(ValueError, "no applicable feature"):
                score_recommendation_features(feature_vectors=(row,))

    def test_pipeline_uses_r4_features_and_propagates_candidates(self):
        candidate = self._candidate("r5:pipeline")
        feature = self._vector(
            "r5:pipeline",
            weakness=Decimal("0.8"),
        )

        with patch(
            "recommendation.ranking.build_recommendation_features",
            return_value=(feature,),
        ) as build:
            ranked = rank_recommendations(
                user=self.user,
                candidates=(candidate,),
                as_of=self.as_of,
                limit=1,
            )

        build.assert_called_once_with(
            user=self.user,
            candidates=(candidate,),
            as_of=self.as_of,
        )
        self.assertEqual(len(ranked), 1)
        self.assertIsInstance(ranked[0], RankedRecommendation)
        self.assertEqual(ranked[0].candidate.candidate_key, "r5:pipeline")

    def test_scoring_version_is_propagated_to_ranked_result(self):
        row = self._vector("r5:version", weakness=Decimal("0.5"))

        ranked = rank_recommendation_features(
            feature_vectors=(row,),
            limit=1,
        )[0]

        self.assertEqual(ranked.scoring_version, "recommendation_ranking_v1")

    def test_scored_and_ranked_outputs_are_immutable(self):
        row = self._vector("r5:immutable", weakness=Decimal("0.5"))
        scored = score_recommendation_features(feature_vectors=(row,))[0]
        ranked = rank_recommendation_features(
            feature_vectors=(row,),
            limit=1,
        )[0]

        with self.assertRaises(Exception):
            scored.score = Decimal("0")
        with self.assertRaises(Exception):
            ranked.rank = 99


class RecommendationExplanationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="r6-explanation@example.com",
            password="password123",
        )
        self.as_of = timezone.now()

    def _candidate(
        self,
        key="r6:candidate",
        candidate_type=RecommendationCandidateType.START_LESSON,
        *,
        lesson_title=None,
        assessment_mode=None,
        vocabulary_due_count=0,
    ):
        return RecommendationCandidate(
            candidate_key=key,
            candidate_type=candidate_type,
            lesson_title=lesson_title,
            assessment_mode=assessment_mode,
            vocabulary_due_count=vocabulary_due_count,
        )

    def _ranked(
        self,
        *,
        rank=1,
        key="r6:candidate",
        candidate_type=RecommendationCandidateType.START_LESSON,
        lesson_title=None,
        assessment_mode=None,
        vocabulary_due_count=0,
        components=None,
        score=Decimal("0.50000"),
    ):
        candidate = self._candidate(
            key,
            candidate_type,
            lesson_title=lesson_title,
            assessment_mode=assessment_mode,
            vocabulary_due_count=vocabulary_due_count,
        )
        vector = RecommendationFeatureVector(
            candidate=candidate,
            feature_version=FEATURE_VERSION,
            skill_scope_ids=(),
            weakness=None,
            recurring_errors=None,
            review_urgency=None,
            uncertainty=None,
            recent_learning=None,
            prerequisite_readiness=None,
            continuity=Decimal("0"),
            novelty=Decimal("0"),
            difficulty_fit=None,
        )
        scored = ScoredRecommendation(
            feature_vector=vector,
            scoring_version="recommendation_ranking_v1",
            score=score,
            configured_weight_sum=Decimal("1.00000"),
            components=tuple(components or ()),
        )
        return RankedRecommendation(rank=rank, scored=scored)

    def _component(
        self,
        feature_name,
        *,
        value="0.8",
        contribution="0.2",
        configured_weight="0.25",
        effective_weight="0.25",
    ):
        return RecommendationScoreComponent(
            feature_name=feature_name,
            value=Decimal(value),
            configured_weight=Decimal(configured_weight),
            effective_weight=Decimal(effective_weight),
            contribution=Decimal(contribution),
        )

    def test_start_lesson_headline_uses_lesson_title(self):
        row = self._ranked(lesson_title="Родительный падеж")

        explained = explain_ranked_recommendations(
            ranked_recommendations=(row,)
        )[0]

        self.assertEqual(
            explained.headline,
            "Học bài: Родительный падеж",
        )

    def test_continue_lesson_headline_uses_lesson_title(self):
        row = self._ranked(
            candidate_type=RecommendationCandidateType.CONTINUE_LESSON,
            lesson_title="Глаголы движения",
        )

        explained = explain_ranked_recommendations(
            ranked_recommendations=(row,)
        )[0]

        self.assertEqual(
            explained.headline,
            "Tiếp tục bài học: Глаголы движения",
        )

    def test_diagnostic_resume_has_specific_headline(self):
        row = self._ranked(
            candidate_type=RecommendationCandidateType.RESUME_ASSESSMENT,
            assessment_mode=AssessmentSession.Mode.DIAGNOSTIC,
        )

        explained = explain_ranked_recommendations(
            ranked_recommendations=(row,)
        )[0]

        self.assertEqual(
            explained.headline,
            "Tiếp tục bài đánh giá đầu vào",
        )

    def test_adaptive_start_has_specific_headline(self):
        row = self._ranked(
            candidate_type=(
                RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT
            ),
        )

        explained = explain_ranked_recommendations(
            ranked_recommendations=(row,)
        )[0]

        self.assertEqual(
            explained.headline,
            "Bắt đầu bài đánh giá thích ứng",
        )

    def test_vocabulary_headline_uses_due_count(self):
        row = self._ranked(
            candidate_type=RecommendationCandidateType.VOCABULARY_REVIEW,
            vocabulary_due_count=12,
        )

        explained = explain_ranked_recommendations(
            ranked_recommendations=(row,)
        )[0]

        self.assertEqual(explained.headline, "Ôn 12 từ vựng đến hạn")

    def test_reasons_are_selected_by_r5_contribution(self):
        row = self._ranked(
            components=(
                self._component(
                    "weakness",
                    contribution="0.10",
                ),
                self._component(
                    "review_urgency",
                    contribution="0.30",
                ),
                self._component(
                    "recurring_errors",
                    contribution="0.20",
                ),
            )
        )

        explained = explain_ranked_recommendations(
            ranked_recommendations=(row,)
        )[0]

        self.assertEqual(
            [reason.feature_name for reason in explained.reasons],
            ["review_urgency", "recurring_errors", "weakness"],
        )

    def test_reason_preserves_feature_value_and_contribution(self):
        component = self._component(
            "weakness",
            value="0.75",
            contribution="0.18750",
        )
        row = self._ranked(components=(component,))

        reason = explain_ranked_recommendations(
            ranked_recommendations=(row,)
        )[0].reasons[0]

        self.assertEqual(reason.feature_name, "weakness")
        self.assertEqual(reason.value, Decimal("0.75"))
        self.assertEqual(reason.contribution, Decimal("0.18750"))
        self.assertEqual(reason.code, "target_skill_needs_strengthening")

    def test_zero_contribution_component_is_not_presented_as_reason(self):
        row = self._ranked(
            components=(
                self._component(
                    "weakness",
                    value="0",
                    contribution="0",
                ),
            ),
            score=Decimal("0"),
        )

        explained = explain_ranked_recommendations(
            ranked_recommendations=(row,)
        )[0]

        self.assertEqual(len(explained.reasons), 1)
        self.assertEqual(explained.reasons[0].code, "available_now")
        self.assertIsNone(explained.reasons[0].feature_name)

    def test_default_explanation_limit_is_three_reasons(self):
        row = self._ranked(
            components=(
                self._component("weakness", contribution="0.30"),
                self._component("review_urgency", contribution="0.25"),
                self._component("uncertainty", contribution="0.20"),
                self._component("novelty", contribution="0.10"),
            )
        )

        explained = explain_ranked_recommendations(
            ranked_recommendations=(row,)
        )[0]

        self.assertEqual(len(explained.reasons), 3)

    def test_explicit_max_reasons_is_respected(self):
        row = self._ranked(
            components=(
                self._component("weakness", contribution="0.30"),
                self._component("review_urgency", contribution="0.25"),
            )
        )

        explained = explain_ranked_recommendations(
            ranked_recommendations=(row,),
            max_reasons=1,
        )[0]

        self.assertEqual(len(explained.reasons), 1)
        self.assertEqual(explained.reasons[0].feature_name, "weakness")

    def test_invalid_max_reasons_is_rejected(self):
        row = self._ranked()

        for value in (0, -1, True, "2"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    explain_ranked_recommendations(
                        ranked_recommendations=(row,),
                        max_reasons=value,
                    )

    def test_vocabulary_review_reason_uses_due_count(self):
        row = self._ranked(
            candidate_type=RecommendationCandidateType.VOCABULARY_REVIEW,
            vocabulary_due_count=7,
            components=(
                self._component(
                    "review_urgency",
                    contribution="0.4",
                ),
            ),
        )

        reason = explain_ranked_recommendations(
            ranked_recommendations=(row,)
        )[0].reasons[0]

        self.assertIn("7 từ vựng", reason.message)
        self.assertIn("SRS", reason.message)

    def test_continue_reason_is_specific_to_unfinished_lesson(self):
        row = self._ranked(
            candidate_type=RecommendationCandidateType.CONTINUE_LESSON,
            components=(
                self._component(
                    "continuity",
                    contribution="0.4",
                ),
            ),
        )

        reason = explain_ranked_recommendations(
            ranked_recommendations=(row,)
        )[0].reasons[0]

        self.assertEqual(reason.message, "Bạn đang học dở bài này.")

    def test_prerequisite_reason_does_not_claim_specific_mastery_value(self):
        row = self._ranked(
            components=(
                self._component(
                    "prerequisite_readiness",
                    value="0.70",
                    contribution="0.2",
                ),
            )
        )

        reason = explain_ranked_recommendations(
            ranked_recommendations=(row,)
        )[0].reasons[0]

        self.assertEqual(
            reason.message,
            "Bài học hiện sẵn sàng về các điều kiện tiên quyết.",
        )
        self.assertNotIn("70", reason.message)

    def test_unknown_r5_component_is_rejected_explicitly(self):
        row = self._ranked(
            components=(
                self._component(
                    "unknown_feature",
                    contribution="0.2",
                ),
            )
        )

        with self.assertRaisesRegex(ValueError, "Unsupported R5 score component"):
            explain_ranked_recommendations(
                ranked_recommendations=(row,)
            )

    def test_exact_contribution_tie_has_deterministic_feature_order(self):
        row = self._ranked(
            components=(
                self._component("review_urgency", contribution="0.2"),
                self._component("weakness", contribution="0.2"),
            )
        )

        explained = explain_ranked_recommendations(
            ranked_recommendations=(row,)
        )[0]

        self.assertEqual(
            [reason.feature_name for reason in explained.reasons],
            ["weakness", "review_urgency"],
        )

    def test_input_rows_are_returned_in_rank_order_without_reranking(self):
        second = self._ranked(rank=2, key="r6:second")
        first = self._ranked(rank=1, key="r6:first")

        explained = explain_ranked_recommendations(
            ranked_recommendations=(second, first)
        )

        self.assertEqual(
            [row.candidate.candidate_key for row in explained],
            ["r6:first", "r6:second"],
        )
        self.assertEqual([row.rank for row in explained], [1, 2])

    def test_duplicate_candidate_keys_are_rejected(self):
        first = self._ranked(rank=1, key="r6:duplicate")
        second = self._ranked(rank=2, key="r6:duplicate")

        with self.assertRaisesRegex(ValueError, "candidate keys must be unique"):
            explain_ranked_recommendations(
                ranked_recommendations=(first, second)
            )

    def test_duplicate_ranks_are_rejected(self):
        first = self._ranked(rank=1, key="r6:first")
        second = self._ranked(rank=1, key="r6:second")

        with self.assertRaisesRegex(ValueError, "ranks must be unique"):
            explain_ranked_recommendations(
                ranked_recommendations=(first, second)
            )

    def test_non_ranked_input_is_rejected(self):
        with self.assertRaises(TypeError):
            explain_ranked_recommendations(
                ranked_recommendations=(object(),)
            )

    def test_explanation_version_is_propagated(self):
        explained = explain_ranked_recommendations(
            ranked_recommendations=(self._ranked(),)
        )[0]

        self.assertEqual(
            explained.explanation_version,
            "recommendation_explanation_v1",
        )

    @override_settings(
        RECOMMENDATION_EXPLANATION={
            "version": "",
            "default_max_reasons": 3,
        }
    )
    def test_policy_requires_non_empty_version(self):
        with self.assertRaisesRegex(ValueError, "non-empty string"):
            explain_ranked_recommendations(
                ranked_recommendations=(self._ranked(),)
            )

    @override_settings(
        RECOMMENDATION_EXPLANATION={
            "version": "r6-test",
            "default_max_reasons": 0,
        }
    )
    def test_policy_requires_positive_default_max_reasons(self):
        with self.assertRaisesRegex(ValueError, "positive integer"):
            explain_ranked_recommendations(
                ranked_recommendations=(self._ranked(),)
            )

    def test_explained_output_does_not_change_r5_score_or_rank(self):
        ranked = self._ranked(
            rank=2,
            score=Decimal("0.81234"),
            components=(
                self._component("weakness", contribution="0.2"),
            ),
        )

        explained = explain_ranked_recommendations(
            ranked_recommendations=(ranked,)
        )[0]

        self.assertEqual(explained.rank, 2)
        self.assertEqual(explained.score, Decimal("0.81234"))
        self.assertEqual(
            explained.scoring_version,
            "recommendation_ranking_v1",
        )
        self.assertIs(explained.ranked, ranked)

    def test_outputs_are_immutable(self):
        explained = explain_ranked_recommendations(
            ranked_recommendations=(self._ranked(),)
        )[0]
        reason = explained.reasons[0]

        self.assertIsInstance(explained, ExplainedRecommendation)
        self.assertIsInstance(reason, RecommendationExplanationReason)
        with self.assertRaises(Exception):
            explained.headline = "changed"
        with self.assertRaises(Exception):
            reason.message = "changed"

    def test_pipeline_calls_r5_and_explains_returned_rows(self):
        ranked = self._ranked(
            components=(
                self._component("weakness", contribution="0.2"),
            )
        )

        with patch(
            "recommendation.explanations.rank_recommendations",
            return_value=(ranked,),
        ) as rank:
            explained = explain_recommendations(
                user=self.user,
                candidates=(ranked.candidate,),
                as_of=self.as_of,
                limit=1,
                max_reasons=1,
            )

        rank.assert_called_once_with(
            user=self.user,
            candidates=(ranked.candidate,),
            as_of=self.as_of,
            limit=1,
        )
        self.assertEqual(len(explained), 1)
        self.assertEqual(explained[0].candidate.candidate_key, "r6:candidate")
        self.assertEqual(len(explained[0].reasons), 1)

    def test_empty_ranking_returns_empty_explanations(self):
        self.assertEqual(
            explain_ranked_recommendations(ranked_recommendations=()),
            (),
        )
