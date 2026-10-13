from django.core.validators import RegexValidator
from django.db import migrations, models


def ensure_question_option_table_empty(apps, schema_editor):
    QuestionOption = apps.get_model(
        "questionbank",
        "QuestionOption",
    )

    if QuestionOption.objects.exists():
        raise RuntimeError(
            "Cannot add required QuestionOption.option_key because "
            "legacy QuestionOption rows still exist. "
            "Clean or migrate them first."
        )


class Migration(migrations.Migration):

    dependencies = [
        (
            "questionbank",
            "0005_remove_questionoption_uq_qopt_one_correct",
        ),
    ]

    operations = [
        migrations.RunPython(
            ensure_question_option_table_empty,
            migrations.RunPython.noop,
        ),

        migrations.AddField(
            model_name="questionoption",
            name="option_key",
            field=models.CharField(
                max_length=16,
                validators=[
                    RegexValidator(
                        regex=r"^[A-Z0-9][A-Z0-9._-]*$",
                        message=(
                            "Option key must start with A-Z or 0-9 "
                            "and contain only A-Z, 0-9, '.', '_' or '-'."
                        ),
                    ),
                ],
            ),
        ),

        migrations.AddConstraint(
            model_name="questionoption",
            constraint=models.UniqueConstraint(
                fields=("question", "option_key"),
                name="uq_qopt_question_key",
            ),
        ),
    ]