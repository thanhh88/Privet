from django.contrib import admin

from .models import (
    Lesson,
    LessonProgress,
    LessonSkill,
    Skill,
    SkillPrerequisite,
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


class SkillPrerequisiteInline(admin.TabularInline):
    model = SkillPrerequisite
    fk_name = "skill"
    extra = 1

    fields = (
        "prerequisite_skill",
        "required_mastery",
        "is_active",
    )

    autocomplete_fields = (
        "prerequisite_skill",
    )


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = (
        "skill_id",
        "skill_code",
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
        "skill_code",
        "skill_name",
        "topic__topic_name",
    )

    ordering = (
        "topic",
        "display_order",
        "skill_id",
    )

    inlines = (
        SkillPrerequisiteInline,
    )


@admin.register(SkillPrerequisite)
class SkillPrerequisiteAdmin(admin.ModelAdmin):
    list_display = (
        "skill_prerequisite_id",
        "skill",
        "prerequisite_skill",
        "required_mastery",
        "is_active",
    )

    list_filter = (
        "is_active",
        "skill__cefr_level",
        "prerequisite_skill__cefr_level",
    )

    search_fields = (
        "skill__skill_code",
        "skill__skill_name",
        "prerequisite_skill__skill_code",
        "prerequisite_skill__skill_name",
    )

    autocomplete_fields = (
        "skill",
        "prerequisite_skill",
    )

    ordering = (
        "skill_id",
        "prerequisite_skill_id",
    )


class LessonSkillInline(admin.TabularInline):
    model = LessonSkill

    extra = 1

    fields = (
        "skill",
    )

    autocomplete_fields = (
        "skill",
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

    inlines = (
        LessonSkillInline,
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