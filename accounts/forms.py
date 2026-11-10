from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import InviteCode


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