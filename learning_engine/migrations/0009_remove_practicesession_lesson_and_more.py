import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def ensure_no_lesson_sessions(
    apps,
    schema_editor,
):
    """
    C2 safety gate.

    Lesson/guided-practice activity no longer belongs
    to the assessment engine. Refuse the migration if
    legacy lesson-mode PracticeSession rows still exist.
    """
    PracticeSession = apps.get_model(
        "learning_engine",
        "PracticeSession",
    )

    db_alias = schema_editor.connection.alias

    lesson_session_count = (
        PracticeSession.objects
        .using(db_alias)
        .filter(
            mode="lesson"
        )
        .count()
    )

    if lesson_session_count:
        raise RuntimeError(
            "Cannot migrate PracticeSession to "
            "AssessmentSession: "
            f"{lesson_session_count} legacy "
            "lesson-mode session(s) still exist."
        )


class Migration(migrations.Migration):

    dependencies = [
        (
            "learning_engine",
            "0008_sessionquestion_active_response_time_ms",
        ),
        (
            "tracking",
            "0001_initial",
        ),
        migrations.swappable_dependency(
            settings.AUTH_USER_MODEL
        ),
    ]

    operations = [
        # ---------------------------------------------
        # Safety
        # ---------------------------------------------
        migrations.RunPython(
            ensure_no_lesson_sessions,
            migrations.RunPython.noop,
        ),

        # ---------------------------------------------
        # Remove the old lesson-specific invariant
        # before removing the lesson field.
        # ---------------------------------------------
        migrations.RemoveConstraint(
            model_name="practicesession",
            name="ck_practice_session_mode_lesson",
        ),

        # ---------------------------------------------
        # C2:
        # Rename instead of create/delete.
        #
        # This preserves:
        # - session_id
        # - user
        # - status
        # - timestamps
        # - SessionQuestion relationships
        # - historical assessment rows
        # ---------------------------------------------
        migrations.RenameModel(
            old_name="PracticeSession",
            new_name="AssessmentSession",
        ),

        # ---------------------------------------------
        # Lesson mode no longer belongs to assessment.
        # ---------------------------------------------
        migrations.RemoveField(
            model_name="assessmentsession",
            name="lesson",
        ),

        # ---------------------------------------------
        # Rename reverse relation semantics.
        # ---------------------------------------------
        migrations.AlterField(
            model_name="assessmentsession",
            name="user",
            field=models.ForeignKey(
                on_delete=(
                    django.db.models.deletion.CASCADE
                ),
                related_name="assessment_sessions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),

        # ---------------------------------------------
        # Assessment modes only.
        # ---------------------------------------------
        migrations.AlterField(
            model_name="assessmentsession",
            name="mode",
            field=models.CharField(
                choices=[
                    (
                        "adaptive",
                        "Adaptive",
                    ),
                    (
                        "diagnostic",
                        "Diagnostic",
                    ),
                ],
                default="adaptive",
                max_length=16,
            ),
        ),

        migrations.AddConstraint(
            model_name="assessmentsession",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    mode__in=[
                        "adaptive",
                        "diagnostic",
                    ]
                ),
                name="ck_assessment_session_mode",
            ),
        ),
    ]