import re
import unicodedata
from decimal import Decimal

from curriculum.taxonomy import SKILLS
from django.core.exceptions import ValidationError

from questionbank.validation import (
    validate_item_format,
    validate_options,
    validate_skill_rows,
)


SCHEMA_VERSION = 1
BANK_NAME = "privet-core"

SOURCE_ID_RE = re.compile(
    r"^[A-Z0-9][A-Z0-9._-]*$"
)

SKILL_CODE_RE = re.compile(
    r"^[A-Z0-9][A-Z0-9._-]*$"
)

OPTION_KEYS = ("A", "B", "C")

KNOWN_SKILL_CODES = frozenset(
    row["skill_code"]
    for row in SKILLS
)


QUESTION_TYPE_MAP = {
    # Grammar
    "single_choice": "multiple_choice",
    "contextual_choice": "multiple_choice",
    "fill_choice": "fill_choice",

    # Vocabulary
    "ru_to_vi": "multiple_choice",
    "vi_to_ru": "multiple_choice",
    "definition_to_ru": "multiple_choice",
    "cloze_ru": "fill_choice",
}


CEFR_MAP = {
    "A1": ("A1", False),
    "A2": ("A2", False),
    "B1_extension": ("B1", True),
}


DIFFICULTY_MAP = {
    "easy": Decimal("0.2500"),
    "medium": Decimal("0.5000"),
    "hard": Decimal("0.7500"),
}


PRIMARY_ONLY_WEIGHT = Decimal("1.00000")

PRIMARY_WITH_SECONDARY_WEIGHT = Decimal("0.70000")

SECONDARY_WEIGHT = Decimal("0.30000")

CANONICAL_TOP_LEVEL_KEYS = {
    "schema_version",
    "bank",
    "questions",
}

CANONICAL_QUESTION_KEYS = {
    "source_id",
    "question_type",
    "item_format",
    "question_text",
    "cefr_level",
    "is_extension",
    "difficulty_score",
    "correct_answer",
    "accepted_answers",
    "explanation",
    "audio_url",
    "skills",
    "options",
}

REQUIRED_CANONICAL_QUESTION_KEYS = {
    "source_id",
    "question_type",
    "item_format",
    "question_text",
    "cefr_level",
    "is_extension",
    "difficulty_score",
    "skills",
    "options",
}

CANONICAL_SKILL_KEYS = {
    "skill_code",
    "is_primary",
    "weight",
}

CANONICAL_OPTION_KEYS = {
    "option_key",
    "option_text",
    "is_correct",
    "misconception_code",
}

class ConversionError(ValueError):
    pass


def normalize_text(
    value,
    *,
    field_name,
):
    """
    Normalize required human-readable text.

    Rules:
    - must be str
    - Unicode NFC
    - strip outer whitespace
    - must remain non-empty
    """
    if not isinstance(value, str):
        raise ConversionError(
            f"{field_name} must be a string."
        )

    value = unicodedata.normalize(
        "NFC",
        value,
    ).strip()

    if not value:
        raise ConversionError(
            f"{field_name} must not be empty."
        )

    return value


def normalize_optional_text(
    value,
    *,
    field_name,
):
    """
    Normalize optional text.

    None remains None.
    If provided, text must be a non-empty string.
    """
    if value is None:
        return None

    return normalize_text(
        value,
        field_name=field_name,
    )


def normalize_identifier(
    value,
    *,
    field_name,
    pattern,
    max_length,
):
    """
    Normalize an identifier without changing case.

    Identifiers are never uppercased or case-folded automatically.
    """
    value = normalize_text(
        value,
        field_name=field_name,
    )

    if len(value) > max_length:
        raise ConversionError(
            f"{field_name} exceeds max length "
            f"{max_length}: {value!r}."
        )

    if pattern.fullmatch(value) is None:
        raise ConversionError(
            f"Invalid {field_name}: {value!r}."
        )

    return value


