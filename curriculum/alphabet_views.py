"""Read-only JSON API for Privet Alphabet (no database writes)."""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET

from .alphabet_catalog import load_catalog, serialize_letter


def respond(data, status=200):
    return JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False})


@login_required
@require_GET
def overview(request):
    catalog = load_catalog()
    return respond({
        "version": catalog["version"],
        "total_letters": len(catalog["letters"]),
        "modes": [
            {
                "id": "table",
                "title": "Học cả bảng",
                "api_url": reverse("alphabet:table"),
            },
            {
                "id": "lessons",
                "title": "Học theo từng bài",
                "api_url": reverse("alphabet:overview"),
            },
        ],
        "lessons": [
            {
                "id": lesson["id"],
                "title": lesson["title"],
                "objective": lesson["objective"],
                "letter_count": len(lesson["letter_ids"]),
                "letter_ids": lesson["letter_ids"],
                "api_url": reverse("alphabet:lesson", args=[lesson["id"]]),
            }
            for lesson in catalog["lessons"]
        ],
    })


@login_required
@require_GET
def table(request):
    catalog = load_catalog()
    return respond({
        "version": catalog["version"],
        "mode": "table",
        "total_letters": len(catalog["letters"]),
        "letters": [serialize_letter(letter) for letter in catalog["letters"]],
    })


@login_required
@require_GET
def lesson(request, lesson_id):
    catalog = load_catalog()
    lessons = catalog["lessons"]
    position = next((i for i, item in enumerate(lessons) if item["id"] == lesson_id), None)
    if position is None:
        return respond({"error": "Bài Alphabet không tồn tại."}, status=404)

    item = lessons[position]
    letter_by_id = {letter["id"]: letter for letter in catalog["letters"]}
    return respond({
        "version": catalog["version"],
        "mode": "lesson",
        "lesson": {
            "id": item["id"],
            "title": item["title"],
            "objective": item["objective"],
            "position": position + 1,
            "total_lessons": len(lessons),
            "previous_url": (
                reverse("alphabet:lesson", args=[lessons[position - 1]["id"]])
                if position > 0 else None
            ),
            "next_url": (
                reverse("alphabet:lesson", args=[lessons[position + 1]["id"]])
                if position < len(lessons) - 1 else None
            ),
        },
        "letters": [serialize_letter(letter_by_id[key]) for key in item["letter_ids"]],
    })


@login_required
@require_GET
def letter(request, letter_id):
    catalog = load_catalog()
    item = next((item for item in catalog["letters"] if item["id"] == letter_id), None)
    if item is None:
        return respond({"error": "Chữ cái không tồn tại."}, status=404)
    return respond({
        "version": catalog["version"],
        "letter": serialize_letter(item),
    })
