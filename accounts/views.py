from django.conf import settings
from django.contrib.auth import login
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import (
    EmailAuthenticationForm,
    RegisterForm,
)


def _redirect_target(request):
    target = (
        request.POST.get("next")
        or request.GET.get("next")
    )

    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target

    return reverse("UI:dashboard")


def _render_auth(
    request,
    *,
    mode,
    login_form=None,
    register_form=None,
):
    return render(
        request,
        "UI/landing.html",
        {
            "auth_mode": mode,
            "login_form": login_form,
            "register_form": register_form,
            "next": (
                request.POST.get("next")
                or request.GET.get("next")
                or ""
            ),
        },
    )


def login_view(request):
    if request.user.is_authenticated:
        return redirect("UI:dashboard")

    form = EmailAuthenticationForm(
        request=request,
        data=request.POST or None,
    )

    if request.method == "POST" and form.is_valid():
        login(
            request,
            form.get_user(),
        )

        return redirect(
            _redirect_target(request)
        )

    return _render_auth(
        request,
        mode="login",
        login_form=form,
    )


def register_view(request):
    if request.user.is_authenticated:
        return redirect("UI:dashboard")

    form = RegisterForm(
        request.POST or None,
    )

    if request.method == "POST" and form.is_valid():
        try:
            user = form.save()

        except ValidationError as exc:
            message = (
                exc.messages[0]
                if exc.messages
                else "Không thể sử dụng mã mời này."
            )

            form.add_error(
                "invite_code",
                message,
            )

        else:
            # User vừa được tạo từ form hợp lệ,
            # có thể login trực tiếp.
            login(
                request,
                user,
                backend=settings.AUTHENTICATION_BACKENDS[0],
            )

            return redirect(
                _redirect_target(request)
            )

    return _render_auth(
        request,
        mode="register",
        register_form=form,
    )