def convert_master_question(record):
    if not isinstance(record, dict):
        raise ConversionError(
            "Each master question must be an object."
        )

    source_id = normalize_identifier(
        record.get("id"),
        field_name="id",
        pattern=SOURCE_ID_RE,
        max_length=128,
    )

    def fail(message):
        raise ConversionError(
            f"[{source_id}] {message}"
        )

    master_type = record.get(
        "question_type"
    )

    if master_type not in QUESTION_TYPE_MAP:
        fail(
            "Unsupported master question_type: "
            f"{master_type!r}."
        )

    question_type = QUESTION_TYPE_MAP[
        master_type
    ]

    item_format = master_type

    cefr = record.get("cefr")

    if cefr not in CEFR_MAP:
        fail(
            f"Unsupported CEFR value: {cefr!r}."
        )

    cefr_level, is_extension = CEFR_MAP[
        cefr
    ]

    difficulty = record.get(
        "difficulty"
    )

    if difficulty not in DIFFICULTY_MAP:
        fail(
            "Unsupported difficulty: "
            f"{difficulty!r}."
        )

    question_text = normalize_text(
        record.get("prompt"),
        field_name=f"{source_id}.prompt",
    )

    explanation = normalize_optional_text(
        record.get("explanation"),
        field_name=f"{source_id}.explanation",
    )

    primary_skill = normalize_identifier(
        record.get("skill"),
        field_name=f"{source_id}.skill",
        pattern=SKILL_CODE_RE,
        max_length=64,
    )

    if primary_skill not in KNOWN_SKILL_CODES:
        fail(
            "Unknown primary skill_code: "
            f"{primary_skill!r}."
        )

    secondary_raw = record.get(
        "secondary_skills"
    )

    secondary_skill = None

    if secondary_raw is not None:
        if not isinstance(
            secondary_raw,
            str,
        ):
            fail(
                "secondary_skills must be null "
                "or one skill-code string in v1."
            )

        secondary_skill = normalize_identifier(
            secondary_raw,
            field_name=(
                f"{source_id}.secondary_skills"
            ),
            pattern=SKILL_CODE_RE,
            max_length=64,
        )

        if secondary_skill not in KNOWN_SKILL_CODES:
            fail(
                "Unknown secondary skill_code: "
                f"{secondary_skill!r}."
            )

        if secondary_skill == primary_skill:
            fail(
                "Secondary skill must differ "
                "from primary skill."
            )

    if secondary_skill is None:
        skills = [
            {
                "skill_code": primary_skill,
                "is_primary": True,
                "weight": float(
                    PRIMARY_ONLY_WEIGHT
                ),
            }
        ]

    else:
        skills = [
            {
                "skill_code": primary_skill,
                "is_primary": True,
                "weight": float(
                    PRIMARY_WITH_SECONDARY_WEIGHT
                ),
            },
            {
                "skill_code": secondary_skill,
                "is_primary": False,
                "weight": float(
                    SECONDARY_WEIGHT
                ),
            },
        ]

    correct_key = record.get("correct")

    if correct_key not in OPTION_KEYS:
        fail(
            "correct must be one of "
            "'A', 'B', 'C'."
        )

    options = []

    for option_key in OPTION_KEYS:
        option_text = normalize_text(
            record.get(
                f"option_{option_key}"
            ),
            field_name=(
                f"{source_id}."
                f"option_{option_key}"
            ),
        )

        is_correct = (
            option_key == correct_key
        )

        misconception = (
            record.get(
                f"misconception_{option_key}"
            )
        )

        if misconception is not None:
            misconception = (
                normalize_text(
                    misconception,
                    field_name=(
                        f"{source_id}."
                        f"misconception_{option_key}"
                    ),
                )
            )

        # Master-data QA invariant:
        # correct option must not have a misconception label.
        if is_correct and misconception is not None:
            fail(
                f"Correct option {option_key} "
                "must not have a "
                "misconception label."
            )

        # Master-data QA invariant:
        # every distractor currently must be diagnosed.
        if (
            not is_correct
            and misconception is None
        ):
            fail(
                f"Incorrect option {option_key} "
                "must have a "
                "misconception label."
            )

        options.append(
            {
                "option_key": option_key,
                "option_text": option_text,
                "is_correct": is_correct,
                "misconception_code": (
                    misconception
                ),
            }
        )

    correct_text = normalize_text(
        record.get("correct_text"),
        field_name=(
            f"{source_id}.correct_text"
        ),
    )

    actual_correct_text = next(
        option["option_text"]
        for option in options
        if option["is_correct"]
    )

    if correct_text != actual_correct_text:
        fail(
            "correct_text does not match "
            f"option_{correct_key}. "
            f"correct_text={correct_text!r}, "
            f"option={actual_correct_text!r}."
        )

    return {
        "source_id": source_id,
        "question_type": question_type,
        "item_format": item_format,
        "question_text": question_text,
        "cefr_level": cefr_level,
        "is_extension": is_extension,
        "difficulty_score": float(
            DIFFICULTY_MAP[difficulty]
        ),
        "explanation": explanation,
        "audio_url": None,
        "skills": skills,
        "options": options,
    }


