# Generated for Privet R1 prerequisite graph.

import decimal

import django.core.validators
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("curriculum", "0003_skill_ck_skill_code_nonblank"),
    ]

    operations = [
        migrations.CreateModel(
            name="SkillPrerequisite",
            fields=[
                (
                    "skill_prerequisite_id",
                    models.BigAutoField(
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "required_mastery",
                    models.DecimalField(
                        decimal_places=4,
                        default=decimal.Decimal("0.7000"),
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(
                                decimal.Decimal("0")
                            ),
                            django.core.validators.MaxValueValidator(
                                decimal.Decimal("1")
                            ),
                        ],
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True),
                ),
                (
                    "prerequisite_skill",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="dependent_links",
                        to="curriculum.skill",
                    ),
                ),
                (
                    "skill",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="prerequisite_links",
                        to="curriculum.skill",
                    ),
                ),
            ],
            options={
                "db_table": "skill_prerequisite",
                "ordering": (
                    "skill_id",
                    "prerequisite_skill_id",
                ),
            },
        ),
        migrations.AddConstraint(
            model_name="skillprerequisite",
            constraint=models.UniqueConstraint(
                fields=(
                    "skill",
                    "prerequisite_skill",
                ),
                name="uq_skill_prerequisite_pair",
            ),
        ),
        migrations.AddConstraint(
            model_name="skillprerequisite",
            constraint=models.CheckConstraint(
                condition=~models.Q(
                    skill=models.F("prerequisite_skill"),
                ),
                name="ck_skill_prerequisite_not_self",
            ),
        ),
        migrations.AddConstraint(
            model_name="skillprerequisite",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(required_mastery__gte=0)
                    & models.Q(required_mastery__lte=1)
                ),
                name="ck_skill_prerequisite_mastery",
            ),
        ),
        migrations.AddIndex(
            model_name="skillprerequisite",
            index=models.Index(
                fields=[
                    "skill",
                    "is_active",
                ],
                name="idx_skill_prereq_skill",
            ),
        ),
        migrations.AddIndex(
            model_name="skillprerequisite",
            index=models.Index(
                fields=[
                    "prerequisite_skill",
                    "is_active",
                ],
                name="idx_skill_prereq_req",
            ),
        ),
    ]
