from django.contrib import admin

from recommendation.models import (
    RecommendationBatch,
    RecommendationDecision,
    RecommendationOutcomeEvent,
)


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


class RecommendationOutcomeEventInline(admin.TabularInline):
    model = RecommendationOutcomeEvent
    extra = 0
    can_delete = False
    fields = (
        "event_type",
        "outcome_version",
        "occurred_at",
        "request_id",
        "created_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
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
    inlines = (RecommendationOutcomeEventInline,)
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


@admin.register(RecommendationOutcomeEvent)
class RecommendationOutcomeEventAdmin(admin.ModelAdmin):
    list_display = (
        "outcome_event_id",
        "decision",
        "user",
        "event_type",
        "outcome_version",
        "occurred_at",
        "created_at",
    )
    list_filter = ("event_type", "outcome_version")
    search_fields = (
        "request_id",
        "decision__candidate_key",
        "decision__batch__request_id",
        "user__email",
    )
    ordering = ("-occurred_at", "-outcome_event_id")
    readonly_fields = (
        "outcome_event_id",
        "decision",
        "user",
        "request_id",
        "event_type",
        "outcome_version",
        "occurred_at",
        "activity_snapshot",
        "result_snapshot",
        "mastery_snapshot",
        "learning_gain_snapshot",
        "payload_hash",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
