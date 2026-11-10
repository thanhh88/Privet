"""Read-only Alphabet catalog used by both learning modes.

No ORM models, sessions, events, mastery or SRS writes.
"""
import json
import re
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.templatetags.static import static

CATALOG_PATH = Path(__file__).resolve().parent / "data" / "alphabet" / "alphabet.json"
STATIC_PREFIX = "curriculum/audio/alphabet"
SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]*$")


def load_catalog():
    """Load and validate the single source of truth for all 33 letters."""
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImproperlyConfigured(f"Cannot read Alphabet catalog: {exc}") from exc

    letters = data.get("letters")
    lessons = data.get("lessons")
    if not isinstance(letters, list) or len(letters) != 33:
        raise ImproperlyConfigured("Alphabet must contain exactly 33 letters.")
    if not isinstance(lessons, list) or len(lessons) != 7:
        raise ImproperlyConfigured("Alphabet must contain exactly 7 lessons.")

    seen_letters = set()
    for letter in letters:
        key = letter.get("id", "")
        if not isinstance(key, str) or not SAFE_ID.fullmatch(key):
            raise ImproperlyConfigured(f"Unsafe letter id: {key!r}")
        if key in seen_letters:
            raise ImproperlyConfigured(f"Repeated letter id: {key}")
        seen_letters.add(key)
        required = ("upper", "lower", "name_ru", "kind", "sound_sample",
                    "sound_vi", "guide_vi")
        if not all(isinstance(letter.get(field), str) and letter[field] for field in required):
            raise ImproperlyConfigured(f"Incomplete letter: {key}")
        if letter["kind"] not in ("vowel", "consonant", "sign"):
            raise ImproperlyConfigured(f"Invalid letter kind: {key}")
        if not isinstance(letter.get("blends"), list) or not letter["blends"]:
            raise ImproperlyConfigured(f"Missing blends: {key}")
        if not isinstance(letter.get("examples"), list) or not 1 <= len(letter["examples"]) <= 2:
            raise ImproperlyConfigured(f"Need 1-2 examples: {key}")
        for blend in letter["blends"]:
            if isinstance(blend, str) and blend:
                continue
            if isinstance(blend, dict) and blend.get("text") and blend.get("tts"):
                continue
            raise ImproperlyConfigured(f"Invalid blend: {key}")
        if any(not item.get("ru") or not item.get("vi") for item in letter["examples"]):
            raise ImproperlyConfigured(f"Invalid example: {key}")

    covered = []
    seen_lessons = set()
    for lesson in lessons:
        lesson_id = lesson.get("id", "")
        if not isinstance(lesson_id, str) or not SAFE_ID.fullmatch(lesson_id):
            raise ImproperlyConfigured(f"Unsafe lesson id: {lesson_id!r}")
        if lesson_id in seen_lessons:
            raise ImproperlyConfigured(f"Repeated lesson id: {lesson_id}")
        seen_lessons.add(lesson_id)
        if not lesson.get("title") or not lesson.get("objective"):
            raise ImproperlyConfigured(f"Incomplete lesson: {lesson_id}")
        members = lesson.get("letter_ids")
        if not isinstance(members, list) or not members:
            raise ImproperlyConfigured(f"Empty lesson: {lesson_id}")
        covered.extend(members)

    if len(covered) != 33 or set(covered) != seen_letters or len(set(covered)) != 33:
        raise ImproperlyConfigured("Every letter must belong to exactly one lesson.")

    return data


def get_audio_path(letter_id, filename):
    return f"{STATIC_PREFIX}/{letter_id}/{filename}"


def get_audio_url(letter_id, filename):
    return static(get_audio_path(letter_id, filename))


def serialize_letter(letter):
    """Provide the same letter details to both table and lesson screens."""
    key = letter["id"]
    blends = []
    for index, blend in enumerate(letter["blends"], start=1):
        text = blend if isinstance(blend, str) else blend["text"]
        blends.append({
            "text": text,
            "audio_url": get_audio_url(key, f"blend-{index:02d}.mp3"),
        })

    return {
        "id": key,
        "upper": letter["upper"],
        "lower": letter["lower"],
        "name_ru": letter["name_ru"],
        "kind": letter["kind"],
        "sound_sample": letter["sound_sample"],
        "sound_vi": letter["sound_vi"],
        "guide_vi": letter["guide_vi"],
        "audio": {
            "name_url": get_audio_url(key, "name.mp3"),
            "sound_url": get_audio_url(key, "sound.mp3"),
            "guide_url": get_audio_url(key, "guide.mp3"),
        },
        "blends": blends,
        "examples": [
            {
                "ru": word["ru"],
                "vi": word["vi"],
                "audio_url": get_audio_url(key, f"word-{index:02d}.mp3"),
            }
            for index, word in enumerate(letter["examples"], start=1)
        ],
    }


def strip_stress(text):
    """Remove the display-only combining acute accent before Russian TTS."""
    return text.replace("\u0301", "")


def iter_audio_jobs(catalog, only_letter=None):
    """Yield (relative_static_path, spoken_text, language) for batch TTS."""
    for letter in catalog["letters"]:
        key = letter["id"]
        if only_letter is not None and key != only_letter:
            continue
        yield get_audio_path(key, "name.mp3"), letter["name_ru"], "ru"
        yield get_audio_path(key, "sound.mp3"), strip_stress(letter["sound_sample"]), "ru"
        yield get_audio_path(key, "guide.mp3"), letter["guide_vi"], "vi"
        for index, blend in enumerate(letter["blends"], start=1):
            spoken = blend if isinstance(blend, str) else blend["tts"]
            yield get_audio_path(key, f"blend-{index:02d}.mp3"), strip_stress(spoken), "ru"
        for index, word in enumerate(letter["examples"], start=1):
            yield get_audio_path(key, f"word-{index:02d}.mp3"), strip_stress(word["ru"]), "ru"
