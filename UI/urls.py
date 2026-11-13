from django.urls import path

from . import views


app_name = "UI"


urlpatterns = [
    path(
        "",
        views.landing,
        name="landing",
    ),

    path(
        "dashboard/",
        views.dashboard,
        name="dashboard",
    ),

    path(
        "profile/",
        views.profile,
        name="profile",
    ),

    path(
        "settings/",
        views.settings_page,
        name="settings",
    ),

    path(
        "lessons/",
        views.lessons,
        name="lessons",
    ),

    path(
        "lessons/vocabulary/",
        views.vocabulary,
        name="vocabulary",
    ),


    path(
        "recommendations/<int:decision_id>/open/",
        views.recommendation_open,
        name="recommendation_open",
    ),

    path(
        "recommendations/<int:decision_id>/dismiss/",
        views.recommendation_dismiss,
        name="recommendation_dismiss",
    ),

    path(
        "learning/lessons/<int:lesson_id>/",
        views.recommended_lesson,
        name="recommended_lesson",
    ),

    path(
        "learning/lessons/<int:lesson_id>/complete/",
        views.recommended_lesson_complete,
        name="recommended_lesson_complete",
    ),

    path(
        "tests/session/<int:session_id>/",
        views.assessment_session,
        name="assessment_session",
    ),

    path(
        "lessons/vocabulary/review/",
        views.vocabulary_review,
        name="vocabulary_review",
    ),

    path(
        "lessons/<slug:lesson_slug>/",
        views.lesson_detail,
        name="lesson_detail",
    ),

    path(
        "progress/",
        views.progress,
        name="progress",
    ),

    path(
        "tests/",
        views.test_hub,
        name="test_hub",
    ),

    path(
        "tests/history/",
        views.test_history,
        name="test_history",
    ),

    path(
        "tests/demo/",
        views.test_doing,
        name="test_doing",
    ),
    path(
        "lessons/vocabulary/alphabet/",
        views.alphabet,
        name="alphabet",
    ),
]