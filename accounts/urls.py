from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views


app_name = "accounts"


urlpatterns = [
    path(
        "login/",
        views.login_view,
        name="login",
    ),

    path(
        "register/",
        views.register_view,
        name="register",
    ),

    path(
        "logout/",
        LogoutView.as_view(
            next_page="UI:landing",
        ),
        name="logout",
    ),
]