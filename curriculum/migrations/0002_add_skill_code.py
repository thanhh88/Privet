from django.core.validators import RegexValidator
from django.db import migrations, models


def ensure_skill_table_empty(apps, schema_editor):
    Skill = apps.get_model("curriculum", "Skill")

    if Skill.objects.exists():
        raise RuntimeError(
            "Cannot add required Skill.skill_code because legacy "
            "Skill rows still exist. Clean or migrate them first."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("curriculum", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            ensure_skill_table_empty,
            migrations.RunPython.noop,
        ),
        migrations.AddField(
            model_name="skill",
            name="skill_code",
            field=models.CharField(
                max_length=64,
                unique=True,
                validators=[
                    RegexValidator(
                        regex=r"^[A-Z0-9][A-Z0-9._-]*$",
                        message=(
                            "Skill code must start with A-Z or 0-9 "
                            "and contain only A-Z, 0-9, '.', '_' or '-'."
                        ),
                    )
                ],
            ),
        ),
    ]