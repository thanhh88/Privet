
from django.urls import path
from . import views
from . import saved_words


app_name = "vocab"


urlpatterns = [
    path("topics/", views.topics, name="topics"),

    path("items/", views.items, name="items"),

    path(
        "items/<slug:source_id>/",
        views.item_detail,
        name="item_detail",
    ),

    path(
        "items/<slug:source_id>/start/",
        views.start_item,
        name="start",
    ),

    path(
        "reviews/due/",
        views.due_reviews,
        name="due",
    ),

    path(
        "reviews/summary/",
        views.review_summary,
        name="summary",
    ),

    path(
        "reviews/history/",
        views.review_history,
        name="history",
    ),

    path(
        "reviews/",
        views.submit_review_view,
        name="review",
    ),

    path(
        "saved/status/",
        saved_words.saved_status,
        name="saved_status",
    ),

    path(
        "saved/",
        saved_words.saved_toggle,
        name="saved_toggle",
    ),

]
