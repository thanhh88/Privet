import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import (
    IntegrityError,
    connection,
    transaction,
)

from curriculum.models import Skill
from questionbank.conversion import (
    BANK_NAME,
    CANONICAL_TOP_LEVEL_KEYS,
    SCHEMA_VERSION,
    ConversionError,
    validate_canonical_bank,
)
from questionbank.models import (
    Question,
    QuestionOption,
    QuestionSkill,
)
from questionbank.validation import (
    validate_options,
    validate_skill_rows,
)

FORCE_QS_CHECKS = (
    "SET CONSTRAINTS "
    "trg_qs_integrity_deferred, "
    "trg_question_skill_required_deferred "
    "IMMEDIATE"
)

RESTORE_QS_CHECKS = (
    "SET CONSTRAINTS "
    "trg_qs_integrity_deferred, "
    "trg_question_skill_required_deferred "
    "DEFERRED"
)

@dataclass
class InputFileInfo:
    path: str
    sha256: str
    question_count: int


@dataclass
class PreparedQuestion:
    source_id: str
    action: str  # CREATE / UPDATE
    existing_question_id: int | None
    question_data: dict
    skill_rows: list
    options: list


@dataclass
class Phase1Result:
    files: list
    prepared_questions: list
    errors: list

    @property
    def create_count(self):
        return sum(
            item.action == "CREATE"
            for item in self.prepared_questions
        )

    @property
    def update_count(self):
        return sum(
            item.action == "UPDATE"
            for item in self.prepared_questions
        )

    @property
    def question_count(self):
        return len(
            self.prepared_questions
        )

    @property
    def is_valid(self):
        return not self.errors

@dataclass
class Phase2Result:
    question_count: int
    create_count: int
    update_count: int
    dry_run: bool

class ImportValidationError(ValueError):
    pass

class ImportPersistenceError(RuntimeError):
    pass

def _sha256(raw_bytes):
    return hashlib.sha256(
        raw_bytes
    ).hexdigest()


def _read_canonical_file(path):
    """
    Parse one canonical JSON file.

    File-level failures are fatal:
    - missing file
    - invalid UTF-8
    - invalid JSON
    - invalid top-level canonical envelope
    """

    path = Path(path)

    if not path.is_file():
        raise ImportValidationError(
            f"File does not exist: {path}"
        )

    raw_bytes = path.read_bytes()
    sha256 = _sha256(raw_bytes)

    try:
        text = raw_bytes.decode(
            "utf-8-sig"
        )
    except UnicodeDecodeError as exc:
        raise ImportValidationError(
            f"{path}: file must be UTF-8."
        ) from exc

    try:
        document = json.loads(
            text,
            parse_float=Decimal,
        )
    except json.JSONDecodeError as exc:
        raise ImportValidationError(
            f"{path}: invalid JSON at "
            f"line {exc.lineno}, "
            f"column {exc.colno}: "
            f"{exc.msg}"
        ) from exc

    if not isinstance(document, dict):
        raise ImportValidationError(
            f"{path}: canonical document "
            "must be a JSON object."
        )

    unknown_keys = (
        set(document)
        - CANONICAL_TOP_LEVEL_KEYS
    )

    if unknown_keys:
        raise ImportValidationError(
            f"{path}: unknown top-level field(s): "
            + ", ".join(
                sorted(unknown_keys)
            )
        )

    missing_keys = (
        CANONICAL_TOP_LEVEL_KEYS
        - set(document)
    )

    if missing_keys:
        raise ImportValidationError(
            f"{path}: missing top-level field(s): "
            + ", ".join(
                sorted(missing_keys)
            )
        )

    if document["schema_version"] != SCHEMA_VERSION:
        raise ImportValidationError(
            f"{path}: unsupported schema_version "
            f"{document['schema_version']!r}; "
            f"expected {SCHEMA_VERSION}."
        )

    if document["bank"] != BANK_NAME:
        raise ImportValidationError(
            f"{path}: unexpected bank "
            f"{document['bank']!r}; "
            f"expected {BANK_NAME!r}."
        )

    questions = document["questions"]

    if not isinstance(questions, list):
        raise ImportValidationError(
            f"{path}: questions must be an array."
        )

    return (
        document,
        InputFileInfo(
            path=str(path),
            sha256=sha256,
            question_count=len(
                questions
            ),
        ),
    )


