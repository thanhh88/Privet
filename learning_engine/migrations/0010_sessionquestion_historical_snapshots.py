from collections import defaultdict

from django.db import migrations, models


def backfill_session_question_snapshots(
    apps,
    schema_editor,
):
    """
    Freeze the best available historical Question metadata
    for SessionQuestion rows created before R3.

    Pre-R3 rows did not have immutable snapshots, so the
    current Question / QuestionSkill state at migration time
    is the only recoverable approximation. Once backfilled,
    later Question Bank edits no longer rewrite history.
    """

    SessionQuestion = apps.get_model(
        "learning_engine",
        "SessionQuestion",
    )
    QuestionSkill = apps.get_model(
        "questionbank",
        "QuestionSkill",
    )

    db_alias = schema_editor.connection.alias

    question_ids = list(
        SessionQuestion.objects.using(db_alias)
        .values_list(
            "question_id",
            flat=True,
        )
        .distinct()
    )

    if not question_ids:
        return

    links_by_question = defaultdict(list)

    skill_links = (
        QuestionSkill.objects.using(db_alias)
        .filter(
            question_id__in=question_ids,
        )
        .select_related(
            "skill",
        )
        .order_by(
            "question_id",
            "-is_primary",
            "skill_id",
        )
    )

    for link in skill_links:
        links_by_question[
            link.question_id
        ].append(
            {
                "skill_id": link.skill_id,
                "skill_code": link.skill.skill_code,
                "is_primary": bool(
                    link.is_primary
                ),
                # Preserve Decimal precision exactly in JSON.
                "weight": str(
                    link.weight
                ),
            }
        )

    missing_question_ids = [
        question_id
        for question_id in question_ids
        if not links_by_question[
            question_id
        ]
    ]

    if missing_question_ids:
        raise RuntimeError(
            "Cannot backfill SessionQuestion "
            "historical snapshots because these "
            "Question IDs have no QuestionSkill "
            "mappings: "
            + ", ".join(
                str(question_id)
                for question_id
                in missing_question_ids
            )
        )

    session_questions = (
        SessionQuestion.objects.using(db_alias)
        .select_related(
            "question",
        )
        .order_by(
            "session_question_id",
        )
    )

    for session_question in (
        session_questions.iterator()
    ):
        session_question.difficulty_score_snapshot = (
            session_question.question.difficulty_score
        )

        session_question.skill_snapshot = list(
            links_by_question[
                session_question.question_id
            ]
        )

        session_question.save(
            using=db_alias,
            update_fields=[
                "difficulty_score_snapshot",
                "skill_snapshot",
            ],
        )


class Migration(migrations.Migration):

    dependencies = [
        (
            "learning_engine",
            "0009_remove_practicesession_lesson_and_more",
        ),
        (
            "questionbank",
            "0008_question_item_format_and_more",
        ),
    ]

    operations = [
        # Phase 1:
        # add nullable columns so existing rows can survive
        # until the historical backfill runs.
        migrations.AddField(
            model_name="sessionquestion",
            name="difficulty_score_snapshot",
            field=models.DecimalField(
                decimal_places=4,
                max_digits=5,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="sessionquestion",
            name="skill_snapshot",
            field=models.JSONField(
                null=True,
            ),
        ),

        # Phase 2:
        # freeze the best recoverable pre-R3 metadata.
        migrations.RunPython(
            backfill_session_question_snapshots,
            migrations.RunPython.noop,
        ),

        # Phase 3:
        # all future SessionQuestion rows must carry
        # immutable historical metadata.
        migrations.AlterField(
            model_name="sessionquestion",
            name="difficulty_score_snapshot",
            field=models.DecimalField(
                decimal_places=4,
                max_digits=5,
            ),
        ),
        migrations.AlterField(
            model_name="sessionquestion",
            name="skill_snapshot",
            field=models.JSONField(),
        ),
        migrations.AddConstraint(
            model_name="sessionquestion",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    difficulty_score_snapshot__gte=0,
                    difficulty_score_snapshot__lte=1,
                ),
                name=(
                    "ck_sq_difficulty_snapshot_range"
                ),
            ),
        ),
    ]
