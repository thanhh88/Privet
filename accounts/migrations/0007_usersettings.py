from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_user_prior_russian_experience"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "daily_goal_minutes",
                    models.PositiveSmallIntegerField(default=20),
                ),
                (
                    "recommendation_explanations_enabled",
                    models.BooleanField(default=True),
                ),
                (
                    "show_streak_on_dashboard",
                    models.BooleanField(default=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="preferences",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "user_settings"},
        ),
        migrations.AddConstraint(
            model_name="usersettings",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    daily_goal_minutes__gte=5,
                    daily_goal_minutes__lte=180,
                ),
                name="ck_user_settings_daily_goal",
            ),
        ),
    ]
