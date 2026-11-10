from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
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
