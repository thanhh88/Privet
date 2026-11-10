import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RecommendationBatch",
            fields=[
                (
                    "batch_id",
                    models.BigAutoField(primary_key=True, serialize=False),
                ),
                ("request_id", models.CharField(max_length=128)),
                (
                    "surface",
                    models.CharField(
                        default="recommendation",
                        max_length=64,
                    ),
                ),
                ("feature_version", models.CharField(max_length=64)),
                ("scoring_version", models.CharField(max_length=64)),
                ("explanation_version", models.CharField(max_length=64)),
                ("context_at", models.DateTimeField()),
                ("item_count", models.PositiveIntegerField()),
                ("payload_hash", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recommendation_batches",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "recommendation_batch",
                "ordering": ["-created_at", "-batch_id"],
            },
        ),
        migrations.CreateModel(
            name="RecommendationDecision",
            fields=[
                (
                    "decision_id",
                    models.BigAutoField(primary_key=True, serialize=False),
                ),
                ("candidate_key", models.CharField(max_length=255)),
                ("candidate_type", models.CharField(max_length=64)),
                ("rank", models.PositiveIntegerField()),
                (
                    "score",
                    models.DecimalField(decimal_places=5, max_digits=6),
                ),
                ("headline", models.CharField(max_length=300)),
                (
                    "candidate_snapshot",
                    models.JSONField(default=dict),
                ),
                (
                    "feature_snapshot",
                    models.JSONField(default=dict),
                ),
                (
                    "score_components",
                    models.JSONField(default=list),
                ),
                (
                    "explanation_reasons",
                    models.JSONField(default=list),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="decisions",
                        to="recommendation.recommendationbatch",
                    ),
                ),
            ],
            options={
                "db_table": "recommendation_decision",
                "ordering": ["batch_id", "rank", "decision_id"],
            },
        ),
        migrations.AddIndex(
            model_name="recommendationbatch",
            index=models.Index(
                fields=["user", "-created_at"],
                name="idx_rec_batch_user_recent",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationbatch",
            constraint=models.UniqueConstraint(
                fields=("user", "request_id"),
                name="uq_recommendation_batch_user_request",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationbatch",
            constraint=models.CheckConstraint(
                condition=models.Q(("item_count__gte", 1)),
                name="ck_recommendation_batch_item_count",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationdecision",
            constraint=models.UniqueConstraint(
                fields=("batch", "rank"),
                name="uq_recommendation_decision_batch_rank",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationdecision",
            constraint=models.UniqueConstraint(
                fields=("batch", "candidate_key"),
                name="uq_recommendation_decision_batch_candidate",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationdecision",
            constraint=models.CheckConstraint(
                condition=models.Q(("rank__gte", 1)),
                name="ck_recommendation_decision_rank",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationdecision",
            constraint=models.CheckConstraint(
                condition=models.Q(("score__gte", 0), ("score__lte", 1)),
                name="ck_recommendation_decision_score",
            ),
        ),
    ]