def convert_master_documents(
    documents,
):
    """
    Convert multiple master documents into one canonical bank.

    documents:
        iterable of (source_name, parsed_json)

    Each master document must be a top-level JSON array.
    """

    questions = []
    seen_source_ids = set()

    for source_name, document in documents:
        if not isinstance(document, list):
            raise ConversionError(
                f"{source_name}: top-level JSON "
                "must be an array."
            )

        for index, record in enumerate(
            document,
            start=1,
        ):
            try:
                question = (
                    convert_master_question(
                        record
                    )
                )
            except ConversionError as exc:
                raise ConversionError(
                    f"{source_name}, item "
                    f"{index}: {exc}"
                ) from exc

            source_id = question[
                "source_id"
            ]

            if source_id in seen_source_ids:
                raise ConversionError(
                    "Duplicate source_id across "
                    "master documents: "
                    f"{source_id!r}."
                )

            seen_source_ids.add(
                source_id
            )

            questions.append(
                question
            )

    # Deterministic output independent of input-file order.
    questions.sort(
        key=lambda question: (
            question["source_id"]
        )
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "bank": BANK_NAME,
        "questions": questions,
    }

def _reject_unknown_keys(
    obj,
    *,
    allowed_keys,
    context,
):
    unknown = set(obj) - allowed_keys

    if unknown:
        raise ConversionError(
            f"{context}: unknown field(s): "
            + ", ".join(sorted(unknown))
        )


def _require_keys(
    obj,
    *,
    required_keys,
    context,
):
    missing = required_keys - set(obj)

    if missing:
        raise ConversionError(
            f"{context}: missing required field(s): "
            + ", ".join(sorted(missing))
        )


def _to_decimal(value, *, context):
    if isinstance(value, bool):
        raise ConversionError(
            f"{context} must be numeric."
        )

    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ConversionError(
            f"{context} must be numeric."
        ) from exc


