
"""Transactional and idempotent Vocabulary SRS services."""

from datetime import (
    datetime,
    time,
    timedelta,
    timezone as dt_timezone,
)

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import (
    VocabularyItem,
    VocabularyReviewLog,
    VocabularySRSState,
)

from .srs import advance, validate_policy
from .tracking_bridge import record_vocabulary_review


class VocabularyError(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code = code
        self.status = status


def policy():
    return validate_policy(settings.VOCABULARY_SRS)


def _lock_user(user):
    # Serialize writes for this learner, including
    # creation before a state row exists.
    get_user_model().objects.select_for_update().get(
        pk=user.pk
    )


def _eligible(item):
    return VocabularyItem.objects.filter(
        pk=item.pk,
        is_active=True,
        topic_links__topic__is_active=True,
    ).exists()


def _utc_bounds(now):
    day = now.astimezone(dt_timezone.utc).date()

    start = datetime.combine(
        day,
        time.min,
        tzinfo=dt_timezone.utc,
    )

    return start, start + timedelta(days=1)


# ============================================================
# START LEARNING
# ============================================================

def start_learning(*, user, item):
    with transaction.atomic():
        _lock_user(user)

        if not _eligible(item):
            raise VocabularyError("item_not_found", 404)

        current = VocabularySRSState.objects.filter(
            user=user,
            item=item,
        ).first()

        # A second start does not reset anything.
        if current:
            return current, False

        now = timezone.now()
        start, end = _utc_bounds(now)

        used = VocabularySRSState.objects.filter(
            user=user,
            learned_at__gte=start,
            learned_at__lt=end,
        ).count()

        if used >= policy()["max_new_per_day"]:
            raise VocabularyError("daily_new_limit")

        state = VocabularySRSState.objects.create(
            user=user,
            item=item,
            box=1,
            version=1,
            lapses=0,
            learned_at=now,
            next_review_at=(
                now + timedelta(
                    days=policy()["intervals"][1]
                )
            ),
        )

        return state, True


# ============================================================
# SUBMIT DUE REVIEW
# ============================================================

def submit_review(
    *,
    user,
    item,
    request_id,
    rating,
    expected_version,
    response_time_ms=None,
):
    if rating not in VocabularyReviewLog.Rating.values:
        raise VocabularyError("invalid_rating", 400)

    if (
        type(expected_version) is not int
        or expected_version < 1
    ):
        raise VocabularyError(
            "invalid_expected_version",
            400,
        )

    if response_time_ms is not None and (
        type(response_time_ms) is not int
        or response_time_ms < 0
        or response_time_ms > 3_600_000
    ):
        raise VocabularyError(
            "invalid_response_time_ms",
            400,
        )

    with transaction.atomic():
        _lock_user(user)

        # --------------------------------------
        # 1. Idempotency check FIRST
        # --------------------------------------

        previous = VocabularyReviewLog.objects.filter(
            request_id=request_id
        ).first()

        if previous:
            if (
                previous.user_id != user.pk
                or previous.item_id != item.pk
                or previous.rating != rating
                or previous.state_version_before != expected_version
                or previous.response_time_ms != response_time_ms
            ):
                raise VocabularyError(
                    "idempotency_conflict"
                )

            return previous

        # --------------------------------------
        # 2. Item and state validation
        # --------------------------------------

        if not _eligible(item):
            raise VocabularyError("item_not_found", 404)

        try:
            state = (
                VocabularySRSState.objects
                .select_for_update()
                .get(
                    user=user,
                    item=item,
                )
            )

        except VocabularySRSState.DoesNotExist as exc:
            raise VocabularyError("not_started") from exc

        # Two tabs may hold different versions.
        if state.version != expected_version:
            raise VocabularyError("stale_state")

        now = timezone.now()

        if state.next_review_at > now:
            raise VocabularyError("not_due")

        # --------------------------------------
        # 3. Leitner transition
        # --------------------------------------

        old_box = state.box

        new_box, days, next_at = advance(
            old_box,
            rating,
            now,
            policy(),
        )

        state.box = new_box
        state.version += 1
        state.lapses += int(rating == "forgot")
        state.last_reviewed_at = now
        state.next_review_at = next_at

        state.save(
            update_fields=[
                "box",
                "version",
                "lapses",
                "last_reviewed_at",
                "next_review_at",
                "updated_at",
            ]
        )

        # --------------------------------------
        # 4. Immutable review snapshot
        # --------------------------------------

        log = VocabularyReviewLog.objects.create(
            request_id=request_id,
            user=user,
            item=item,
            state=state,
            rating=rating,

            box_before=old_box,
            box_after=new_box,
            interval_days_applied=days,

            state_version_before=expected_version,
            state_version_after=state.version,
            lapses_after=state.lapses,

            reviewed_at=now,
            next_review_at=next_at,

            srs_version=policy()["version"],
            response_time_ms=response_time_ms,
        )

        # --------------------------------------
        # 5. Vocabulary-specific tracking only
        # --------------------------------------

        record_vocabulary_review(
            user=user,
            at=now,
            response_time_ms=response_time_ms,
        )

        return log
