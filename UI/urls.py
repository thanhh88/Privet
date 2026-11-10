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