from django.contrib import admin

from recommendation.models import RecommendationBatch, RecommendationDecision


class RecommendationDecisionInline(admin.TabularInline):
    model = RecommendationDecision
    extra = 0
    can_delete = False
    show_change_link = False
    fields = (
        "rank",
        "candidate_type",
        "candidate_key",
        "score",
        "headline",
        "created_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(RecommendationBatch)
class RecommendationBatchAdmin(admin.ModelAdmin):
    list_display = (
        "batch_id",
        "user",
        "surface",
        "request_id",
        "item_count",
        "feature_version",
        "scoring_version",
        "explanation_version",
        "context_at",
        "created_at",
    )
    list_filter = (
        "surface",
        "feature_version",
        "scoring_version",
        "explanation_version",
    )
    search_fields = (
        "request_id",
        "user__email",
    )
    ordering = ("-created_at", "-batch_id")
    readonly_fields = (
        "batch_id",
        "user",
        "request_id",
        "surface",
        "feature_version",
        "scoring_version",
        "explanation_version",
        "context_at",
        "item_count",
        "payload_hash",
        "created_at",
    )
    inlines = (RecommendationDecisionInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RecommendationDecision)
class RecommendationDecisionAdmin(admin.ModelAdmin):
    list_display = (
        "decision_id",
        "batch",
        "rank",
        "candidate_type",
        "candidate_key",
        "score",
        "created_at",
    )
    list_filter = ("candidate_type",)
    search_fields = (
        "candidate_key",
        "batch__request_id",
        "batch__user__email",
    )
    ordering = ("-created_at", "batch_id", "rank")
    readonly_fields = (
        "decision_id",
        "batch",
        "candidate_key",
        "candidate_type",
        "rank",
        "score",
        "headline",
        "candidate_snapshot",
        "feature_snapshot",
        "score_components",
        "explanation_reasons",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
