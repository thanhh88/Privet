from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

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
        null=True,
    )

    last_name = models.CharField(
        max_length=150,
        blank=True,
        null=True,
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "user"

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()

        super().save(*args, **kwargs)

    def __str__(self):
        return self.email

class InviteCode(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REDEEMED = "redeemed", "Redeemed"
        DISABLED = "disabled", "Disabled"

    id = models.BigAutoField(primary_key=True)

    code = models.CharField(
        max_length=64,
        unique=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
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

    def __str__(self):
        return self.code