from __future__ import annotations

import csv
import io
import json
import socket
import sqlite3
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
LLM_TIMEOUT_SECONDS = 300

REQUIRED_PROMPT_COLUMNS = {"prompt_id", "prompt_name", "user_prompt_template", "enabled"}


@dataclass
class PromptConfig:
    prompt_id: str
    prompt_name: str
    user_prompt_template: str
    enabled: bool
    temperature: float = 1.0
    max_tokens: int = 800


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        con.commit()
    finally:
        con.close()


def list_knowledge() -> list[dict[str, Any]]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT id, title, content, created_at FROM knowledge_base ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def add_knowledge(title: str, content: str) -> dict[str, Any]:
    if not title.strip() or not content.strip():
        raise ValueError("title and content are required")
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.execute(
            "INSERT INTO knowledge_base(title, content, created_at) VALUES(?, ?, ?)",
            (title.strip(), content.strip(), now_iso()),
        )
        con.commit()
        row_id = cur.lastrowid
    finally:
        con.close()

    return {"id": row_id, "title": title.strip(), "content": content.strip(), "created_at": now_iso()}


def delete_knowledge(item_id: int) -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("DELETE FROM knowledge_base WHERE id = ?", (item_id,))
        con.commit()
    finally:
        con.close()


def to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def parse_prompt_csv_text(csv_text: str) -> list[PromptConfig]:
    reader = csv.DictReader(io.StringIO(csv_text))
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

        prompts.append(
            PromptConfig(
                prompt_id=prompt_id,
                prompt_name=(row.get("prompt_name") or "").strip() or prompt_id,
                user_prompt_template=(row.get("user_prompt_template") or "").strip(),
                enabled=enabled,
                temperature=float((row.get("temperature") or "1.0").strip()),
                max_tokens=int((row.get("max_tokens") or "800").strip()),
            )
        )
    if not prompts:
        raise ValueError("No enabled prompts found in CSV")
    return prompts


def render_prompt(template: str, document_text: str) -> str:
    return template.replace("{{document_text}}", document_text)


def build_knowledge_context(limit: int = 6) -> str:
    kb = list_knowledge()[:limit]
    if not kb:
        return ""
    chunks = []
    for item in kb:
        chunks.append(f"[KB:{item['title']}]\n{item['content']}")
    return "\n\n".join(chunks)


def extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text", "")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)

    return ""


def _build_llm_request(
    provider: str,
    model: str,
    api_key: str,
    messages: list[dict[str, str]],
    temperature: float = 1.0,
    *,
    stream: bool = False,
) -> urllib.request.Request:
    if not api_key.strip():
        raise ValueError("APIトークンが未設定です。画面右上の設定から入力してください。")

    if provider != "openai":
        raise ValueError("現在の実装では provider=openai のみ対応です。")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if stream:
        payload["stream"] = True

    return urllib.request.Request(
        OPENAI_CHAT_COMPLETIONS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )


def _open_llm_response(
    provider: str,
    model: str,
    api_key: str,
    messages: list[dict[str, str]],
    temperature: float = 1.0,
    *,
    stream: bool = False,
) -> Any:
    req = _build_llm_request(
        provider=provider,
        model=model,
        api_key=api_key,
        messages=messages,
        temperature=temperature,
        stream=stream,
    )
    try:
        return urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise ValueError(f"LLM API error: {e.code} {detail[:400]}") from e
    except (TimeoutError, socket.timeout) as e:
        raise ValueError("LLM API の応答がタイムアウトしました。プロンプトを短くするか、少し待ってから再試行してください。") from e
    except urllib.error.URLError as e:
        raise ValueError(f"Network error while calling LLM API: {e}") from e


def iter_openai_text_chunks(response: Any) -> Iterator[str]:
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="ignore").strip()
        if not line or not line.startswith("data:"):
            continue

        data = line[5:].strip()
        if data == "[DONE]":
            return

        event = json.loads(data)
        for choice in event.get("choices", []):
            delta = choice.get("delta", {})
            text = extract_text_content(delta.get("content", ""))
            if text:
                yield text


def call_llm(provider: str, model: str, api_key: str, messages: list[dict[str, str]], temperature: float = 1.0) -> str:
    try:
        with _open_llm_response(
            provider=provider,
            model=model,
            api_key=api_key,
            messages=messages,
            temperature=temperature,
        ) as res:
            body = json.loads(res.read().decode("utf-8"))
            message = body["choices"][0]["message"]
            content = extract_text_content(message.get("content", ""))
            if not content:
                raise ValueError("LLM API returned an empty response.")
            return content
    except KeyError as e:
        raise ValueError("LLM API returned an unexpected response format.") from e


def run_batch(document_text: str, prompt_csv_text: str, provider: str, model: str, api_key: str) -> list[dict[str, Any]]:
    prompts = parse_prompt_csv_text(prompt_csv_text)
    kb_context = build_knowledge_context()

    results: list[dict[str, Any]] = []
    for p in prompts:
        try:
            user_prompt = render_prompt(p.user_prompt_template, document_text)
            system_prompt = (
                "あなたは業務文書アシスタントです。"
                "与えられた文書とナレッジベースを参照して、正確・簡潔に回答してください。"
            )
            if kb_context:
                system_prompt += f"\n\n# ナレッジベース\n{kb_context}"

            answer = call_llm(
                provider=provider,
                model=model,
                api_key=api_key,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=p.temperature,
            )
            results.append(
                {
                    "prompt_id": p.prompt_id,
                    "prompt_name": p.prompt_name,
                    "status": "completed",
                    "response": answer,
                    "error_message": None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "prompt_id": p.prompt_id,
                    "prompt_name": p.prompt_name,
                    "status": "failed",
                    "response": None,
                    "error_message": str(exc),
                }
            )
    return results


