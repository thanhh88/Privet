from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import InviteCode, User, UserSettings


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    model = User

    list_display = (
        "user_id",
        "email",
        "first_name",
        "last_name",
        "is_staff",
        "is_active",
    )

    list_filter = (
        "is_staff",
        "is_active",
        "is_superuser",
    )

    search_fields = (
        "email",
        "first_name",
        "last_name",
    )

    ordering = ("user_id",)

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "email",
                    "password",
                )
            },
        ),
        (
            "Personal information",
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "prior_russian_experience",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Important dates",
            {
                "fields": (
                    "last_login",
                    "date_joined",
                )
            },
        ),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "password1",
                    "password2",
                    "is_staff",
                    "is_active",
                ),
            },
        ),
    )


@admin.register(InviteCode)
class InviteCodeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "code",
        "status",
        "redeemed_by_user",
        "created_at",
        "redeemed_at",
    )

    list_filter = (
        "status",
        "created_at",
    )

    search_fields = (
        "code",
        "redeemed_by_user__email",
    )

    readonly_fields = (
        "created_at",
        "redeemed_at",
    )

    ordering = ("-created_at",)

@admin.register(UserSettings)
class UserSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "daily_goal_minutes",
        "recommendation_explanations_enabled",
        "show_streak_on_dashboard",
        "updated_at",
    )
    search_fields = ("user__email", "user__first_name", "user__last_name")
    readonly_fields = ("updated_at",)
