from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Skill, SkillPrerequisite, Topic
from .prerequisites import (
    DEFAULT_REQUIRED_MASTERY,
    get_direct_prerequisite_links,
    get_prerequisite_closure,
    set_skill_prerequisite,
    would_create_prerequisite_cycle,
)


class SkillPrerequisiteGraphTests(TestCase):
    def setUp(self):
        self.topic = Topic.objects.create(
            topic_name="R1 prerequisite graph",
            category="grammar",
            display_order=1,
        )
        self.skill_a = self._skill("R1-A", "Skill A", 1)
        self.skill_b = self._skill("R1-B", "Skill B", 2)
        self.skill_c = self._skill("R1-C", "Skill C", 3)
        self.skill_d = self._skill("R1-D", "Skill D", 4)

    def _skill(self, code, name, order):
        return Skill.objects.create(
            skill_code=code,
            topic=self.topic,
            skill_name=name,
            cefr_level=Skill.CefrLevel.A1,
            display_order=order,
        )

    def _edge(
        self,
        skill,
        prerequisite_skill,
        *,
        required_mastery=DEFAULT_REQUIRED_MASTERY,
        is_active=True,
    ):
        edge = SkillPrerequisite(
            skill=skill,
            prerequisite_skill=prerequisite_skill,
            required_mastery=required_mastery,
            is_active=is_active,
        )
        edge.full_clean()
        edge.save()
        return edge

    def test_valid_prerequisite_uses_default_mastery(self):
        edge = self._edge(self.skill_b, self.skill_a)

        self.assertEqual(
            edge.required_mastery,
            Decimal("0.7000"),
        )
        self.assertTrue(edge.is_active)
        self.assertEqual(
            list(self.skill_b.prerequisite_links.all()),
            [edge],
        )
        self.assertEqual(
            list(self.skill_a.dependent_links.all()),
            [edge],
        )

    def test_custom_required_mastery_is_supported(self):
        edge = self._edge(
            self.skill_b,
            self.skill_a,
            required_mastery=Decimal("0.8500"),
        )

        self.assertEqual(
            edge.required_mastery,
            Decimal("0.8500"),
        )

    def test_self_prerequisite_rejected_by_model_validation(self):
        edge = SkillPrerequisite(
            skill=self.skill_a,
            prerequisite_skill=self.skill_a,
        )

        with self.assertRaises(ValidationError) as caught:
            edge.full_clean()

        self.assertIn(
            "prerequisite_skill",
            caught.exception.message_dict,
        )

    def test_self_prerequisite_rejected_by_database_constraint(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SkillPrerequisite.objects.create(
                    skill=self.skill_a,
                    prerequisite_skill=self.skill_a,
                )

    def test_duplicate_pair_rejected_by_database_constraint(self):
        self._edge(self.skill_b, self.skill_a)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SkillPrerequisite.objects.create(
                    skill=self.skill_b,
                    prerequisite_skill=self.skill_a,
                )

    def test_required_mastery_below_zero_rejected(self):
        edge = SkillPrerequisite(
            skill=self.skill_b,
            prerequisite_skill=self.skill_a,
            required_mastery=Decimal("-0.0001"),
        )

        with self.assertRaises(ValidationError):
            edge.full_clean()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SkillPrerequisite.objects.create(
                    skill=self.skill_b,
                    prerequisite_skill=self.skill_a,
                    required_mastery=Decimal("-0.0001"),
                )

    def test_required_mastery_above_one_rejected(self):
        edge = SkillPrerequisite(
            skill=self.skill_b,
            prerequisite_skill=self.skill_a,
            required_mastery=Decimal("1.0001"),
        )

        with self.assertRaises(ValidationError):
            edge.full_clean()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SkillPrerequisite.objects.create(
                    skill=self.skill_b,
                    prerequisite_skill=self.skill_a,
                    required_mastery=Decimal("1.0001"),
                )

    def test_two_node_cycle_rejected(self):
        self._edge(self.skill_a, self.skill_b)

        reverse_edge = SkillPrerequisite(
            skill=self.skill_b,
            prerequisite_skill=self.skill_a,
        )

        with self.assertRaises(ValidationError) as caught:
            reverse_edge.full_clean()

        self.assertIn(
            "cycle",
            str(caught.exception).lower(),
        )

    def test_multi_hop_cycle_rejected(self):
        self._edge(self.skill_a, self.skill_b)
        self._edge(self.skill_b, self.skill_c)

        closing_edge = SkillPrerequisite(
            skill=self.skill_c,
            prerequisite_skill=self.skill_a,
        )

        with self.assertRaises(ValidationError):
            closing_edge.full_clean()

    def test_inactive_edge_may_exist_but_cannot_be_activated_into_cycle(self):
        self._edge(self.skill_a, self.skill_b)
        self._edge(self.skill_b, self.skill_c)
        closing_edge = self._edge(
            self.skill_c,
            self.skill_a,
            is_active=False,
        )

        closing_edge.is_active = True
        with self.assertRaises(ValidationError):
            closing_edge.full_clean()

    def test_updating_existing_edge_does_not_false_positive_cycle(self):
        edge = self._edge(self.skill_b, self.skill_a)
        edge.required_mastery = Decimal("0.8000")

        edge.full_clean()
        edge.save()
        edge.refresh_from_db()

        self.assertEqual(
            edge.required_mastery,
            Decimal("0.8000"),
        )

    def test_would_create_cycle_helper_follows_active_edges(self):
        self._edge(self.skill_a, self.skill_b)
        self._edge(self.skill_b, self.skill_c)

        self.assertTrue(
            would_create_prerequisite_cycle(
                skill_id=self.skill_c.pk,
                prerequisite_skill_id=self.skill_a.pk,
            )
        )
        self.assertFalse(
            would_create_prerequisite_cycle(
                skill_id=self.skill_d.pk,
                prerequisite_skill_id=self.skill_a.pk,
            )
        )

    def test_direct_links_exclude_inactive_by_default(self):
        active = self._edge(self.skill_d, self.skill_a)
        inactive = self._edge(
            self.skill_d,
            self.skill_b,
            is_active=False,
        )

        self.assertEqual(
            list(get_direct_prerequisite_links(self.skill_d)),
            [active],
        )
        self.assertEqual(
            list(
                get_direct_prerequisite_links(
                    self.skill_d,
                    active_only=False,
                )
            ),
            [active, inactive],
        )

    def test_prerequisite_closure_returns_transitive_skills_once(self):
        self._edge(self.skill_a, self.skill_b)
        self._edge(self.skill_a, self.skill_c)
        self._edge(self.skill_b, self.skill_d)
        self._edge(self.skill_c, self.skill_d)

        closure = get_prerequisite_closure(self.skill_a)

        self.assertEqual(
            [skill.pk for skill in closure],
            [
                self.skill_b.pk,
                self.skill_c.pk,
                self.skill_d.pk,
            ],
        )

    def test_prerequisite_closure_ignores_inactive_edges_by_default(self):
        self._edge(self.skill_a, self.skill_b)
        self._edge(
            self.skill_b,
            self.skill_c,
            is_active=False,
        )

        self.assertEqual(
            [
                skill.pk
                for skill in get_prerequisite_closure(
                    self.skill_a
                )
            ],
            [self.skill_b.pk],
        )
        self.assertEqual(
            [
                skill.pk
                for skill in get_prerequisite_closure(
                    self.skill_a,
                    active_only=False,
                )
            ],
            [self.skill_b.pk, self.skill_c.pk],
        )

    def test_set_skill_prerequisite_is_idempotent_for_same_pair(self):
        first = set_skill_prerequisite(
            skill=self.skill_b,
            prerequisite_skill=self.skill_a,
        )
        second = set_skill_prerequisite(
            skill=self.skill_b,
            prerequisite_skill=self.skill_a,
            required_mastery=Decimal("0.9000"),
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            SkillPrerequisite.objects.count(),
            1,
        )
        second.refresh_from_db()
        self.assertEqual(
            second.required_mastery,
            Decimal("0.9000"),
        )

    def test_set_skill_prerequisite_rejects_cycle(self):
        set_skill_prerequisite(
            skill=self.skill_a,
            prerequisite_skill=self.skill_b,
        )

        with self.assertRaises(ValidationError):
            set_skill_prerequisite(
                skill=self.skill_b,
                prerequisite_skill=self.skill_a,
            )
