from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class RecommendationBatch(models.Model):
    """One immutable recommendation impression batch for one learner.

    R7 records what the recommender decided to surface.  Outcome/acceptance
    tracking deliberately belongs to R8 and is not stored here.
    """

    batch_id = models.BigAutoField(primary_key=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recommendation_batches",
    )

    # Client/request supplied idempotency key.  It prevents duplicate logs
    # when the same UI request is retried.
    request_id = models.CharField(max_length=128)

    # Product surface that requested the recommendations.  R7 keeps this
    # intentionally open-ended so R9 can add UI surfaces without a migration.
    surface = models.CharField(max_length=64, default="recommendation")

    feature_version = models.CharField(max_length=64)
    scoring_version = models.CharField(max_length=64)
    explanation_version = models.CharField(max_length=64)

    # Exact learner-state evaluation time used by the recommendation pipeline.
    context_at = models.DateTimeField()

    item_count = models.PositiveIntegerField()

    # SHA-256 of the canonical persisted decision payload.  Used to detect a
    # conflicting retry that reuses request_id for different recommendations.
    payload_hash = models.CharField(max_length=64)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "recommendation_batch"
        ordering = ["-created_at", "-batch_id"]
        indexes = [
            models.Index(
                fields=["user", "-created_at"],
                name="idx_rec_batch_user_recent",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "request_id"],
                name="uq_recommendation_batch_user_request",
            ),
            models.CheckConstraint(
                condition=Q(item_count__gte=1),
                name="ck_recommendation_batch_item_count",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(
                "RecommendationBatch rows are immutable after creation."
            )
        return super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"RecommendationBatch(user={self.user_id}, "
            f"request={self.request_id}, items={self.item_count})"
        )


class RecommendationDecision(models.Model):
    """Immutable snapshot of one ranked recommendation shown in a batch."""

    decision_id = models.BigAutoField(primary_key=True)

    batch = models.ForeignKey(
        RecommendationBatch,
        on_delete=models.CASCADE,
        related_name="decisions",
    )

    candidate_key = models.CharField(max_length=255)
    candidate_type = models.CharField(max_length=64)

    rank = models.PositiveIntegerField()
    score = models.DecimalField(max_digits=6, decimal_places=5)

    headline = models.CharField(max_length=300)

    # Historical snapshots intentionally avoid foreign keys to lessons,
    # assessments, or skills.  A later content edit/deletion must not rewrite
    # what the recommender actually showed during an experiment.
    candidate_snapshot = models.JSONField(default=dict)
    feature_snapshot = models.JSONField(default=dict)
    score_components = models.JSONField(default=list)
    explanation_reasons = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "recommendation_decision"
        ordering = ["batch_id", "rank", "decision_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "rank"],
                name="uq_recommendation_decision_batch_rank",
            ),
            models.UniqueConstraint(
                fields=["batch", "candidate_key"],
                name="uq_recommendation_decision_batch_candidate",
            ),
            models.CheckConstraint(
                condition=Q(rank__gte=1),
                name="ck_recommendation_decision_rank",
            ),
            models.CheckConstraint(
                condition=Q(score__gte=0) & Q(score__lte=1),
                name="ck_recommendation_decision_score",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(
                "RecommendationDecision rows are immutable after creation."
            )
        return super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"RecommendationDecision(batch={self.batch_id}, "
            f"rank={self.rank}, candidate={self.candidate_key})"
        )
