import hashlib
import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from questionbank.conversion import (
    ConversionError,
    convert_master_documents,
    validate_canonical_bank,
)


EXPECTED_TOTAL = 554


class Command(BaseCommand):
    help = (
        "Build deterministic canonical Privet Question Bank JSON "
        "from master authoring JSON files."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "master_files",
            nargs="+",
            help=(
                "One or more master Question Bank JSON files."
            ),
        )

        parser.add_argument(
            "--output",
            required=True,
            help=(
                "Path of the canonical JSON file to create."
            ),
        )

        parser.add_argument(
            "--expected-count",
            type=int,
            default=EXPECTED_TOTAL,
            help=(
                "Expected total number of questions. "
                f"Default: {EXPECTED_TOTAL}."
            ),
        )

    def handle(self, *args, **options):
        master_paths = [
            Path(value)
            for value in options["master_files"]
        ]

        output_path = Path(
            options["output"]
        )

        expected_count = options[
            "expected_count"
        ]

        documents = []

        for path in master_paths:
            if not path.is_file():
                raise CommandError(
                    f"Master file does not exist: {path}"
                )

            raw_bytes = path.read_bytes()

            sha256 = hashlib.sha256(
                raw_bytes
            ).hexdigest()

            try:
                text = raw_bytes.decode(
                    "utf-8-sig"
                )
            except UnicodeDecodeError as exc:
                raise CommandError(
                    f"{path}: file must be UTF-8."
                ) from exc

            try:
                document = json.loads(
                    text,
                    parse_float=Decimal,
                )
            except json.JSONDecodeError as exc:
                raise CommandError(
                    f"{path}: invalid JSON at "
                    f"line {exc.lineno}, "
                    f"column {exc.colno}: "
                    f"{exc.msg}"
                ) from exc

            if not isinstance(
                document,
                list,
            ):
                raise CommandError(
                    f"{path}: top-level JSON "
                    "must be an array."
                )

            self.stdout.write(
                f"MASTER {path}"
            )
            self.stdout.write(
                f"  questions: {len(document)}"
            )
            self.stdout.write(
                f"  sha256: {sha256}"
            )

            documents.append(
                (
                    str(path),
                    document,
                )
            )

        try:
            canonical = (
                convert_master_documents(
                    documents
                )
            )

            validate_canonical_bank(
                canonical
            )

        except ConversionError as exc:
            raise CommandError(
                str(exc)
            ) from exc

        question_count = len(
            canonical["questions"]
        )

        if question_count != expected_count:
            raise CommandError(
                "Unexpected total question count: "
                f"expected {expected_count}, "
                f"found {question_count}."
            )

        # Serialize explicitly to UTF-8 bytes with LF newlines.
        # This keeps output reproducible across Windows/Linux.
        payload = (
            json.dumps(
                canonical,
                ensure_ascii=False,
                indent=2,
                sort_keys=False,
            )
            + "\n"
        ).encode("utf-8")

        output_sha256 = hashlib.sha256(
            payload
        ).hexdigest()

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temp_path = output_path.with_name(
            output_path.name + ".tmp"
        )

        try:
            temp_path.write_bytes(
                payload
            )

            temp_path.replace(
                output_path
            )
        finally:
            if temp_path.exists():
                temp_path.unlink()

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Canonical Question Bank built successfully."
            )
        )
        self.stdout.write(
            f"questions: {question_count}"
        )
        self.stdout.write(
            f"output: {output_path}"
        )
        self.stdout.write(
            f"sha256: {output_sha256}"
        )