from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from curriculum.models import Lesson, LessonProgress, Skill, Topic
from learning_engine.models import AssessmentSession, SkillMastery
from tracking.models import StudySession

from .forms import ProfileForm, UserSettingsForm
from .models import UserSettings
from .preferences import get_user_settings
from .profile_summary import build_profile_summary


User = get_user_model()


class UserSettingsModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="learner@example.com",
            password="StrongPass123!",
        )

    def test_get_user_settings_creates_defaults(self):
        row = get_user_settings(user=self.user)
        self.assertEqual(row.daily_goal_minutes, 20)
        self.assertTrue(row.recommendation_explanations_enabled)
        self.assertTrue(row.show_streak_on_dashboard)

    def test_get_user_settings_is_idempotent(self):
        first = get_user_settings(user=self.user)
        second = get_user_settings(user=self.user)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(UserSettings.objects.filter(user=self.user).count(), 1)

    def test_unsaved_user_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "User must be saved"):
            get_user_settings(user=User(email="new@example.com"))

    def test_settings_form_rejects_goal_below_minimum(self):
        row = get_user_settings(user=self.user)
        form = UserSettingsForm(
            data={
                "daily_goal_minutes": 4,
                "recommendation_explanations_enabled": "on",
                "show_streak_on_dashboard": "on",
            },
            instance=row,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("daily_goal_minutes", form.errors)

    def test_settings_form_rejects_goal_above_maximum(self):
        row = get_user_settings(user=self.user)
        form = UserSettingsForm(
            data={
                "daily_goal_minutes": 181,
                "recommendation_explanations_enabled": "on",
                "show_streak_on_dashboard": "on",
            },
            instance=row,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("daily_goal_minutes", form.errors)


class ProfileFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="learner@example.com",
            password="StrongPass123!",
            first_name="Nguyen",
            last_name="Anh",
        )

    def test_email_is_normalized(self):
        form = ProfileForm(
            data={
                "first_name": "Nguyen",
                "last_name": "Anh",
                "email": " NEW@Example.COM ",
            },
            instance=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.email, "new@example.com")

    def test_duplicate_email_is_rejected_case_insensitively(self):
        User.objects.create_user(
            email="other@example.com",
            password="StrongPass123!",
        )
        form = ProfileForm(
            data={
                "first_name": "Nguyen",
                "last_name": "Anh",
                "email": "OTHER@example.com",
            },
            instance=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)


class ProfileSummaryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="summary@example.com",
            password="StrongPass123!",
            prior_russian_experience=None,
        )
        self.settings = get_user_settings(user=self.user)
        self.settings.daily_goal_minutes = 20
        self.settings.save(update_fields=["daily_goal_minutes", "updated_at"])

    def test_summary_uses_authoritative_learner_route(self):
        summary = build_profile_summary(
            user=self.user,
            user_settings=self.settings,
        )
        self.assertEqual(summary.route, "ask_prior_experience")
        self.assertEqual(summary.route_label, "Chưa hoàn tất onboarding")

    def test_summary_counts_real_learning_records(self):
        topic = Topic.objects.create(topic_name="T", category="grammar")
        skill = Skill.objects.create(
            skill_code="A1.TEST",
            topic=topic,
            skill_name="Test skill",
            cefr_level="A1",
        )
        lesson = Lesson.objects.create(
            title="Lesson",
            lesson_type="grammar",
            content={},
            is_active=True,
        )
        LessonProgress.objects.create(
            user=self.user,
            lesson=lesson,
            status=LessonProgress.Status.COMPLETED,
            completion_ratio=Decimal("1.0000"),
            completed_at=timezone.now(),
        )
        AssessmentSession.objects.create(
            user=self.user,
            mode=AssessmentSession.Mode.DIAGNOSTIC,
            status=AssessmentSession.Status.COMPLETED,
            ended_at=timezone.now(),
        )
        SkillMastery.objects.create(
            user=self.user,
            skill=skill,
            mastery_score=Decimal("0.80000"),
            observation_count=5,
            correct_attempts=4,
            recent_accuracy=Decimal("0.80000"),
            historical_accuracy=Decimal("0.80000"),
        )
        now = timezone.now()
        StudySession.objects.create(
            user=self.user,
            activity_type=StudySession.ActivityType.VOCABULARY,
            status=StudySession.Status.COMPLETED,
            started_at=now,
            last_activity_at=now,
            ended_at=now,
            active_seconds=900,
        )

        summary = build_profile_summary(
            user=self.user,
            user_settings=self.settings,
            as_of=now,
        )
        self.assertEqual(summary.completed_lessons, 1)
        self.assertEqual(summary.total_lessons, 1)
        self.assertEqual(summary.completed_assessments, 1)
        self.assertEqual(summary.mastered_skills, 1)
        self.assertEqual(summary.total_skills, 1)
        self.assertEqual(summary.today_study_minutes, 15)
        self.assertEqual(summary.daily_goal_percent, 75)
        self.assertEqual(summary.total_study_minutes, 15)

    def test_naive_as_of_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "timezone-aware"):
            build_profile_summary(
                user=self.user,
                user_settings=self.settings,
                as_of=timezone.now().replace(tzinfo=None),
            )


