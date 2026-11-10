
from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path(
        "admin/",
        admin.site.urls,
    ),

    path(
        "api/learning/",
        include("learning_engine.urls"),
    ),

    path(
        "api/vocabulary/alphabet/",
        include("curriculum.alphabet_urls"),
    ),

    path(
        "accounts/",
        include("accounts.urls"),
    ),

    path(
        "",
        include("UI.urls"),
    ),
    path(
        "api/vocabulary/",
        include("vocabulary.urls"),
    ),
]
