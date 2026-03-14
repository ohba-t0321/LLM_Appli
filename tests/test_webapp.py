import tempfile
import unittest
from pathlib import Path

from app import webapp


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        webapp.DATA_DIR = Path(self.tmp.name)
        webapp.DB_PATH = webapp.DATA_DIR / "app.db"
        webapp.init_db()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_kb_crud(self) -> None:
        item = webapp.add_knowledge("契約書ひな型", "第1条 目的")
        self.assertTrue(item["id"] > 0)

        items = webapp.list_knowledge()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "契約書ひな型")

        webapp.delete_knowledge(item["id"])
        self.assertEqual(webapp.list_knowledge(), [])

    def test_parse_prompt_csv_text(self) -> None:
        csv_text = (
            "prompt_id,prompt_name,user_prompt_template,enabled\n"
            "P001,要約,要約してください: {{document_text}},true\n"
        )
        prompts = webapp.parse_prompt_csv_text(csv_text)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0].prompt_id, "P001")

    def test_build_knowledge_context(self) -> None:
        webapp.add_knowledge("NDA", "秘密保持条項")
        ctx = webapp.build_knowledge_context()
        self.assertIn("[KB:NDA]", ctx)


if __name__ == "__main__":
    unittest.main()
