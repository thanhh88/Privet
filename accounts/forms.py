from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import InviteCode, UserSettings


User = get_user_model()


class EmailAuthenticationForm(AuthenticationForm):
    """
    Login bằng email.

    Django AuthenticationForm vẫn dùng internal field name = username,
    nhưng UI hiển thị là Email.
    """

    username = forms.EmailField(
        label="Email",
        max_length=254,
    )

    def clean_username(self):
        email = self.cleaned_data["username"]
        return email.strip().lower()


class RegisterForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        max_length=254,
    )

    password = forms.CharField(
        label="Mật khẩu",
        widget=forms.PasswordInput,
    )

    first_name = forms.CharField(
        label="Họ",
        max_length=150,
        required=False,
    )

    last_name = forms.CharField(
        label="Tên",
        max_length=150,
        required=False,
    )

    invite_code = forms.CharField(
        label="Mã mời",
        max_length=32,
        required=True,
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()

        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Email này đã được sử dụng."
            )

        return email

    def clean_password(self):
        password = self.cleaned_data["password"]

        validate_password(password)

        return password

    def clean_invite_code(self):
        code = self.cleaned_data["invite_code"].strip()

        invite = InviteCode.objects.filter(
            code=code,
        ).first()

        if invite is None:
            raise forms.ValidationError(
                "Mã mời không hợp lệ."
            )

        if invite.status != "available":
            raise forms.ValidationError(
                "Mã mời này đã được sử dụng hoặc không còn hiệu lực."
            )

        return code

    def save(self):
        if not self.is_valid():
            raise ValueError(
                "Cannot save an invalid RegisterForm."
            )

        with transaction.atomic():
            # Lock row để cùng một invite code không thể
            # được redeem đồng thời bởi hai request.
            invite = (
                InviteCode.objects
                .select_for_update()
                .filter(
                    code=self.cleaned_data["invite_code"],
                )
                .first()
            )

            if (
                invite is None
                or invite.status != "available"
            ):
                raise ValidationError(
                    "Mã mời này không còn khả dụng."
                )

            user = User.objects.create_user(
                email=self.cleaned_data["email"],
                password=self.cleaned_data["password"],
                first_name=self.cleaned_data[
                    "first_name"
                ].strip(),
                last_name=self.cleaned_data[
                    "last_name"
                ].strip(),
            )

            invite.status = "redeemed"
            invite.redeemed_by_user = user
            invite.redeemed_at = timezone.now()

            # Giữ validation hiện tại của InviteCode.
            invite.full_clean()

            invite.save(
                update_fields=[
                    "status",
                    "redeemed_by_user",
                    "redeemed_at",
                ]
            )

        return user

class PrivetPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label="Mật khẩu hiện tại",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    new_password1 = forms.CharField(
        label="Mật khẩu mới",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    new_password2 = forms.CharField(
        label="Nhập lại mật khẩu mới",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "email",
        )
        labels = {
            "first_name": "Họ",
            "last_name": "Tên",
            "email": "Email",
        }

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()

        queryset = User.objects.filter(email__iexact=email)
        if self.instance.pk is not None:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise forms.ValidationError("Email này đã được sử dụng.")

        return email


class UserSettingsForm(forms.ModelForm):
    class Meta:
        model = UserSettings
        fields = (
            "daily_goal_minutes",
            "recommendation_explanations_enabled",
            "show_streak_on_dashboard",
        )
        labels = {
            "daily_goal_minutes": "Mục tiêu học mỗi ngày (phút)",
            "recommendation_explanations_enabled": (
                "Hiển thị lý do của đề xuất học tập"
            ),
            "show_streak_on_dashboard": "Hiển thị streak trên trang chủ",
        }
        widgets = {
            "daily_goal_minutes": forms.NumberInput(
                attrs={"min": 5, "max": 180, "step": 5}
            ),
        }

    def clean_daily_goal_minutes(self):
        value = self.cleaned_data["daily_goal_minutes"]
        if value < 5 or value > 180:
            raise forms.ValidationError(
                "Mục tiêu mỗi ngày phải nằm trong khoảng 5–180 phút."
            )
        return value
