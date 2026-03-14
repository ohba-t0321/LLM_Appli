from __future__ import annotations

import argparse
import csv
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_PROMPT_COLUMNS = {"prompt_id", "prompt_name", "user_prompt_template", "enabled"}


@dataclass
class PromptConfig:
    prompt_id: str
    prompt_name: str
    system_prompt: str
    user_prompt_template: str
    output_schema: str
    temperature: float
    max_tokens: int
    enabled: bool


@dataclass
class PromptResult:
    prompt_id: str
    prompt_name: str
    status: str
    response: str | None
    error_message: str | None


@dataclass
class JobResult:
    job_id: str
    model: str
    created_at: str
    input_file: str
    prompt_csv: str
    results: list[PromptResult]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def read_text_file(path: Path) -> str:
    data = path.read_bytes()
    if not data:
        raise ValueError("input file is empty")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="ignore")


def parse_prompt_csv(path: Path) -> list[PromptConfig]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV header is missing")

        missing = REQUIRED_PROMPT_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV missing required columns: {sorted(missing)}")

        prompts: list[PromptConfig] = []
        ids: set[str] = set()
        for row in reader:
            prompt_id = (row.get("prompt_id") or "").strip()
            if not prompt_id:
                raise ValueError("prompt_id is required")
            if prompt_id in ids:
                raise ValueError(f"duplicate prompt_id: {prompt_id}")
            ids.add(prompt_id)

            enabled = to_bool(row.get("enabled", ""))
            if not enabled:
                continue

            try:
                temperature = float((row.get("temperature") or "0.2").strip())
                max_tokens = int((row.get("max_tokens") or "800").strip())
            except ValueError as exc:
                raise ValueError(f"invalid numeric value in prompt_id={prompt_id}") from exc

            prompts.append(
                PromptConfig(
                    prompt_id=prompt_id,
                    prompt_name=(row.get("prompt_name") or "").strip() or prompt_id,
                    system_prompt=(row.get("system_prompt") or "").strip(),
                    user_prompt_template=(row.get("user_prompt_template") or "").strip(),
                    output_schema=(row.get("output_schema") or "").strip(),
                    temperature=temperature,
                    max_tokens=max_tokens,
                    enabled=enabled,
                )
            )

    if not prompts:
        raise ValueError("No enabled prompts found in CSV")
    return prompts


def render_prompt(template: str, document_text: str) -> str:
    return template.replace("{{document_text}}", document_text)


def mock_llm(prompt_text: str, max_tokens: int) -> str:
    body = (
        "[MOCK RESPONSE]\n"
        f"Analyzed Prompt: {prompt_text[:240]}\n"
        "Result: This is a placeholder result. Replace mock_llm with a real LLM provider call."
    )
    return body[:max_tokens]


def run_job(input_file: Path, prompt_csv: Path, model: str = "mock-llm") -> JobResult:
    doc = read_text_file(input_file)
    prompts = parse_prompt_csv(prompt_csv)

    results: list[PromptResult] = []
    for prompt in prompts:
        try:
            prompt_text = render_prompt(prompt.user_prompt_template, doc)
            response = mock_llm(prompt_text, prompt.max_tokens)
            results.append(
                PromptResult(
                    prompt_id=prompt.prompt_id,
                    prompt_name=prompt.prompt_name,
                    status="completed",
                    response=response,
                    error_message=None,
                )
            )
        except Exception as exc:
            results.append(
                PromptResult(
                    prompt_id=prompt.prompt_id,
                    prompt_name=prompt.prompt_name,
                    status="failed",
                    response=None,
                    error_message=str(exc),
                )
            )

    return JobResult(
        job_id=str(uuid.uuid4()),
        model=model,
        created_at=now_iso(),
        input_file=str(input_file),
        prompt_csv=str(prompt_csv),
        results=results,
    )


def save_outputs(job: JobResult, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{job.job_id}.json"
    json_payload: dict[str, Any] = {
        "job_id": job.job_id,
        "model": job.model,
        "created_at": job.created_at,
        "input_file": job.input_file,
        "prompt_csv": job.prompt_csv,
        "results": [asdict(r) for r in job.results],
    }
    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = output_dir / f"{job.job_id}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["prompt_id", "prompt_name", "status", "response", "error_message"])
        writer.writeheader()
        for result in job.results:
            writer.writerow(asdict(result))

    return json_path, csv_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run CSV-driven multi-prompt analysis for one input file")
    parser.add_argument("--input-file", required=True, type=Path)
    parser.add_argument("--prompt-csv", required=True, type=Path)
    parser.add_argument("--output-dir", default=Path("outputs"), type=Path)
    parser.add_argument("--model", default="mock-llm")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    job = run_job(args.input_file, args.prompt_csv, args.model)
    json_path, csv_path = save_outputs(job, args.output_dir)
    print(f"job_id={job.job_id}")
    print(f"json={json_path}")
    print(f"csv={csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
