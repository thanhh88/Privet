
import json
import re

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from .models import VocabularyItem, VocabularySavedWord


SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def _json(payload, status=200):
    return JsonResponse(
        payload,
        status=status,
        json_dumps_params={"ensure_ascii": False},
    )


@require_GET
def saved_status(request):
    """
    GET /api/vocabulary/saved/status/?ids=...
    Returns saved IDs for the current user.
    """

    if not request.user.is_authenticated:
        return _json(
            {"error": "authentication_required"},
            401,
        )

    source_ids = request.GET.getlist("ids")

    if len(source_ids) > 50 or any(
        not SOURCE_ID.fullmatch(source_id)
        for source_id in source_ids
    ):
        return _json({"error": "invalid_ids"}, 400)

    source_ids = list(dict.fromkeys(source_ids))

    saved_ids = list(
        VocabularySavedWord.objects.filter(
            user=request.user,
            item__source_id__in=source_ids,
        ).values_list("item__source_id", flat=True)
    )

    return _json({"saved_ids": saved_ids})


@csrf_protect
@require_POST
def saved_toggle(request):
    """
    POST /api/vocabulary/saved/

    Body:
    {
        "source_id": "ru-...",
        "saved": true
    }

    Idempotent: repeated save does not duplicate a row.
    """

    if not request.user.is_authenticated:
        return _json(
            {"error": "authentication_required"},
            401,
        )

    if (
        request.content_type != "application/json"
        or len(request.body) > 4096
    ):
        return _json(
            {"error": "invalid_content_type_or_size"},
            400,
        )

    try:
        data = json.loads(request.body)
    except (UnicodeDecodeError, ValueError):
        return _json({"error": "invalid_json"}, 400)

    if (
        not isinstance(data, dict)
        or set(data) != {"source_id", "saved"}
    ):
        return _json({"error": "invalid_payload"}, 400)

    source_id = data["source_id"]
    desired = data["saved"]

    if (
        not isinstance(source_id, str)
        or not SOURCE_ID.fullmatch(source_id)
        or type(desired) is not bool
    ):
        return _json({"error": "invalid_payload"}, 400)

    item = VocabularyItem.objects.filter(
        source_id=source_id,
        is_active=True,
        topic_links__topic__is_active=True,
    ).first()

    if item is None:
        return _json({"error": "item_not_found"}, 404)

    if desired:
        VocabularySavedWord.objects.get_or_create(
            user=request.user,
            item=item,
        )
    else:
        VocabularySavedWord.objects.filter(
            user=request.user,
            item=item,
        ).delete()

    return _json({
        "source_id": source_id,
        "saved": desired,
    })
