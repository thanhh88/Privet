
"""Vocabulary v2 API with A1/A2 shared items."""

import json

from functools import wraps
from uuid import UUID

from django.db.models import (
    Count,
    OuterRef,
    Q,
    Subquery,
)

from django.http import JsonResponse
from django.utils import timezone

from django.views.decorators.csrf import csrf_protect

from django.views.decorators.http import (
    require_GET,
    require_POST,
)

from .models import (
    VocabularyItem,
    VocabularyItemTopic,
    VocabularyReviewLog,
    VocabularySRSState,
    VocabularyTopic,
)

from .services import (
    VocabularyError,
    policy,
    start_learning,
    submit_review,
)


# ============================================================
# GENERAL HELPERS
# ============================================================

def reply(payload, status=200):
    return JsonResponse(
        payload,
        status=status,
        json_dumps_params={
            "ensure_ascii": False,
        },
    )


def error(code, status=400):
    return reply(
        {"error": code},
        status,
    )


def authenticated(func):

    @wraps(func)
    def wrapper(request, *args, **kwargs):

        if not request.user.is_authenticated:
            return error(
                "authentication_required",
                401,
            )

        return func(
            request,
            *args,
            **kwargs,
        )

    return wrapper


def body_json(request):

    if (
        request.content_type != "application/json"
        or len(request.body) > 16384
    ):
        raise ValueError(
            "invalid_content_type_or_size"
        )

    try:
        obj = json.loads(request.body)

    except (
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise ValueError(
            "invalid_json"
        ) from exc

    if not isinstance(obj, dict):
        raise ValueError(
            "invalid_json_object"
        )

    return obj


def pagination(request, *, maximum=100):

    try:
        limit = int(
            request.GET.get(
                "limit",
                min(30, maximum),
            )
        )

        offset = int(
            request.GET.get(
                "offset",
                0,
            )
        )

    except (
        ValueError,
        TypeError,
    ) as exc:
        raise ValueError(
            "invalid_pagination"
        ) from exc

    if (
        limit < 1
        or limit > maximum
        or offset < 0
    ):
        raise ValueError(
            "invalid_pagination"
        )

    return limit, offset


# ============================================================
# ITEM QUERY HELPERS
# ============================================================

def visible_items():

    return VocabularyItem.objects.filter(
        is_active=True,
        topic_links__topic__is_active=True,
    ).distinct()


def visible_item(source_id):

    return visible_items().filter(
        source_id=source_id,
    ).first()


# ============================================================
# SERIALIZERS
# ============================================================

def state_json(state):

    if state is None:
        return None

    return {
        "box": state.box,
        "version": state.version,
        "lapses": state.lapses,

        "learned_at": (
            state.learned_at.isoformat()
        ),

        "last_reviewed_at": (
            state.last_reviewed_at.isoformat()
            if state.last_reviewed_at
            else None
        ),

        "next_review_at": (
            state.next_review_at.isoformat()
        ),

        "is_due": (
            state.next_review_at <= timezone.now()
        ),
    }


def _english_for_level(item, level):
    """
    A1 and A2 may have different English
    glosses for the same Russian entry.
    """

    if level in ("A1", "A2"):

        for entry in item.source_entries or []:

            if (
                entry.get("level") == level
                and entry.get("english_meaning")
            ):
                return entry["english_meaning"]

    return item.meaning_en


def item_json(
    item,
    state=None,
    requested_level=None,
):

    topics = [
        topic
        for topic in item.topics.all()
        if topic.is_active
    ]

    topic_codes = sorted(
        topic.code
        for topic in topics
    )

    available_levels = sorted({
        topic.cefr_level
        for topic in topics
        if topic.code in (
            "level-a1",
            "level-a2",
        )
    })

    meaning_en = _english_for_level(
        item,
        requested_level,
    )

    display_meaning = (
        item.meaning_vi or meaning_en
    )

    display_language = (
        "vi"
        if item.meaning_vi
        else "en"
    )

    return {
        "source_id": item.source_id,
        "lemma": item.lemma,

        "stress_marked": (
            item.stress_marked or item.lemma
        ),

        "meaning_vi": item.meaning_vi,
        "meaning_en": meaning_en,

        "display_meaning": display_meaning,
        "display_language": display_language,

        "part_of_speech": item.part_of_speech,
        "cefr_level": item.cefr_level,

        "available_levels": available_levels,

        "example_ru": item.example_ru,
        "example_vi": item.example_vi,

        "notes_vi": item.notes_vi,
        "audio_url": item.audio_url,

        "topic_codes": topic_codes,

        "srs": state_json(state),
    }


def receipt(log):
    """
    Return immutable review-log snapshot.
    """

    return {
        "request_id": str(log.request_id),
        "source_id": log.item.source_id,

        "rating": log.rating,

        "box_before": log.box_before,
        "box_after": log.box_after,

        "interval_days_applied": (
            log.interval_days_applied
        ),

        "state_version_before": (
            log.state_version_before
        ),

        "state_version_after": (
            log.state_version_after
        ),

        "lapses_after": log.lapses_after,

        "reviewed_at": (
            log.reviewed_at.isoformat()
        ),

        "next_review_at": (
            log.next_review_at.isoformat()
        ),

        "srs_version": log.srs_version,

        "response_time_ms": (
            log.response_time_ms
        ),
    }


# ============================================================
# GET /api/vocabulary/topics/
# ============================================================

@require_GET
@authenticated
def topics(request):

    level = (
        request.GET.get("level", "")
        .strip()
        .upper()
    )

    if level and level not in ("A1", "A2"):
        return error(
            "invalid_level"
        )

    qs = (
        VocabularyTopic.objects
        .filter(is_active=True)
        .select_related("assessment_skill")
        .annotate(
            item_count=Count(
                "item_links",
                filter=Q(
                    item_links__item__is_active=True
                ),
                distinct=True,
            )
        )
    )

    if level:
        qs = qs.filter(
            cefr_level=level,
        )

    result = []

    for topic in qs.order_by(
        "display_order",
        "code",
    ):

        result.append({
            "code": topic.code,

            "title_vi": topic.title_vi,
            "title_ru": topic.title_ru,

            "cefr_level": topic.cefr_level,

            "description": topic.description,

            "item_count": topic.item_count,

            "assessment_skill_code": (
                topic.assessment_skill.skill_code
                if topic.assessment_skill
                else None
            ),
        })

    return reply({
        "topics": result,
    })


# ============================================================
# GET /api/vocabulary/items/
# ============================================================

@require_GET
@authenticated
def items(request):

    try:
        limit, offset = pagination(request)

    except ValueError as exc:
        return error(str(exc))

    qs = visible_items()

    level = (
        request.GET.get("level", "")
        .strip()
        .upper()
    )

    topic_code = (
        request.GET.get("topic", "")
        .strip()
    )

    search = (
        request.GET.get("q", "")
        .strip()
    )

    if level and level not in ("A1", "A2"):
        return error(
            "invalid_level"
        )

    # Filter by Topic, if provided.
    if topic_code:

        topic_exists = VocabularyTopic.objects.filter(
            code=topic_code,
            is_active=True,
        ).exists()

        if not topic_exists:
            return error(
                "topic_not_found",
                404,
            )

        qs = qs.filter(
            topic_links__topic__code=topic_code,
        )

    # Critical: filter via A1/A2 membership,
    # not via VocabularyItem.cefr_level.
    if level:

        qs = qs.filter(
            topic_links__topic__code=(
                f"level-{level.lower()}"
            )
        )

    # Search Russian, Vietnamese or English.
    if search:

        if len(search) > 100:
            return error(
                "search_too_long"
            )

        qs = qs.filter(
            Q(lemma__icontains=search)
            | Q(stress_marked__icontains=search)
            | Q(meaning_vi__icontains=search)
            | Q(meaning_en__icontains=search)
        )

    # Preserve Excel order for level browsing.
    rank_topic = (
        topic_code
        or (
            f"level-{level.lower()}"
            if level
            else None
        )
    )

    if rank_topic:

        rank = (
            VocabularyItemTopic.objects
            .filter(
                item_id=OuterRef("pk"),
                topic__code=rank_topic,
            )
            .values("display_order")[:1]
        )

        qs = (
            qs.annotate(
                source_order=Subquery(rank)
            )
            .order_by(
                "source_order",
                "source_id",
            )
        )

    else:
        qs = qs.order_by(
            "cefr_level",
            "lemma",
            "source_id",
        )

    total = qs.count()

    page = list(
        qs.prefetch_related("topics")[
            offset:offset + limit
        ]
    )

    states = {
        state.item_id: state
        for state in (
            VocabularySRSState.objects.filter(
                user=request.user,
                item_id__in=[
                    item.pk
                    for item in page
                ],
            )
        )
    }

    return reply({
        "total": total,
        "limit": limit,
        "offset": offset,

        "items": [
            item_json(
                item,
                states.get(item.pk),
                level,
            )
            for item in page
        ],
    })


# ============================================================
# GET /api/vocabulary/items/<source_id>/
# ============================================================

@require_GET
@authenticated
def item_detail(request, source_id):

    item = visible_item(source_id)

    if item is None:
        return error(
            "item_not_found",
            404,
        )

    level = (
        request.GET.get("level", "")
        .strip()
        .upper()
    )

    if level and level not in ("A1", "A2"):
        return error(
            "invalid_level"
        )

    state = VocabularySRSState.objects.filter(
        user=request.user,
        item=item,
    ).first()

    return reply({
        "item": item_json(
            item,
            state,
            level,
        ),
    })


# ============================================================
# POST /items/<source_id>/start/
# ============================================================

@csrf_protect
@require_POST
@authenticated
def start_item(request, source_id):

    item = visible_item(source_id)

    if item is None:
        return error(
            "item_not_found",
            404,
        )

    try:
        data = body_json(request)

        if data != {}:
            return error(
                "expected_empty_json_object"
            )

        state, created = start_learning(
            user=request.user,
            item=item,
        )

    except ValueError as exc:
        return error(str(exc))

    except VocabularyError as exc:
        return error(
            exc.code,
            exc.status,
        )

    return reply(
        {
            "source_id": source_id,
            "created": created,
            "srs": state_json(state),
        },
        status=201 if created else 200,
    )


# ============================================================
# GET /reviews/due/
# ============================================================

@require_GET
@authenticated
def due_reviews(request):

    try:
        limit, offset = pagination(
            request,
            maximum=policy()["max_due_per_request"],
        )

    except ValueError as exc:
        return error(str(exc))

    qs = (
        VocabularySRSState.objects
        .filter(
            user=request.user,
            item__is_active=True,
            item__topic_links__topic__is_active=True,
            next_review_at__lte=timezone.now(),
        )
        .distinct()
        .order_by(
            "next_review_at",
            "pk",
        )
    )

    total = qs.count()

    page = list(
        qs.select_related("item")
        .prefetch_related("item__topics")[
            offset:offset + limit
        ]
    )

    return reply({
        "total": total,
        "limit": limit,
        "offset": offset,

        "items": [
            item_json(state.item, state)
            for state in page
        ],
    })


# ============================================================
# GET /reviews/summary/
# ============================================================

@require_GET
@authenticated
def review_summary(request):

    from .services import _utc_bounds

    now = timezone.now()

    start, end = _utc_bounds(now)

    states = (
        VocabularySRSState.objects
        .filter(
            user=request.user,
            item__is_active=True,
            item__topic_links__topic__is_active=True,
        )
        .distinct()
    )

    new_today = (
        VocabularySRSState.objects
        .filter(
            user=request.user,
            learned_at__gte=start,
            learned_at__lt=end,
        )
        .count()
    )

    return reply({
        "learned_items": states.count(),

        "due_items": states.filter(
            next_review_at__lte=now
        ).count(),

        "new_today_utc": new_today,

        "new_remaining_today_utc": max(
            0,
            policy()["max_new_per_day"] - new_today,
        ),

        "review_count": (
            VocabularyReviewLog.objects
            .filter(user=request.user)
            .count()
        ),
    })


# ============================================================
# POST /reviews/
# ============================================================

@csrf_protect
@require_POST
@authenticated
def submit_review_view(request):

    try:
        data = body_json(request)

    except ValueError as exc:
        return error(str(exc))

    source_id = data.get("source_id")
    raw_id = data.get("request_id")

    if (
        not isinstance(source_id, str)
        or not source_id
    ):
        return error("source_id_required")

    if not isinstance(raw_id, str):
        return error("request_id_required")

    try:
        request_id = UUID(raw_id)

    except (ValueError, AttributeError):
        return error("invalid_request_id")

    allowed = {
        "source_id",
        "request_id",
        "rating",
        "expected_version",
        "response_time_ms",
    }

    if set(data) - allowed:
        return error("unexpected_review_fields")

    item = VocabularyItem.objects.filter(
        source_id=source_id,
    ).first()

    if item is None:
        return error(
            "item_not_found",
            404,
        )

    try:
        log = submit_review(
            user=request.user,
            item=item,
            request_id=request_id,
            rating=data.get("rating"),
            expected_version=data.get(
                "expected_version"
            ),
            response_time_ms=data.get(
                "response_time_ms"
            ),
        )

    except VocabularyError as exc:
        return error(
            exc.code,
            exc.status,
        )

    return reply({
        "review": receipt(log),
    })


# ============================================================
# GET /reviews/history/
# ============================================================

@require_GET
@authenticated
def review_history(request):

    try:
        limit, offset = pagination(request)

    except ValueError as exc:
        return error(str(exc))

    qs = (
        VocabularyReviewLog.objects
        .filter(user=request.user)
        .select_related("item")
    )

    total = qs.count()

    return reply({
        "total": total,
        "limit": limit,
        "offset": offset,

        "items": [
            receipt(log)
            for log in qs[
                offset:offset + limit
            ]
        ],
    })