class ProfileSettingsViewTests(TestCase):
    password = "StrongPass123!"

    def setUp(self):
        self.user = User.objects.create_user(
            email="ui@example.com",
            password=self.password,
            first_name="Chu",
            last_name="Anh",
            prior_russian_experience=None,
        )
        self.client.force_login(self.user)

    def test_profile_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("UI:profile"))
        self.assertEqual(response.status_code, 302)

    def test_settings_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("UI:settings"))
        self.assertEqual(response.status_code, 302)

    def test_profile_page_renders_real_identity(self):
        response = self.client.get(reverse("UI:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ui@example.com")
        self.assertContains(response, "Tiến độ học tập")
        self.assertNotContains(response, "Trạng thái học tập")
        self.assertNotContains(response, "Quyền kiểm soát")

    def test_profile_post_updates_identity(self):
        response = self.client.post(
            reverse("UI:profile"),
            {
                "first_name": "Chu",
                "last_name": "Thu Anh",
                "email": "updated@example.com",
            },
        )
        self.assertRedirects(response, reverse("UI:profile"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_name, "Thu Anh")
        self.assertEqual(self.user.email, "updated@example.com")

    def test_settings_get_lazily_creates_preferences(self):
        self.assertFalse(UserSettings.objects.filter(user=self.user).exists())
        response = self.client.get(reverse("UI:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(UserSettings.objects.filter(user=self.user).exists())

    def test_preference_post_persists_values(self):
        response = self.client.post(
            reverse("UI:settings"),
            {
                "action": "preferences",
                "daily_goal_minutes": 35,
                "recommendation_explanations_enabled": "",
                "show_streak_on_dashboard": "",
            },
        )
        self.assertRedirects(response, reverse("UI:settings"))
        row = UserSettings.objects.get(user=self.user)
        self.assertEqual(row.daily_goal_minutes, 35)
        self.assertFalse(row.recommendation_explanations_enabled)
        self.assertFalse(row.show_streak_on_dashboard)

    def test_invalid_preference_post_does_not_persist(self):
        row = get_user_settings(user=self.user)
        response = self.client.post(
            reverse("UI:settings"),
            {
                "action": "preferences",
                "daily_goal_minutes": 999,
                "recommendation_explanations_enabled": "on",
                "show_streak_on_dashboard": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.daily_goal_minutes, 20)
        self.assertContains(response, "5–180")

    def test_password_change_keeps_authenticated_session(self):
        response = self.client.post(
            reverse("UI:settings"),
            {
                "action": "password",
                "old_password": self.password,
                "new_password1": "AnotherStrongPass456!",
                "new_password2": "AnotherStrongPass456!",
            },
        )
        self.assertRedirects(response, reverse("UI:settings"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("AnotherStrongPass456!"))
        self.assertEqual(
            self.client.get(reverse("UI:profile")).status_code,
            200,
        )

    def test_wrong_old_password_is_rejected(self):
        response = self.client.post(
            reverse("UI:settings"),
            {
                "action": "password",
                "old_password": "wrong",
                "new_password1": "AnotherStrongPass456!",
                "new_password2": "AnotherStrongPass456!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    @patch("UI.views._dashboard_recommendations", return_value=[])
    def test_dashboard_respects_hidden_streak_setting(self, mocked):
        row = get_user_settings(user=self.user)
        row.show_streak_on_dashboard = False
        row.save(update_fields=["show_streak_on_dashboard", "updated_at"])

        response = self.client.get(reverse("UI:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "streak-card")

    @patch("UI.views._dashboard_recommendations", return_value=[])
    def test_dashboard_passes_explanation_preference_to_recommender_ui(self, mocked):
        row = get_user_settings(user=self.user)
        row.recommendation_explanations_enabled = False
        row.save(
            update_fields=["recommendation_explanations_enabled", "updated_at"]
        )

        response = self.client.get(reverse("UI:dashboard"))
        self.assertEqual(response.status_code, 200)
        mocked.assert_called_once_with(self.user, show_explanations=False)

    @patch("UI.views._dashboard_recommendations", return_value=[])
    def test_dashboard_user_menu_links_profile_settings_and_logout(self, mocked):
        response = self.client.get(reverse("UI:dashboard"))
        self.assertContains(response, reverse("UI:profile"))
        self.assertContains(response, reverse("UI:settings"))
        self.assertContains(response, reverse("accounts:logout"))

    def test_settings_page_does_not_expose_algorithm_controls(self):
        response = self.client.get(reverse("UI:settings"))
        self.assertNotContains(response, 'name="mastery_threshold"')
        self.assertNotContains(response, 'name="recommendation_weights"')
        self.assertNotContains(response, 'name="prerequisite_threshold"')

    def test_profile_and_settings_keep_primary_header_navigation(self):
        for route_name in ("UI:profile", "UI:settings"):
            response = self.client.get(reverse(route_name))
            self.assertContains(response, reverse("UI:dashboard"))
            self.assertContains(response, reverse("UI:lessons"))
            self.assertContains(response, reverse("UI:test_hub"))
            self.assertContains(response, reverse("UI:progress"))
            self.assertContains(response, 'class="dashboard-header account-app-header"')

    def test_profile_does_not_promote_profile_or_settings_into_primary_nav(self):
        response = self.client.get(reverse("UI:profile"))
        self.assertNotContains(response, 'class="dashboard-nav-link is-active"')

    def test_settings_hides_password_form_until_disclosure_is_opened(self):
        response = self.client.get(reverse("UI:settings"))
        self.assertContains(response, '<summary class="account-password-trigger">Đổi mật khẩu</summary>', html=True)
        self.assertNotContains(response, "Cài đặt hệ thống")
        self.assertNotContains(
            response,
            'class="account-password-disclosure" open',
        )
        self.assertNotEqual(response.context["active_section"], "password")

    def test_invalid_password_post_reopens_password_disclosure(self):
        response = self.client.post(
            reverse("UI:settings"),
            {
                "action": "password",
                "old_password": "wrong",
                "new_password1": "AnotherStrongPass456!",
                "new_password2": "AnotherStrongPass456!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_section"], "password")
        self.assertContains(
            response,
            'class="account-password-disclosure" open',
        )
