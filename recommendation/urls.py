from django.urls import path

from recommendation import views


app_name = "recommendation"

urlpatterns = [
    path(
        "impressions/",
        views.create_impression_view,
        name="impression-create",
    ),
    path(
        "decisions/<int:decision_id>/state/",
        views.decision_state_view,
        name="decision-state",
    ),
    path(
        "decisions/<int:decision_id>/open/",
        views.open_decision_view,
        name="decision-open",
    ),
    path(
        "decisions/<int:decision_id>/dismiss/",
        views.dismiss_decision_view,
        name="decision-dismiss",
    ),
    path(
        "decisions/<int:decision_id>/complete/",
        views.complete_decision_view,
        name="decision-complete",
    ),
    path(
        "decisions/<int:decision_id>/abandon/",
        views.abandon_decision_view,
        name="decision-abandon",
    ),
]
