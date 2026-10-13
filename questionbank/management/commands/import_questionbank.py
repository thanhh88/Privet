from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from questionbank.importing import (
    ImportPersistenceError,
    ImportValidationError,
    run_phase1,
    run_phase2,
)


class Command(BaseCommand):
    help = (
        "Validate and import canonical Privet Question Bank JSON. "
        "Normal commit is not enabled yet."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "canonical_files",
            nargs="+",
            help=(
                "One or more canonical Privet "
                "Question Bank JSON files."
            ),
        )

        mode_group = (
            parser.add_mutually_exclusive_group()
        )

        mode_group.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Run Phase 1 and Phase 2, then commit "
                "the entire batch if all checks pass."
            ),
        )

        mode_group.add_argument(
            "--validate-only",
            action="store_true",
            help=(
                "Run Phase 1 validation only. "
                "No database writes are performed."
            ),
        )

        mode_group.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Run Phase 1 and real Phase 2 database "
                "writes/constraints, then roll back the "
                "entire batch."
            ),
        )

    def handle(self, *args, **options):
        validate_only = options[
            "validate_only"
        ]
        dry_run = options[
            "dry_run"
        ]
        commit = options[
            "commit"
        ]

        if (
                not validate_only
                and not dry_run
                and not commit
        ):
            raise CommandError(
                "Choose one mode: "
                "--validate-only, --dry-run, "
                "or --commit."
            )

        paths = options[
            "canonical_files"
        ]

        self.stdout.write(
            "Privet Question Bank Importer"
        )

        if validate_only:
            self.stdout.write(
                "Mode: VALIDATE ONLY"
            )
            self.stdout.write(
                "Database writes: DISABLED"
            )

        elif dry_run:
            self.stdout.write(
                "Mode: DRY RUN"
            )
            self.stdout.write(
                "Database writes: ENABLED TEMPORARILY"
            )
            self.stdout.write(
                "Final transaction outcome: ROLLBACK"
            )

        else:
            self.stdout.write(
                "Mode: COMMIT"
            )
            self.stdout.write(
                "Database writes: ENABLED"
            )
            self.stdout.write(
                "Final transaction outcome: COMMIT "
                "only if the entire batch succeeds"
            )

        self.stdout.write("")

        # -----------------------------------------
        # Phase 1
        # -----------------------------------------

        try:
            phase1 = run_phase1(
                paths
            )

        except ImportValidationError as exc:
            raise CommandError(
                str(exc)
            ) from exc

        # -----------------------------------------
        # File report
        # -----------------------------------------

        for file_info in phase1.files:
            self.stdout.write(
                f"FILE {file_info.path}"
            )
            self.stdout.write(
                "  questions: "
                f"{file_info.question_count}"
            )
            self.stdout.write(
                "  sha256: "
                f"{file_info.sha256}"
            )

        self.stdout.write("")

        # -----------------------------------------
        # Phase 1 report
        # -----------------------------------------

        self.stdout.write(
            "Phase 1 summary:"
        )
        self.stdout.write(
            f"  prepared: "
            f"{phase1.question_count}"
        )
        self.stdout.write(
            f"  CREATE: "
            f"{phase1.create_count}"
        )
        self.stdout.write(
            f"  UPDATE: "
            f"{phase1.update_count}"
        )
        self.stdout.write(
            f"  errors: "
            f"{len(phase1.errors)}"
        )

        if phase1.errors:
            self.stdout.write("")

            self.stderr.write(
                self.style.ERROR(
                    "Phase 1 validation errors:"
                )
            )

            for number, error in enumerate(
                phase1.errors,
                start=1,
            ):
                self.stderr.write(
                    f"{number}. {error}"
                )

            raise CommandError(
                "Question Bank validation failed "
                f"with {len(phase1.errors)} "
                "error(s). "
                "Database was not modified."
            )

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                "Phase 1 validation passed."
            )
        )

        # -----------------------------------------
        # VALIDATE ONLY stops here.
        # -----------------------------------------

        if validate_only:
            self.stdout.write(
                self.style.SUCCESS(
                    "Database was not modified."
                )
            )
            return

        # -----------------------------------------
        # Phase 2 DRY RUN
        # -----------------------------------------

        self.stdout.write("")

        if dry_run:
            self.stdout.write(
                "Starting Phase 2 dry run..."
            )
        else:
            self.stdout.write(
                "Starting Phase 2 commit..."
            )

        try:
            phase2 = run_phase2(
                phase1,
                dry_run=dry_run,
            )

        except ImportPersistenceError as exc:
            raise CommandError(
                "Phase 2 failed. "
                "The entire batch was rolled back. "
                f"{exc}"
            ) from exc

        self.stdout.write("")
        self.stdout.write(
            "Phase 2 summary:"
        )
        self.stdout.write(
            f"  processed: "
            f"{phase2.question_count}"
        )
        self.stdout.write(
            f"  CREATE: "
            f"{phase2.create_count}"
        )
        self.stdout.write(
            f"  UPDATE: "
            f"{phase2.update_count}"
        )

        self.stdout.write("")

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    "Phase 2 dry run passed."
                )
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "All database changes were rolled back."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Phase 2 commit passed."
                )
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "The entire Question Bank batch "
                    "was committed successfully."
                )
            )