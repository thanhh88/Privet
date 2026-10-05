from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TransactionTestCase

from curriculum.models import Skill, Topic
from questionbank.models import Question, QuestionSkill


class QuestionSkillIntegrityTests(TransactionTestCase):
    def setUp(self):
        self.topic = Topic.objects.create(
            topic_name="Grammar",
            category="grammar",
            display_order=1,
            is_active=True,
        )

        self.skill_1 = Skill.objects.create(
            topic=self.topic,
            skill_name="Noun gender",
            cefr_level="A1",
            display_order=1,
            is_active=True,
        )

        self.skill_2 = Skill.objects.create(
            topic=self.topic,
            skill_name="Noun number",
            cefr_level="A1",
            display_order=2,
            is_active=True,
        )

    def create_question(self, source_id):
        return Question.objects.create(
            source_id=source_id,
            question_type=Question.QuestionType.MULTIPLE_CHOICE,
            question_text="Test question",
            cefr_level="A1",
            difficulty_score=Decimal("0.2000"),
            is_active=True,
        )

    def test_valid_question_skill_mapping(self):
        with transaction.atomic():
            question = self.create_question(
                "TEST_VALID_001"
            )

            QuestionSkill.objects.create(
                question=question,
                skill=self.skill_1,
                is_primary=True,
                weight=Decimal("0.60000"),
            )

            QuestionSkill.objects.create(
                question=question,
                skill=self.skill_2,
                is_primary=False,
                weight=Decimal("0.40000"),
            )

        self.assertEqual(
            QuestionSkill.objects.filter(
                question=question
            ).count(),
            2,
        )

    def test_question_without_skill_is_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_question(
                    "TEST_NO_SKILL_001"
                )

    def test_question_without_primary_skill_is_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                question = self.create_question(
                    "TEST_NO_PRIMARY_001"
                )

                QuestionSkill.objects.create(
                    question=question,
                    skill=self.skill_1,
                    is_primary=False,
                    weight=Decimal("0.60000"),
                )

                QuestionSkill.objects.create(
                    question=question,
                    skill=self.skill_2,
                    is_primary=False,
                    weight=Decimal("0.40000"),
                )

    def test_wrong_weight_sum_is_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                question = self.create_question(
                    "TEST_BAD_WEIGHT_001"
                )

                QuestionSkill.objects.create(
                    question=question,
                    skill=self.skill_1,
                    is_primary=True,
                    weight=Decimal("0.60000"),
                )

                QuestionSkill.objects.create(
                    question=question,
                    skill=self.skill_2,
                    is_primary=False,
                    weight=Decimal("0.30000"),
                )

    def test_multiple_primary_skills_are_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                question = self.create_question(
                    "TEST_TWO_PRIMARY_001"
                )

                QuestionSkill.objects.create(
                    question=question,
                    skill=self.skill_1,
                    is_primary=True,
                    weight=Decimal("0.50000"),
                )

                QuestionSkill.objects.create(
                    question=question,
                    skill=self.skill_2,
                    is_primary=True,
                    weight=Decimal("0.50000"),
                )

    def test_weight_sum_within_tolerance_is_allowed(self):
        with transaction.atomic():
            question = self.create_question(
                "TEST_WEIGHT_TOLERANCE_001"
            )

            QuestionSkill.objects.create(
                question=question,
                skill=self.skill_1,
                is_primary=True,
                weight=Decimal("0.50000"),
            )

            QuestionSkill.objects.create(
                question=question,
                skill=self.skill_2,
                is_primary=False,
                weight=Decimal("0.49999"),
            )

        self.assertEqual(
            QuestionSkill.objects.filter(
                question=question
            ).count(),
            2,
        )