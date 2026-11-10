# Generated for Vocabulary Stabilization.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracking", "0003_remove_studysession_ck_study_session_activity_type_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="studyevent",
            name="ck_study_event_type",
        ),
        migrations.RemoveConstraint(
            model_name="studyevent",
            name="ck_study_event_target",
        ),
        migrations.AddConstraint(
            model_name="studyevent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    event_type__in=[
                        "lesson_opened",
                        "practice_started",
                        "practice_completed",
                        "lesson_completed",
                        "test_started",
                        "test_completed",
                        "vocabulary_review_started",
                        "vocabulary_review_completed",
                    ]
                ),
                name="ck_study_event_type",
            ),
        ),
        migrations.AddConstraint(
            model_name="studyevent",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        event_type__in=[
                            "lesson_opened",
                            "practice_started",
                            "practice_completed",
                            "lesson_completed",
                        ],
                        lesson__isnull=False,
                        assessment_session__isnull=True,
                    )
                    | models.Q(
                        event_type__in=[
                            "test_started",
                            "test_completed",
                        ],
                        lesson__isnull=True,
                        assessment_session__isnull=False,
                    )
                    | models.Q(
                        event_type__in=[
                            "vocabulary_review_started",
                            "vocabulary_review_completed",
                        ],
                        lesson__isnull=True,
                        assessment_session__isnull=True,
                    )
                ),
                name="ck_study_event_target",
            ),
        ),
    ]
