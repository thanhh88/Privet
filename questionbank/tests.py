from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TransactionTestCase

from curriculum.models import Skill, Topic
from questionbank.models import (
    Question,
    QuestionOption,
    QuestionSkill,
)
from questionbank.validation import (
    validate_item_format,
    validate_options,
    validate_question_answer_fields,
    validate_skill_rows,
)


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
            skill_code="TEST-SKILL-01",
            skill_name="Noun gender",
            cefr_level="A1",
            display_order=1,
            is_active=True,
        )

        self.skill_2 = Skill.objects.create(
            topic=self.topic,
            skill_code="TEST-SKILL-02",
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

    def test_primary_skill_can_be_switched_in_any_save_order(self):
        with transaction.atomic():
            question = self.create_question(
                "TEST_SWITCH_PRIMARY_001"
            )

            old_primary = QuestionSkill.objects.create(
                question=question,
                skill=self.skill_1,
                is_primary=True,
                weight=Decimal("0.50000"),
            )

            new_primary = QuestionSkill.objects.create(
                question=question,
                skill=self.skill_2,
                is_primary=False,
                weight=Decimal("0.50000"),
            )

        # Intentionally enable the new primary first.
        # The deferred DB trigger must allow either save order.
        with transaction.atomic():
            new_primary.is_primary = True
            new_primary.save(
                update_fields=["is_primary"]
            )

            old_primary.is_primary = False
            old_primary.save(
                update_fields=["is_primary"]
            )

        old_primary.refresh_from_db()
        new_primary.refresh_from_db()

        self.assertFalse(old_primary.is_primary)
        self.assertTrue(new_primary.is_primary)


class QuestionOptionIdentityTests(TransactionTestCase):
    """
    Database-level tests for stable QuestionOption identity.

    option_key is unique only within one Question:
        Q1/A + Q1/A -> invalid
        Q1/A + Q2/A -> valid
    """

    def setUp(self):
        self.topic = Topic.objects.create(
            topic_name="Option Test Grammar",
            category="grammar",
            display_order=1,
            is_active=True,
        )

        self.skill = Skill.objects.create(
            topic=self.topic,
            skill_code="TEST-OPTION-SKILL",
            skill_name="Option test skill",
            cefr_level="A1",
            display_order=1,
            is_active=True,
        )

    def create_valid_question(self, source_id):
        with transaction.atomic():
            question = Question.objects.create(
                source_id=source_id,
                question_type=Question.QuestionType.MULTIPLE_CHOICE,
                question_text="Test question",
                cefr_level="A1",
                difficulty_score=Decimal("0.2000"),
                is_active=True,
            )

            QuestionSkill.objects.create(
                question=question,
                skill=self.skill,
                is_primary=True,
                weight=Decimal("1.00000"),
            )

        return question

    def test_duplicate_option_key_on_same_question_is_rejected(self):
        question = self.create_valid_question(
            "TEST_OPTION_DUPLICATE_001"
        )

        QuestionOption.objects.create(
            question=question,
            option_key="A",
            option_text="First",
            is_correct=True,
            display_order=1,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                QuestionOption.objects.create(
                    question=question,
                    option_key="A",
                    option_text="Second",
                    is_correct=False,
                    display_order=2,
                )

    def test_same_option_key_on_different_questions_is_allowed(self):
        question_1 = self.create_valid_question(
            "TEST_OPTION_SCOPE_001"
        )
        question_2 = self.create_valid_question(
            "TEST_OPTION_SCOPE_002"
        )

        QuestionOption.objects.create(
            question=question_1,
            option_key="A",
            option_text="First",
            is_correct=True,
            display_order=1,
        )

        QuestionOption.objects.create(
            question=question_2,
            option_key="A",
            option_text="First",
            is_correct=True,
            display_order=1,
        )

        self.assertEqual(
            QuestionOption.objects.filter(
                option_key="A"
            ).count(),
            2,
        )


class QuestionValidationTests(SimpleTestCase):
    def test_valid_skill_rows(self):
        validate_skill_rows(
            [
                {
                    "is_primary": True,
                    "weight": Decimal("0.60000"),
                },
                {
                    "is_primary": False,
                    "weight": Decimal("0.40000"),
                },
            ]
        )

    def test_skill_rows_wrong_weight_sum_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_skill_rows(
                [
                    {
                        "is_primary": True,
                        "weight": Decimal("0.60000"),
                    },
                    {
                        "is_primary": False,
                        "weight": Decimal("0.30000"),
                    },
                ]
            )

    def test_multiple_choice_requires_one_correct_option(self):
        with self.assertRaises(ValidationError):
            validate_options(
                "multiple_choice",
                [
                    {
                        "option_key": "A",
                        "option_text": "A",
                        "is_correct": True,
                    },
                    {
                        "option_key": "B",
                        "option_text": "B",
                        "is_correct": True,
                    },
                ],
            )

    def test_multiple_choice_valid_options(self):
        validate_options(
            "multiple_choice",
            [
                {
                    "option_key": "A",
                    "option_text": "A",
                    "is_correct": True,
                },
                {
                    "option_key": "B",
                    "option_text": "B",
                    "is_correct": False,
                },
            ],
        )

    def test_fill_blank_rejects_options(self):
        with self.assertRaises(ValidationError):
            validate_options(
                "fill_blank",
                [
                    {
                        "option_key": "A",
                        "option_text": "A",
                        "is_correct": True,
                    }
                ],
            )

    def test_fill_blank_requires_answer(self):
        with self.assertRaises(ValidationError):
            validate_question_answer_fields(
                question_type="fill_blank",
                correct_answer="",
                accepted_answers=None,
            )

    def test_fill_blank_accepts_correct_answer(self):
        validate_question_answer_fields(
            question_type="fill_blank",
            correct_answer="студент",
            accepted_answers=None,
        )

    def test_fill_blank_accepts_answer_list(self):
        validate_question_answer_fields(
            question_type="fill_blank",
            correct_answer="",
            accepted_answers=[
                "студент",
                "Студент",
            ],
        )

    def test_option_based_question_rejects_correct_answer(self):
        with self.assertRaises(ValidationError):
            validate_question_answer_fields(
                question_type="multiple_choice",
                correct_answer="A",
                accepted_answers=None,
            )

    def test_accepted_answers_must_be_list(self):
        with self.assertRaises(ValidationError):
            validate_question_answer_fields(
                question_type="fill_blank",
                correct_answer="",
                accepted_answers="студент",
            )

    def test_contextual_is_rejected_for_now(self):
        with self.assertRaises(ValidationError):
            validate_options(
                "contextual",
                [],
            )

    def test_option_with_empty_text_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_options(
                "multiple_choice",
                [
                    {
                        "option_key": "A",
                        "option_text": "",
                        "is_correct": True,
                    },
                    {
                        "option_key": "B",
                        "option_text": "B",
                        "is_correct": False,
                    },
                ],
            )

    def test_option_with_whitespace_only_text_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_options(
                "multiple_choice",
                [
                    {
                        "option_key": "A",
                        "option_text": "   ",
                        "is_correct": True,
                    },
                    {
                        "option_key": "B",
                        "option_text": "B",
                        "is_correct": False,
                    },
                ],
            )

    def test_option_with_empty_key_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_options(
                "multiple_choice",
                [
                    {
                        "option_key": "",
                        "option_text": "First",
                        "is_correct": True,
                    },
                    {
                        "option_key": "B",
                        "option_text": "Second",
                        "is_correct": False,
                    },
                ],
            )

    def test_option_with_whitespace_only_key_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_options(
                "multiple_choice",
                [
                    {
                        "option_key": "   ",
                        "option_text": "First",
                        "is_correct": True,
                    },
                    {
                        "option_key": "B",
                        "option_text": "Second",
                        "is_correct": False,
                    },
                ],
            )

    def test_duplicate_option_keys_are_rejected(self):
        with self.assertRaises(ValidationError):
            validate_options(
                "multiple_choice",
                [
                    {
                        "option_key": "A",
                        "option_text": "First",
                        "is_correct": True,
                    },
                    {
                        "option_key": "A",
                        "option_text": "Second",
                        "is_correct": False,
                    },
                ],
            )

    def test_different_option_keys_are_allowed(self):
        validate_options(
            "multiple_choice",
            [
                {
                    "option_key": "A",
                    "option_text": "First",
                    "is_correct": True,
                },
                {
                    "option_key": "B",
                    "option_text": "Second",
                    "is_correct": False,
                },
            ],
        )

    def test_duplicate_skills_are_rejected(self):
        with self.assertRaises(ValidationError):
            validate_skill_rows(
                [
                    {
                        "skill": 10,
                        "is_primary": True,
                        "weight": Decimal("0.50000"),
                    },
                    {
                        "skill": 10,
                        "is_primary": False,
                        "weight": Decimal("0.50000"),
                    },
                ]
            )

    def test_different_skills_are_allowed(self):
        validate_skill_rows(
            [
                {
                    "skill": 10,
                    "is_primary": True,
                    "weight": Decimal("0.60000"),
                },
                {
                    "skill": 20,
                    "is_primary": False,
                    "weight": Decimal("0.40000"),
                },
            ]
        )

    def test_multiple_choice_accepts_single_choice_format(self):
        validate_item_format(
            "multiple_choice",
            "single_choice",
        )

    def test_multiple_choice_accepts_contextual_choice_format(self):
        validate_item_format(
            "multiple_choice",
            "contextual_choice",
        )

    def test_multiple_choice_accepts_vocab_formats(self):
        for item_format in {
            "ru_to_vi",
            "vi_to_ru",
            "definition_to_ru",
        }:
            validate_item_format(
                "multiple_choice",
                item_format,
            )

    def test_fill_choice_accepts_fill_choice_format(self):
        validate_item_format(
            "fill_choice",
            "fill_choice",
        )

    def test_fill_choice_accepts_cloze_ru_format(self):
        validate_item_format(
            "fill_choice",
            "cloze_ru",
        )

    def test_item_format_can_be_none(self):
        validate_item_format(
            "fill_blank",
            None,
        )

    def test_cloze_ru_rejects_multiple_choice(self):
        with self.assertRaises(ValidationError):
            validate_item_format(
                "multiple_choice",
                "cloze_ru",
            )

    def test_ru_to_vi_rejects_fill_choice(self):
        with self.assertRaises(ValidationError):
            validate_item_format(
                "fill_choice",
                "ru_to_vi",
            )

    def test_unknown_item_format_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_item_format(
                "multiple_choice",
                "unknown_format",
            )

    def test_question_full_clean_rejects_incompatible_item_format(self):
        question = Question(
            source_id="TEST_ITEM_FORMAT_001",
            question_type=Question.QuestionType.MULTIPLE_CHOICE,
            item_format=Question.ItemFormat.CLOZE_RU,
            question_text="Test question",
            cefr_level="A1",
            difficulty_score=Decimal("0.2000"),
            is_active=True,
        )

        with self.assertRaises(ValidationError):
            question.full_clean(
                validate_unique=False,
                validate_constraints=False,
            )

    def test_question_full_clean_accepts_compatible_item_format(self):
        question = Question(
            source_id="TEST_ITEM_FORMAT_002",
            question_type=Question.QuestionType.MULTIPLE_CHOICE,
            item_format=Question.ItemFormat.RU_TO_VI,
            question_text="Test question",
            cefr_level="A1",
            difficulty_score=Decimal("0.2000"),
            is_active=True,
        )

        question.full_clean(
            validate_unique=False,
            validate_constraints=False,
        )

    def test_option_accepts_misconception_code(self):
        validate_options(
            "multiple_choice",
            [
                {
                    "option_key": "A",
                    "option_text": "First",
                    "misconception_code": "wrong_case",
                    "is_correct": False,
                },
                {
                    "option_key": "B",
                    "option_text": "Second",
                    "misconception_code": None,
                    "is_correct": True,
                },
            ],
        )

    def test_empty_misconception_code_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_options(
                "multiple_choice",
                [
                    {
                        "option_key": "A",
                        "option_text": "First",
                        "misconception_code": "",
                        "is_correct": False,
                    },
                    {
                        "option_key": "B",
                        "option_text": "Second",
                        "misconception_code": None,
                        "is_correct": True,
                    },
                ],
            )

    def test_whitespace_only_misconception_code_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_options(
                "multiple_choice",
                [
                    {
                        "option_key": "A",
                        "option_text": "First",
                        "misconception_code": "   ",
                        "is_correct": False,
                    },
                    {
                        "option_key": "B",
                        "option_text": "Second",
                        "misconception_code": None,
                        "is_correct": True,
                    },
                ],
            )