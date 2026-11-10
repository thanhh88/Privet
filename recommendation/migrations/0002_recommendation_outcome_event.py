import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("recommendation", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RecommendationOutcomeEvent",
            fields=[
                (
                    "outcome_event_id",
                    models.BigAutoField(primary_key=True, serialize=False),
                ),
                ("request_id", models.CharField(max_length=128)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("accepted", "Accepted"),
                            ("activity_started", "Activity started"),
                            ("activity_completed", "Activity completed"),
                            ("activity_abandoned", "Activity abandoned"),
                            ("dismissed", "Dismissed"),
                        ],
                        max_length=32,
                    ),
                ),
                ("outcome_version", models.CharField(max_length=64)),
                ("occurred_at", models.DateTimeField()),
                ("activity_snapshot", models.JSONField(default=dict)),
                ("result_snapshot", models.JSONField(default=dict)),
                ("mastery_snapshot", models.JSONField(default=list)),
                ("learning_gain_snapshot", models.JSONField(default=list)),
                ("payload_hash", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "decision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outcome_events",
                        to="recommendation.recommendationdecision",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recommendation_outcome_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "recommendation_outcome_event",
                "ordering": [
                    "decision_id",
                    "occurred_at",
                    "outcome_event_id",
                ],
            },
        ),
        migrations.AddIndex(
            model_name="recommendationoutcomeevent",
            index=models.Index(
                fields=["user", "-occurred_at"],
                name="idx_rec_outcome_user_recent",
            ),
        ),
        migrations.AddIndex(
            model_name="recommendationoutcomeevent",
            index=models.Index(
                fields=["decision", "occurred_at"],
                name="idx_rec_outcome_decision_time",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationoutcomeevent",
            constraint=models.UniqueConstraint(
                fields=("decision", "request_id"),
                name="uq_rec_outcome_decision_request",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationoutcomeevent",
            constraint=models.UniqueConstraint(
                fields=("decision", "event_type"),
                name="uq_rec_outcome_decision_event",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationoutcomeevent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "event_type__in",
                        [
                            "accepted",
                            "activity_started",
                            "activity_completed",
                            "activity_abandoned",
                            "dismissed",
                        ],
                    )
                ),
                name="ck_rec_outcome_event_type",
            ),
        ),
    ]
