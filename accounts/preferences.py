from .models import UserSettings


def get_user_settings(*, user):
    """Return the persisted learner-facing settings for a saved user."""

    if user.pk is None:
        raise ValueError("User must be saved before settings can be resolved.")

    settings_row, _ = UserSettings.objects.get_or_create(user=user)
    return settings_row
