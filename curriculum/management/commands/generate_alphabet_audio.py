"""Generate static MP3 assets once, outside the learner request flow.

Install: python -m pip install edge-tts
Dry-run: python manage.py generate_alphabet_audio --dry-run
One letter: python manage.py generate_alphabet_audio --only-letter m
All: python manage.py generate_alphabet_audio
"""
import asyncio
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from curriculum.alphabet_catalog import iter_audio_jobs, load_catalog


class Command(BaseCommand):
    help = "Generate AI TTS audio for Alphabet into curriculum/static (no DB changes)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--overwrite", action="store_true")
        parser.add_argument("--only-letter", default=None)
        parser.add_argument("--voice-ru", default="ru-RU-SvetlanaNeural")
        parser.add_argument("--voice-vi", default="vi-VN-HoaiMyNeural")

    def handle(self, *args, **options):
        catalog = load_catalog()
        selected_id = options["only_letter"]
        valid_ids = {letter["id"] for letter in catalog["letters"]}
        if selected_id is not None and selected_id not in valid_ids:
            raise CommandError(f"Unknown letter id: {selected_id}")

        jobs = list(iter_audio_jobs(catalog, only_letter=selected_id))
        app_dir = Path(__file__).resolve().parents[2]
        static_dir = app_dir / "static"

        if options["dry_run"]:
            self.stdout.write(f"{len(jobs)} planned MP3 files:")
            for relative_path, text, language in jobs:
                self.stdout.write(f"[{language}] {relative_path} <= {text}")
            return

        try:
            import edge_tts
        except ImportError as exc:
            raise CommandError(
                "edge-tts is required for audio generation. "
                "Install with: python -m pip install edge-tts"
            ) from exc

        async def run():
            created = 0
            skipped = 0
            for relative_path, text, language in jobs:
                output = static_dir / relative_path
                if output.is_file() and output.stat().st_size > 0 and not options["overwrite"]:
                    skipped += 1
                    continue

                output.parent.mkdir(parents=True, exist_ok=True)
                temp = output.with_name(output.stem + ".part.mp3")
                voice = options["voice_vi"] if language == "vi" else options["voice_ru"]
                try:
                    speaker = edge_tts.Communicate(text=text, voice=voice)
                    await speaker.save(str(temp))
                    if not temp.is_file() or temp.stat().st_size == 0:
                        raise RuntimeError("TTS returned an empty file.")
                    temp.replace(output)
                except Exception as exc:
                    temp.unlink(missing_ok=True)
                    raise CommandError(
                        f"TTS failed at {relative_path}: {exc}"
                    ) from exc

                created += 1
                self.stdout.write(f"Created {relative_path}")

            return created, skipped

        created, skipped = asyncio.run(run())
        self.stdout.write(self.style.SUCCESS(
            f"Done. Created: {created}. Skipped existing: {skipped}. "
            "Listen and review the audio before publishing."
        ))
