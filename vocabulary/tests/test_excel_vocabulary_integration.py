
import json

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model

from django.core.management import call_command
from django.core.management.base import CommandError

from django.test import TestCase
from django.urls import reverse

from vocabulary.models import (
    VocabularyItem,
    VocabularyItemTopic,
    VocabularySRSState,
    VocabularyTopic,
)

from vocabulary.services import start_learning


class ExcelVocabularyImportTests(TestCase):

    def setUp(self):

        self.folder = TemporaryDirectory()

        self.addCleanup(
            self.folder.cleanup
        )

        self.path = (
            Path(self.folder.name)
            / "vocab_v1.json"
        )

        self.payload = {
            "version": 1,

            "topics": [
                {
                    "code": "level-a1",
                    "title_vi": "Danh sách A1",
                    "cefr_level": "A1",
                },

                {
                    "code": "level-a2",
                    "title_vi": "Danh sách A2",
                    "cefr_level": "A2",
                },
            ],

            "items": [
                {
                    "source_id": "ru-test-lemma-001",
                    "lemma": "мама",
                    "meaning_en": "mum",
                    "meaning_vi": "",
                    "cefr_level": "A1",

                    "topic_codes": [
                        "level-a1",
                        "level-a2",
                    ],

                    "topic_orders": {
                        "level-a1": 2,
                        "level-a2": 11,
                    },

                    "source_entries": [
                        {
                            "level": "A1",
                            "row_number": 2,
                            "initial": "М",
                            "russian_entry": "мама",
                            "english_meaning": "mum",
                            "pdf_page": 11,
                            "book_page": 10,
                            "source_file": "a1.pdf",
                        },

                        {
                            "level": "A2",
                            "row_number": 11,
                            "initial": "М",
                            "russian_entry": "мама",
                            "english_meaning": "mother",
                            "pdf_page": 12,
                            "book_page": 11,
                            "source_file": "a2.pdf",
                        },
                    ],
                }
            ],
        }

        self.user = get_user_model().objects.create_user(
            email="excelvoc@example.com",
            password="password123",
        )

        self.client.force_login(
            self.user
        )

    def write(self):

        self.path.write_text(
            json.dumps(
                self.payload,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def run_command(self, **kwargs):

        return call_command(
            "import_vocabulary",
            self.path,
            stdout=StringIO(),
            **kwargs,
        )

    # --------------------------------------------------------
    # VALIDATION + ROLLBACK
    # --------------------------------------------------------

    def test_validate_only_and_dry_run_make_no_writes(self):

        self.write()

        self.run_command(
            validate_only=True
        )

        self.run_command(
            dry_run=True
        )

        self.assertEqual(
            VocabularyItem.objects.count(),
            0,
        )

        self.assertEqual(
            VocabularyTopic.objects.count(),
            0,
        )

    # --------------------------------------------------------
    # A1/A2 MEMBERSHIP
    # --------------------------------------------------------

    def test_commit_preserves_two_level_positions_and_glosses(self):

        self.write()

        self.run_command(
            commit=True
        )

        self.assertEqual(
            VocabularyItem.objects.count(),
            1,
        )

        self.assertEqual(
            VocabularyItemTopic.objects.count(),
            2,
        )

        item = VocabularyItem.objects.get()

        self.assertEqual(
            item.meaning_en,
            "mum",
        )

        self.assertEqual(
            item.meaning_vi,
            "",
        )

        self.assertEqual(
            len(item.source_entries),
            2,
        )

        ranks = dict(
            item.topic_links.values_list(
                "topic__code",
                "display_order",
            )
        )

        self.assertEqual(
            ranks,
            {
                "level-a1": 2,
                "level-a2": 11,
            },
        )

        topic = VocabularyTopic.objects.get(
            code="level-a1"
        )

        self.assertIsNone(
            topic.assessment_skill_id
        )

    # --------------------------------------------------------
    # IDEMPOTENT IMPORT
    # --------------------------------------------------------

    def test_repeat_commit_is_idempotent(self):

        self.write()

        self.run_command(commit=True)

        before = VocabularyItem.objects.get().updated_at

        self.run_command(commit=True)

        self.assertEqual(
            VocabularyItem.objects.count(),
            1,
        )

        self.assertEqual(
            VocabularyItemTopic.objects.count(),
            2,
        )

        self.assertEqual(
            VocabularyItem.objects.get().updated_at,
            before,
        )

    # --------------------------------------------------------
    # API FILTERS A2 BY TOPIC MEMBERSHIP
    # --------------------------------------------------------

    def test_a2_includes_shared_a1_item_with_a2_gloss(self):

        self.write()

        self.run_command(
            commit=True
        )

        response = self.client.get(
            reverse("vocab:items"),
            {
                "level": "A2",
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.json()["total"],
            1,
        )

        row = response.json()["items"][0]

        self.assertEqual(
            row["cefr_level"],
            "A1",
        )

        self.assertEqual(
            row["meaning_en"],
            "mother",
        )

        self.assertEqual(
            row["display_language"],
            "en",
        )

        self.assertEqual(
            row["available_levels"],
            ["A1", "A2"],
        )

    # --------------------------------------------------------
    # PROTECT STUDIED CONTENT
    # --------------------------------------------------------

    def test_cannot_change_meaning_en_after_srs_start(self):

        self.write()

        self.run_command(
            commit=True
        )

        start_learning(
            user=self.user,
            item=VocabularyItem.objects.get(),
        )

        self.payload["items"][0]["meaning_en"] = (
            "mommy"
        )

        self.write()

        with self.assertRaises(CommandError):

            self.run_command(
                commit=True
            )

        self.assertEqual(
            VocabularyItem.objects.get().meaning_en,
            "mum",
        )

        self.assertEqual(
            VocabularySRSState.objects.count(),
            1,
        )

    # --------------------------------------------------------
    # INVALID SOURCE TOPIC MAPPING
    # --------------------------------------------------------

    def test_missing_topic_mapping_rejected_before_write(self):

        self.payload["items"][0]["topic_orders"] = {
            "level-a1": 2,
        }

        self.write()

        with self.assertRaises(CommandError):

            self.run_command(
                validate_only=True
            )

        self.assertEqual(
            VocabularyItem.objects.count(),
            0,
        )