def generate_contract(request_text: str, provider: str, model: str, api_key: str) -> str:
    kb_context = build_knowledge_context(limit=12)
    system_prompt = (
        "あなたは法務アシスタントです。ユーザー要望に応じて契約書草案を作成してください。"
        "ナレッジベースに契約雛型・条項例があれば優先して活用し、不足部分は一般的な表現で補完してください。"
        "出力は日本語で、見出し付きで構成してください。"
    )
    if kb_context:
        system_prompt += f"\n\n# 契約書ナレッジベース\n{kb_context}"

    user_prompt = f"次の要件で契約書の草案を作成してください。\n\n{request_text}"

    return call_llm(
        provider=provider,
        model=model,
        api_key=api_key,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=1.0,
    )


class AppHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text_stream_headers(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _write_text_stream_chunk(self, text: str) -> None:
        if not text:
            return
        self.wfile.write(text.encode("utf-8"))
        self.wfile.flush()

    def _relay_text_stream(
        self,
        provider: str,
        model: str,
        api_key: str,
        messages: list[dict[str, str]],
        temperature: float = 1.0,
    ) -> None:
        with _open_llm_response(
            provider=provider,
            model=model,
            api_key=api_key,
            messages=messages,
            temperature=temperature,
            stream=True,
        ) as res:
            self._send_text_stream_headers()
            try:
                for chunk in iter_openai_text_chunks(res):
                    self._write_text_stream_chunk(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                try:
                    self._write_text_stream_chunk(f"\n\n[受信エラー] {exc}")
                except (BrokenPipeError, ConnectionResetError):
                    return

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _serve_static(self, rel_path: str) -> None:
        target = (WEB_DIR / rel_path).resolve()
        if WEB_DIR.resolve() not in target.parents and target != WEB_DIR.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.exists() or target.is_dir():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content = target.read_bytes()
        if target.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif target.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        else:
            ctype = "application/octet-stream"

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/api/knowledge":
            self._send_json(200, {"items": list_knowledge()})
            return

        if parsed.path.startswith("/assets/"):
            self._serve_static(parsed.path.lstrip("/"))
            return

        if parsed.path in {"/", "/index.html"}:
            self._serve_static("index.html")
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/knowledge":
                body = self._read_json_body()
                item = add_knowledge(body.get("title", ""), body.get("content", ""))
                self._send_json(201, {"item": item})
                return

            if parsed.path == "/api/chat":
                body = self._read_json_body()
                provider = body.get("provider", "openai")
                model = body.get("model", "gpt-4o-mini")
                api_key = body.get("api_key", "")
                message = body.get("message", "")
                kb_context = build_knowledge_context()

                system = "あなたは業務向けAIアシスタントです。日本語で明確に回答してください。"
                if kb_context:
                    system += f"\n\n# ナレッジベース\n{kb_context}"

                response = call_llm(
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": message},
                    ],
                )
                self._send_json(200, {"response": response})
                return

            if parsed.path == "/api/chat/stream":
                body = self._read_json_body()
                provider = body.get("provider", "openai")
                model = body.get("model", "gpt-4o-mini")
                api_key = body.get("api_key", "")
                message = body.get("message", "")
                kb_context = build_knowledge_context()

                system = "あなたは業務向けAIアシスタントです。日本語で明確に回答してください。"
                if kb_context:
                    system += f"\n\n# ナレッジベース\n{kb_context}"

                self._relay_text_stream(
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": message},
                    ],
                )
                return

            if parsed.path == "/api/batch":
                body = self._read_json_body()
                results = run_batch(
                    document_text=body.get("document_text", ""),
                    prompt_csv_text=body.get("prompt_csv_text", ""),
                    provider=body.get("provider", "openai"),
                    model=body.get("model", "gpt-4o-mini"),
                    api_key=body.get("api_key", ""),
                )
                self._send_json(200, {"job_id": str(uuid.uuid4()), "results": results})
                return

            if parsed.path == "/api/generate_contract":
                body = self._read_json_body()
                draft = generate_contract(
                    request_text=body.get("request_text", ""),
                    provider=body.get("provider", "openai"),
                    model=body.get("model", "gpt-4o-mini"),
                    api_key=body.get("api_key", ""),
                )
                self._send_json(200, {"draft": draft})
                return

            if parsed.path == "/api/generate_contract/stream":
                body = self._read_json_body()
                request_text = body.get("request_text", "")
                provider = body.get("provider", "openai")
                model = body.get("model", "gpt-4o-mini")
                api_key = body.get("api_key", "")

                kb_context = build_knowledge_context(limit=12)
                system_prompt = (
                    "あなたは法務アシスタントです。ユーザー要望に応じて契約書草案を作成してください。"
                    "ナレッジベースに契約雛型・条項例があれば優先して活用し、不足部分は一般的な表現で補完してください。"
                    "出力は日本語で、見出し付きで構成してください。"
                )
                if kb_context:
                    system_prompt += f"\n\n# 契約書ナレッジベース\n{kb_context}"

                user_prompt = f"次の要件で契約書の草案を作成してください。\n\n{request_text}"
                self._relay_text_stream(
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=1.0,
                )
                return

            self.send_error(HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:  # unexpected failure
            self._send_json(500, {"error": f"internal error: {exc}"})

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/knowledge":
            q = parse_qs(parsed.query)
            item_id = int((q.get("id") or ["0"])[0])
            if item_id <= 0:
                self._send_json(400, {"error": "id is required"})
                return
            delete_knowledge(item_id)
            self._send_json(200, {"ok": True})
            return
        self.send_error(HTTPStatus.NOT_FOUND)


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    init_db()
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Server started at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
