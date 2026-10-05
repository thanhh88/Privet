from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from .managers import UserManager


class User(AbstractUser):
    username = None

    user_id = models.BigAutoField(
        primary_key=True,
    )

    email = models.EmailField(
        unique=True,
    )

    first_name = models.CharField(
        max_length=150,
        blank=True,
    )

    last_name = models.CharField(
        max_length=150,
        blank=True,
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "app_user"

        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="uq_user_email_ci",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()

        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class InviteCode(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        REDEEMED = "redeemed", "Redeemed"
        DISABLED = "disabled", "Disabled"

    id = models.BigAutoField(
        primary_key=True,
    )

    code = models.CharField(
        max_length=64,
        unique=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.AVAILABLE,
    )

    redeemed_by_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="redeemed_invite_code",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    redeemed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "invite_code"

        constraints = [
            # B7:
            # Chỉ cho phép 3 giá trị status hợp lệ ở cấp database.
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "available",
                        "redeemed",
                        "disabled",
                    ]
                ),
                name="ck_invite_status",
            ),

            # B8:
            # Nếu redeemed:
            #   redeemed_by_user và redeemed_at phải tồn tại.
            #
            # Nếu không phải redeemed:
            #   cả hai phải NULL.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="redeemed",
                        redeemed_by_user__isnull=False,
                        redeemed_at__isnull=False,
                    )
                    |
                    (
                        ~models.Q(status="redeemed")
                        & models.Q(
                            redeemed_by_user__isnull=True,
                            redeemed_at__isnull=True,
                        )
                    )
                ),
                name="ck_invite_redeemed_state",
            ),
        ]

    def clean(self):
        super().clean()

        if self.status == self.Status.REDEEMED:
            if self.redeemed_by_user_id is None:
                raise ValidationError(
                    {
                        "redeemed_by_user":
                            "Required when status is redeemed."
                    }
                )

            if self.redeemed_at is None:
                self.redeemed_at = timezone.now()

        elif (
                self.redeemed_by_user_id is not None
                or self.redeemed_at is not None
        ):
            raise ValidationError(
                "Only redeemed codes may have a "
                "redeemer / redeemed_at."
            )

    def __str__(self):
        return self.code