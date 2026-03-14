import json
import tempfile
import unittest
from pathlib import Path

from app.main import parse_prompt_csv, run_job, save_outputs


class AppTests(unittest.TestCase):
    def test_parse_prompt_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "prompts.csv"
            csv_path.write_text(
                "prompt_id,prompt_name,user_prompt_template,enabled,temperature,max_tokens\n"
                "P001,要約,要約してください: {{document_text}},true,0.2,300\n"
                "P002,判定,判定してください: {{document_text}},true,0.1,200\n",
                encoding="utf-8",
            )
            prompts = parse_prompt_csv(csv_path)
            self.assertEqual(len(prompts), 2)
            self.assertEqual(prompts[0].prompt_id, "P001")

    def test_run_job_and_save_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_file = tmp_path / "input.txt"
            prompt_csv = tmp_path / "prompts.csv"
            output_dir = tmp_path / "outputs"

            input_file.write_text("これは社内向けのテスト文書です。", encoding="utf-8")
            prompt_csv.write_text(
                "prompt_id,prompt_name,user_prompt_template,enabled\n"
                "P001,要約,文書を要約してください: {{document_text}},true\n"
                "P002,論点抽出,文書の論点を抽出してください: {{document_text}},true\n",
                encoding="utf-8",
            )

            job = run_job(input_file, prompt_csv)
            self.assertEqual(len(job.results), 2)
            self.assertTrue(all(r.status == "completed" for r in job.results))

            json_path, csv_path = save_outputs(job, output_dir)
            self.assertTrue(json_path.exists())
            self.assertTrue(csv_path.exists())

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["job_id"], job.job_id)
            self.assertEqual(len(payload["results"]), 2)

    def test_missing_required_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "prompts.csv"
            csv_path.write_text("prompt_id,prompt_name,enabled\nP001,要約,true\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_prompt_csv(csv_path)


if __name__ == "__main__":
    unittest.main()
