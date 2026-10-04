from django.contrib import admin

from .models import (
    Lesson,
    LessonProgress,
    Skill,
    Topic,
)


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = (
        "topic_id",
        "topic_name",
        "category",
        "display_order",
        "is_active",
    )

    list_filter = (
        "category",
        "is_active",
    )

    search_fields = (
        "topic_name",
        "category",
    )

    ordering = (
        "display_order",
        "topic_id",
    )


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = (
        "skill_id",
        "skill_name",
        "topic",
        "cefr_level",
        "is_extension",
        "display_order",
        "is_active",
    )

    list_filter = (
        "cefr_level",
        "is_extension",
        "is_active",
        "topic",
    )

    search_fields = (
        "skill_name",
        "topic__topic_name",
    )

    ordering = (
        "topic",
        "display_order",
        "skill_id",
    )

@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = (
        "lesson_id",
        "title",
        "lesson_type",
        "display_order",
        "is_active",
    )

    list_filter = (
        "lesson_type",
        "is_active",
    )

    search_fields = (
        "title",
    )

    ordering = (
        "display_order",
        "lesson_id",
    )


@admin.register(LessonProgress)
class LessonProgressAdmin(admin.ModelAdmin):
    list_display = (
        "lesson_progress_id",
        "user",
        "lesson",
        "status",
        "completion_ratio",
        "started_at",
        "last_accessed_at",
        "completed_at",
    )

    list_filter = (
        "status",
    )

    search_fields = (
        "user__email",
        "lesson__title",
    )

    ordering = (
        "-last_accessed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False