def _validate_one_canonical_question(
    question,
):
    """
    Reuse the canonical contract while allowing
    Phase 1 to collect errors question-by-question.
    """

    single_question_bank = {
        "schema_version": SCHEMA_VERSION,
        "bank": BANK_NAME,
        "questions": [question],
    }

    validate_canonical_bank(
        single_question_bank
    )


def _question_model_data(
    question,
    *,
    is_active,
):
    """
    Canonical Question -> Django Question fields.

    is_active is intentionally NOT controlled by
    canonical JSON:
    - CREATE => True
    - UPDATE => preserve current database value
    """

    return {
        "source_id": question[
            "source_id"
        ],
        "question_type": question[
            "question_type"
        ],
        "item_format": question[
            "item_format"
        ],
        "question_text": question[
            "question_text"
        ],
        "cefr_level": question[
            "cefr_level"
        ],
        "is_extension": question[
            "is_extension"
        ],
        "difficulty_score": Decimal(
            str(
                question[
                    "difficulty_score"
                ]
            )
        ),
        "correct_answer": question.get(
            "correct_answer"
        ),
        "accepted_answers": question.get(
            "accepted_answers"
        ),
        "explanation": question.get(
            "explanation"
        ),
        "audio_url": question.get(
            "audio_url"
        ),
        "is_active": is_active,
    }


def _apply_question_data(
    instance,
    data,
):
    for field_name, value in (
        data.items()
    ):
        setattr(
            instance,
            field_name,
            value,
        )


