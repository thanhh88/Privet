from django.apps import AppConfig


class UIConfig(AppConfig):
    """UI assets and templates only; this app has no models or migrations."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "UI"
    verbose_name = "Privet UI"