def validate_canonical_bank(bank):
    """
    Validate canonical Question Bank v1 without touching the database.

    This validator is intentionally reusable by:
    - the master -> canonical builder
    - the future importer
    """

    if not isinstance(bank, dict):
        raise ConversionError(
            "Canonical bank must be a JSON object."
        )

    _require_keys(
        bank,
        required_keys=CANONICAL_TOP_LEVEL_KEYS,
        context="bank",
    )

    _reject_unknown_keys(
        bank,
        allowed_keys=CANONICAL_TOP_LEVEL_KEYS,
        context="bank",
    )

    if bank["schema_version"] != SCHEMA_VERSION:
        raise ConversionError(
            "Unsupported schema_version: "
            f"{bank['schema_version']!r}."
        )

    if bank["bank"] != BANK_NAME:
        raise ConversionError(
            f'Expected bank="{BANK_NAME}", '
            f'found {bank["bank"]!r}.'
        )

    questions = bank["questions"]

    if not isinstance(questions, list):
        raise ConversionError(
            "bank.questions must be an array."
        )

    seen_source_ids = set()

    for index, question in enumerate(
        questions,
        start=1,
    ):
        context = f"questions[{index}]"

        if not isinstance(question, dict):
            raise ConversionError(
                f"{context} must be an object."
            )

        _require_keys(
            question,
            required_keys=REQUIRED_CANONICAL_QUESTION_KEYS,
            context=context,
        )

        _reject_unknown_keys(
            question,
            allowed_keys=CANONICAL_QUESTION_KEYS,
            context=context,
        )

        source_id = question["source_id"]

        if not isinstance(source_id, str):
            raise ConversionError(
                f"{context}.source_id must be a string."
            )

        if (
            source_id != source_id.strip()
            or unicodedata.normalize("NFC", source_id)
            != source_id
        ):
            raise ConversionError(
                f"{context}.source_id is not normalized."
            )

        if len(source_id) > 128:
            raise ConversionError(
                f"{context}.source_id exceeds 128 characters."
            )

        if SOURCE_ID_RE.fullmatch(source_id) is None:
            raise ConversionError(
                f"{context}: invalid source_id "
                f"{source_id!r}."
            )

        if source_id in seen_source_ids:
            raise ConversionError(
                f"Duplicate canonical source_id: "
                f"{source_id!r}."
            )

        seen_source_ids.add(source_id)

        question_type = question["question_type"]
        item_format = question["item_format"]

        try:
            validate_item_format(
                question_type,
                item_format,
            )
        except ValidationError as exc:
            raise ConversionError(
                f"[{source_id}] invalid item_format: {exc}"
            ) from exc

        if question_type not in {
            "multiple_choice",
            "fill_choice",
            "fill_blank",
        }:
            raise ConversionError(
                f"[{source_id}] unsupported canonical "
                f"question_type: {question_type!r}."
            )

        if question["cefr_level"] not in {
            "A1",
            "A2",
            "B1",
        }:
            raise ConversionError(
                f"[{source_id}] invalid cefr_level: "
                f"{question['cefr_level']!r}."
            )

        if not isinstance(
            question["is_extension"],
            bool,
        ):
            raise ConversionError(
                f"[{source_id}] is_extension "
                "must be boolean."
            )

        if (
            question["is_extension"]
            and question["cefr_level"] != "B1"
        ):
            raise ConversionError(
                f"[{source_id}] extension questions "
                'must use cefr_level="B1".'
            )

        difficulty = _to_decimal(
            question["difficulty_score"],
            context=(
                f"[{source_id}] difficulty_score"
            ),
        )

        if not (
            Decimal("0")
            <= difficulty
            <= Decimal("1")
        ):
            raise ConversionError(
                f"[{source_id}] difficulty_score "
                "must be between 0 and 1."
            )

        question_text = question["question_text"]

        if (
            not isinstance(question_text, str)
            or not question_text.strip()
        ):
            raise ConversionError(
                f"[{source_id}] question_text "
                "must be a non-empty string."
            )

        skills = question["skills"]

        if not isinstance(skills, list):
            raise ConversionError(
                f"[{source_id}] skills must be an array."
            )

        normalized_skill_rows = []

        for skill_index, skill_row in enumerate(
            skills,
            start=1,
        ):
            skill_context = (
                f"[{source_id}] skills[{skill_index}]"
            )

            if not isinstance(skill_row, dict):
                raise ConversionError(
                    f"{skill_context} must be an object."
                )

            _require_keys(
                skill_row,
                required_keys=CANONICAL_SKILL_KEYS,
                context=skill_context,
            )

            _reject_unknown_keys(
                skill_row,
                allowed_keys=CANONICAL_SKILL_KEYS,
                context=skill_context,
            )

            skill_code = skill_row["skill_code"]

            if (
                not isinstance(skill_code, str)
                or SKILL_CODE_RE.fullmatch(
                    skill_code
                ) is None
            ):
                raise ConversionError(
                    f"{skill_context}: invalid skill_code."
                )

            if skill_code not in KNOWN_SKILL_CODES:
                raise ConversionError(
                    f"{skill_context}: unknown skill_code "
                    f"{skill_code!r}."
                )

            if not isinstance(
                skill_row["is_primary"],
                bool,
            ):
                raise ConversionError(
                    f"{skill_context}.is_primary "
                    "must be boolean."
                )

            weight = _to_decimal(
                skill_row["weight"],
                context=f"{skill_context}.weight",
            )

            if not (
                Decimal("0")
                <= weight
                <= Decimal("1")
            ):
                raise ConversionError(
                    f"{skill_context}.weight "
                    "must be between 0 and 1."
                )

            normalized_skill_rows.append(
                {
                    "skill": skill_code,
                    "is_primary": (
                        skill_row["is_primary"]
                    ),
                    "weight": weight,
                }
            )

        try:
            validate_skill_rows(
                normalized_skill_rows
            )
        except ValidationError as exc:
            raise ConversionError(
                f"[{source_id}] invalid skills: {exc}"
            ) from exc

        options = question["options"]

        if not isinstance(options, list):
            raise ConversionError(
                f"[{source_id}] options must be an array."
            )

        for option_index, option in enumerate(
            options,
            start=1,
        ):
            option_context = (
                f"[{source_id}] options[{option_index}]"
            )

            if not isinstance(option, dict):
                raise ConversionError(
                    f"{option_context} must be an object."
                )

            _require_keys(
                option,
                required_keys=CANONICAL_OPTION_KEYS,
                context=option_context,
            )

            _reject_unknown_keys(
                option,
                allowed_keys=CANONICAL_OPTION_KEYS,
                context=option_context,
            )

        try:
            validate_options(
                question_type,
                options,
            )
        except ValidationError as exc:
            raise ConversionError(
                f"[{source_id}] invalid options: {exc}"
            ) from exc