def run_phase1(
    paths,
):
    """
    Phase 1 Question Bank import validation.

    NO database writes.

    Performs:
    - canonical file parsing
    - schema/version validation
    - duplicate source_id detection across all files
    - DB Skill resolution
    - CREATE / UPDATE classification
    - Question.full_clean()
    - shared skill validation
    - shared option validation
    - collection of predictable per-question errors
    """

    documents = []
    file_infos = []

    # -------------------------------------------------
    # 1. File-level parsing.
    # Fatal errors stop immediately.
    # -------------------------------------------------

    for path in paths:
        document, file_info = (
            _read_canonical_file(
                path
            )
        )

        documents.append(
            (
                file_info.path,
                document,
            )
        )

        file_infos.append(
            file_info
        )

    # -------------------------------------------------
    # 2. Detect duplicate source_id across ALL files.
    # -------------------------------------------------

    source_locations = {}
    duplicate_errors = []
    raw_questions = []

    for file_path, document in documents:
        for index, question in enumerate(
            document["questions"],
            start=1,
        ):
            raw_questions.append(
                (
                    file_path,
                    index,
                    question,
                )
            )

            if not isinstance(
                question,
                dict,
            ):
                # Detailed error is collected later.
                continue

            source_id = question.get(
                "source_id"
            )

            if not isinstance(
                source_id,
                str,
            ):
                continue

            location = (
                f"{file_path}:"
                f"questions[{index}]"
            )

            if source_id in source_locations:
                duplicate_errors.append(
                    (
                        f"{location}: duplicate "
                        f"source_id {source_id!r}; "
                        "first seen at "
                        f"{source_locations[source_id]}."
                    )
                )
            else:
                source_locations[
                    source_id
                ] = location

    # -------------------------------------------------
    # 3. Prefetch current DB state.
    # Read-only.
    # -------------------------------------------------

    candidate_source_ids = {
        question.get("source_id")
        for _, _, question in raw_questions
        if (
            isinstance(question, dict)
            and isinstance(
                question.get(
                    "source_id"
                ),
                str,
            )
        )
    }

    existing_questions = {
        question.source_id: question
        for question in (
            Question.objects.filter(
                source_id__in=(
                    candidate_source_ids
                )
            )
        )
    }

    existing_option_keys = {}

    existing_question_ids = [
        question.pk
        for question in existing_questions.values()
    ]

    for question_id, option_key in (
            QuestionOption.objects.filter(
                question_id__in=existing_question_ids
            ).values_list(
                "question_id",
                "option_key",
            )
    ):
        existing_option_keys.setdefault(
            question_id,
            set(),
        ).add(
            option_key
        )

    skills_by_code = {
        skill.skill_code: skill
        for skill in Skill.objects.all()
    }

    # -------------------------------------------------
    # 4. Validate every predictable question-level
    #    error and collect them all.
    # -------------------------------------------------

    prepared = []
    errors = list(
        duplicate_errors
    )

    duplicate_source_ids = {
        message.split(
            "duplicate source_id ",
            1,
        )[1].split(
            ";",
            1,
        )[0].strip("'\"")
        for message in duplicate_errors
        if "duplicate source_id " in message
    }

    for (
        file_path,
        index,
        raw_question,
    ) in raw_questions:

        location = (
            f"{file_path}:"
            f"questions[{index}]"
        )

        try:
            _validate_one_canonical_question(
                raw_question
            )

            source_id = raw_question[
                "source_id"
            ]

            # Do not prepare duplicate rows.
            if source_id in duplicate_source_ids:
                continue

            # -----------------------------------------
            # Resolve all Skill codes against DB.
            # -----------------------------------------

            resolved_skill_rows = []

            for skill_row in raw_question[
                "skills"
            ]:
                skill_code = skill_row[
                    "skill_code"
                ]

                skill = skills_by_code.get(
                    skill_code
                )

                if skill is None:
                    raise ImportValidationError(
                        f"skill_code "
                        f"{skill_code!r} "
                        "does not exist in DB."
                    )

                resolved_skill_rows.append(
                    {
                        # Include both forms so the
                        # prepared state is useful to
                        # shared validation and Phase 2.
                        "skill": skill,
                        "skill_id": skill.pk,
                        "skill_code": (
                            skill.skill_code
                        ),
                        "is_primary": (
                            skill_row[
                                "is_primary"
                            ]
                        ),
                        "weight": Decimal(
                            str(
                                skill_row[
                                    "weight"
                                ]
                            )
                        ),
                    }
                )

            try:
                validate_skill_rows(
                    resolved_skill_rows
                )
            except ValidationError as exc:
                raise ImportValidationError(
                    f"invalid skills: {exc}"
                ) from exc

            options = []

            for display_order, option in enumerate(
                raw_question["options"],
                start=1,
            ):
                options.append(
                    {
                        "option_key": option[
                            "option_key"
                        ],
                        "option_text": option[
                            "option_text"
                        ],
                        "misconception_code": (
                            option[
                                "misconception_code"
                            ]
                        ),
                        "is_correct": option[
                            "is_correct"
                        ],
                        "display_order": (
                            display_order
                        ),
                    }
                )

            try:
                validate_options(
                    raw_question[
                        "question_type"
                    ],
                    options,
                )
            except ValidationError as exc:
                raise ImportValidationError(
                    f"invalid options: {exc}"
                ) from exc

            # -----------------------------------------
            # Question.full_clean()
            # -----------------------------------------

            existing = (
                existing_questions.get(
                    source_id
                )
            )

            if existing is not None:
                canonical_option_keys = {
                    option["option_key"]
                    for option in options
                }

                db_option_keys = (
                    existing_option_keys.get(
                        existing.pk,
                        set(),
                    )
                )

                omitted_option_keys = (
                        db_option_keys
                        - canonical_option_keys
                )

                if omitted_option_keys:
                    raise ImportValidationError(
                        "canonical input omits existing "
                        "option_key(s): "
                        + ", ".join(
                            sorted(
                                omitted_option_keys
                            )
                        )
                    )

            if existing is None:
                action = "CREATE"
                existing_question_id = None
                is_active = True
                question_instance = (
                    Question()
                )

            else:
                action = "UPDATE"
                existing_question_id = (
                    existing.pk
                )

                # Preserve operational state.
                is_active = (
                    existing.is_active
                )

                # Important:
                # using the existing instance lets
                # Django unique validation exclude
                # this row's own source_id.
                question_instance = existing

            question_data = (
                _question_model_data(
                    raw_question,
                    is_active=is_active,
                )
            )

            _apply_question_data(
                question_instance,
                question_data,
            )

            try:
                question_instance.full_clean()
            except ValidationError as exc:
                raise ImportValidationError(
                    f"Question.full_clean() "
                    f"failed: {exc}"
                ) from exc

            prepared.append(
                PreparedQuestion(
                    source_id=source_id,
                    action=action,
                    existing_question_id=(
                        existing_question_id
                    ),
                    question_data=(
                        question_data
                    ),
                    skill_rows=(
                        resolved_skill_rows
                    ),
                    options=options,
                )
            )

        except (
            ConversionError,
            ImportValidationError,
            ValidationError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            errors.append(
                f"{location}: {exc}"
            )

    # Stable reporting order.
    prepared.sort(
        key=lambda item: item.source_id
    )

    return Phase1Result(
        files=file_infos,
        prepared_questions=prepared,
        errors=errors,
    )

def _force_question_constraints():
    """
    Force deferred QuestionSkill integrity checks
    for the current question work performed so far.

    If valid, restore deferred mode for the next
    question in the same outer transaction.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            FORCE_QS_CHECKS
        )
        cursor.execute(
            RESTORE_QS_CHECKS
        )


def _save_question(
    prepared,
):
    """
    Persist one Question row.

    UPDATE locks the existing row and preserves
    its current operational is_active value.
    """

    data = dict(
        prepared.question_data
    )

    if prepared.action == "CREATE":
        question = Question(
            **data
        )
        question.save()

        return question

    if prepared.action != "UPDATE":
        raise ImportPersistenceError(
            f"[{prepared.source_id}] "
            f"Unknown action "
            f"{prepared.action!r}."
        )

    try:
        question = (
            Question.objects
            .select_for_update()
            .get(
                pk=(
                    prepared.existing_question_id
                )
            )
        )
    except Question.DoesNotExist as exc:
        raise ImportPersistenceError(
            f"[{prepared.source_id}] "
            "Question disappeared between "
            "Phase 1 and Phase 2."
        ) from exc

    # Operational state is outside canonical data.
    # Preserve the value existing at write time.
    data["is_active"] = (
        question.is_active
    )

    _apply_question_data(
        question,
        data,
    )

    question.save()

    return question


def _reconcile_question_skills(
    question,
    prepared_skill_rows,
):
    """
    Reconcile QuestionSkill by semantic identity:
        (question, skill)

    Canonical skills represent the full desired
    QuestionSkill state for this Question.
    """

    existing_rows = {
        row.skill_id: row
        for row in (
            QuestionSkill.objects
            .select_for_update()
            .filter(
                question=question
            )
        )
    }

    desired_skill_ids = {
        row["skill_id"]
        for row in prepared_skill_rows
    }

    for prepared_row in (
        prepared_skill_rows
    ):
        skill_id = prepared_row[
            "skill_id"
        ]

        row = existing_rows.get(
            skill_id
        )

        if row is None:
            QuestionSkill.objects.create(
                question=question,
                skill_id=skill_id,
                is_primary=(
                    prepared_row[
                        "is_primary"
                    ]
                ),
                weight=(
                    prepared_row[
                        "weight"
                    ]
                ),
            )

            continue

        row.is_primary = prepared_row[
            "is_primary"
        ]
        row.weight = prepared_row[
            "weight"
        ]

        row.save(
            update_fields=[
                "is_primary",
                "weight",
            ]
        )

    stale_skill_ids = (
        set(existing_rows)
        - desired_skill_ids
    )

    if stale_skill_ids:
        QuestionSkill.objects.filter(
            question=question,
            skill_id__in=stale_skill_ids,
        ).delete()


def _temporary_option_text(
    *,
    question,
    option,
    forbidden_texts,
):
    """
    Produce a short unique temporary option_text.

    This allows safe swaps such as:
        A: x -> y
        B: y -> x

    without transiently violating the existing
    unique (question, option_text) constraint.
    """

    base = (
        f"__PRIVET_IMPORT_TMP_"
        f"{question.pk}_"
        f"{option.pk}__"
    )

    candidate = base
    counter = 1

    while candidate in forbidden_texts:
        candidate = (
            f"{base}_{counter}"
        )
        counter += 1

    forbidden_texts.add(
        candidate
    )

    return candidate


def _reconcile_question_options(
    question,
    prepared_options,
):
    """
    Reconcile QuestionOption by:
        (question, option_key)

    Existing option rows retain their PKs.

    Existing DB option_keys omitted by canonical input
    are rejected rather than silently deleted.
    """

    existing_rows = {
        row.option_key: row
        for row in (
            QuestionOption.objects
            .select_for_update()
            .filter(
                question=question
            )
        )
    }

    desired_by_key = {
        option["option_key"]: option
        for option in prepared_options
    }

    omitted_keys = (
        set(existing_rows)
        - set(desired_by_key)
    )

    if omitted_keys:
        raise ImportPersistenceError(
            f"[{question.source_id}] "
            "canonical input omits existing "
            "option_key(s): "
            + ", ".join(
                sorted(
                    omitted_keys
                )
            )
        )

    # -------------------------------------------------
    # Temporary text pass.
    #
    # The DB still has unique(question, option_text).
    # Without this pass, swapping A/B option text could
    # fail depending on update order.
    # -------------------------------------------------

    forbidden_texts = {
        option["option_text"]
        for option in prepared_options
    }

    forbidden_texts.update(
        row.option_text
        for row in existing_rows.values()
    )

    for option_key, row in (
        existing_rows.items()
    ):
        if option_key not in desired_by_key:
            continue

        temporary_text = (
            _temporary_option_text(
                question=question,
                option=row,
                forbidden_texts=(
                    forbidden_texts
                ),
            )
        )

        QuestionOption.objects.filter(
            pk=row.pk
        ).update(
            option_text=temporary_text
        )

    # -------------------------------------------------
    # Final canonical state.
    # -------------------------------------------------

    for prepared_option in (
        prepared_options
    ):
        option_key = prepared_option[
            "option_key"
        ]

        row = existing_rows.get(
            option_key
        )

        if row is None:
            QuestionOption.objects.create(
                question=question,
                option_key=option_key,
                option_text=(
                    prepared_option[
                        "option_text"
                    ]
                ),
                misconception_code=(
                    prepared_option[
                        "misconception_code"
                    ]
                ),
                is_correct=(
                    prepared_option[
                        "is_correct"
                    ]
                ),
                display_order=(
                    prepared_option[
                        "display_order"
                    ]
                ),
            )

            continue

        # Refresh in-memory text after temporary
        # QuerySet.update().
        row.option_text = (
            prepared_option[
                "option_text"
            ]
        )
        row.misconception_code = (
            prepared_option[
                "misconception_code"
            ]
        )
        row.is_correct = (
            prepared_option[
                "is_correct"
            ]
        )
        row.display_order = (
            prepared_option[
                "display_order"
            ]
        )

        row.save(
            update_fields=[
                "option_text",
                "misconception_code",
                "is_correct",
                "display_order",
            ]
        )


def run_phase2(
    phase1_result,
    *,
    dry_run,
):
    """
    Persist an already-valid Phase 1 result.

    One outer transaction covers the ENTIRE batch.

    dry_run=True:
        execute real writes and real DB constraints,
        then roll the entire transaction back.

    dry_run=False:
        commit only if every Question succeeds.
    """

    if not phase1_result.is_valid:
        raise ImportPersistenceError(
            "Phase 2 cannot run because "
            "Phase 1 contains validation errors."
        )

    current_source_id = None

    try:
        with transaction.atomic():

            for prepared in (
                phase1_result.prepared_questions
            ):
                current_source_id = (
                    prepared.source_id
                )

                question = _save_question(
                    prepared
                )

                _reconcile_question_skills(
                    question,
                    prepared.skill_rows,
                )

                _reconcile_question_options(
                    question,
                    prepared.options,
                )

                # Force PostgreSQL deferred
                # QuestionSkill constraints NOW,
                # while still inside the single
                # batch transaction.
                _force_question_constraints()

            if dry_run:
                transaction.set_rollback(
                    True
                )

    except IntegrityError as exc:
        source_label = (
            current_source_id
            or "<unknown>"
        )

        raise ImportPersistenceError(
            f"[{source_label}] "
            f"database integrity error: {exc}"
        ) from exc

    except ImportPersistenceError:
        raise

    except Exception as exc:
        source_label = (
            current_source_id
            or "<unknown>"
        )

        raise ImportPersistenceError(
            f"[{source_label}] "
            f"unexpected persistence error: "
            f"{exc}"
        ) from exc

    return Phase2Result(
        question_count=(
            phase1_result.question_count
        ),
        create_count=(
            phase1_result.create_count
        ),
        update_count=(
            phase1_result.update_count
        ),
        dry_run=dry_run,
    )