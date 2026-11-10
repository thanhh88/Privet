from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from curriculum.models import (
    Lesson,
    LessonProgress,
    LessonSkill,
    Skill,
    Topic,
)
from learning_engine.models import AssessmentSession
from recommendation.candidates import (
    RecommendationCandidateType,
    generate_recommendation_candidates,
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
