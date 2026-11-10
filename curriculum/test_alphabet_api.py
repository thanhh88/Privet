import json
from types import SimpleNamespace

from django.test import RequestFactory, SimpleTestCase

from curriculum import alphabet_views
from curriculum.alphabet_catalog import iter_audio_jobs, load_catalog


class AlphabetCatalogTests(SimpleTestCase):
    def test_every_letter_appears_in_exactly_one_lesson(self):
        catalog = load_catalog()
        ids = [letter["id"] for letter in catalog["letters"]]
        covered = [
            key
            for lesson in catalog["lessons"]
            for key in lesson["letter_ids"]
        ]
        self.assertEqual(len(ids), 33)
        self.assertEqual(len(set(ids)), 33)
        self.assertEqual(len(catalog["lessons"]), 7)
        self.assertCountEqual(ids, covered)

    def test_table_uses_cyrillic_order(self):
        catalog = load_catalog()
        letters = "".join(letter["upper"] for letter in catalog["letters"])
        self.assertEqual(letters, "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ")

    def test_every_letter_has_two_or_more_audio_types(self):
        catalog = load_catalog()
        jobs = list(iter_audio_jobs(catalog))
        self.assertGreater(len(jobs), 33 * 2)
        self.assertEqual(len({job[0] for job in jobs}), len(jobs))
        self.assertTrue(all(job[1] for job in jobs))


class AlphabetApiTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def request(self, url="/"):
        request = self.factory.get(url)
        request.user = SimpleNamespace(is_authenticated=True)
        return request

    def payload(self, response):
        return json.loads(response.content.decode("utf-8"))

    def test_table_returns_33_letters(self):
        response = alphabet_views.table(self.request())
        self.assertEqual(response.status_code, 200)
        result = self.payload(response)
        self.assertEqual(result["mode"], "table")
        self.assertEqual(len(result["letters"]), 33)
        self.assertEqual(result["letters"][0]["upper"], "А")
        self.assertIn("name_url", result["letters"][0]["audio"])

    def test_seven_lesson_summaries(self):
        response = alphabet_views.overview(self.request())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.payload(response)["lessons"]), 7)

    def test_lesson_one_returns_five_letters_in_learning_order(self):
        response = alphabet_views.lesson(self.request(), "lesson-1")
        self.assertEqual(response.status_code, 200)
        result = self.payload(response)
        self.assertEqual(
            [letter["upper"] for letter in result["letters"]],
            ["А", "О", "М", "Т", "К"],
        )

    def test_unknown_lesson_and_letter_are_404(self):
        self.assertEqual(
            alphabet_views.lesson(self.request(), "missing").status_code, 404
        )
        self.assertEqual(
            alphabet_views.letter(self.request(), "missing").status_code, 404
        )

    def test_post_is_not_allowed(self):
        request = self.factory.post("/")
        request.user = SimpleNamespace(is_authenticated=True)
        self.assertEqual(alphabet_views.table(request).status_code, 405)
