# app/services/index_updater.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any, List
import sys
import requests

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from config.settings import cfg  # Импортируем cfg из config.settings
except ImportError as e:
    print(f"Ошибка импорта config.settings: {e}")
    print(f"PROJECT_ROOT: {PROJECT_ROOT}")
    print(f"Путь к settings.py: {PROJECT_ROOT / 'config' / 'settings.py'}")
    raise



from config.settings import Config  # содержит MODEL_QWEN, OPENROUTER_API_KEY [file:203]
from app.services.project_scanner import ProjectScanner
from app.services.file_io_tools import FileReaderTool, ReadRequest
from app.utils.token_counter import TokenCounter

QWEN_CONTEXT_LIMIT = 20_000
SYSTEM_PROMPT = """You are "IndexWorker", a deterministic code analysis agent.
Your only job is to analyze one code chunk at a time and return a SHORT, PRECISE JSON description of it.

CONTEXT:
- You will receive XML with one hunk> element per request.
- The code is inside a tent><![CDATA[ ... ]]></content> section.
- For Python/Go/SQL, indentation and formatting MUST be preserved exactly as you see them.
- You MAY also receive text> with <imports>, <globals> and <parent_class> to help you understand the code.
- Do NOT modify or rewrite the code. You only describe it.

LANGUAGE:
- The chunk can be one of: Python, Go, SQL, JSON.
- Infer the language from the code structure.
- If you are not confident, set "language": "unknown" in your JSON.

TOKEN LIMIT:
- The host system will not send you more than you can handle.
- DO NOT request full files if the tool already reports "file is too large" or "chunks only".
- If you need more context, say so in the "notes" field instead of guessing.

YOUR TASKS FOR EACH CHUNK:
You must:
1) Detect whether the chunk has syntactic problems.
   - For Python: think like a Python parser. Pay special attention to indentation.
   - For Go and SQL: check for obvious syntax errors or incomplete constructs.
   - For JSON: invalid JSON structure.
2) Identify which methods/functions and files this chunk directly calls or references.
   - Only report what is explicitly visible in the chunk.
   - Do NOT invent or guess calls that you do not see in the code.
3) Provide a very short, concrete description of what this chunk does.
   - 1–3 short sentences maximum.
   - Focus on behavior and side effects, not generalities.

STRICT ANTI-HALLUCINATION RULES:
- NEVER invent functions, methods, classes, variables or files that are not clearly visible in the given code or in the provided context.
- If you are not sure, use null or an empty list and explain briefly in "notes".
- If you cannot determine something, say you cannot determine it. Do NOT guess.
- Do NOT assume the presence of external services, databases or APIs unless they are explicitly referenced in the code.

OUTPUT FORMAT (MUST BE VALID JSON, NO EXTRA TEXT):
You MUST respond with a single valid JSON object, nothing else. No explanations, no Markdown, no comments.

Use this exact schema:

{
  "target": {
    "file": "relative/path/to/file.ext",
    "kind": "method | function | class | struct | interface | procedure | trigger | query | json_chunk | unknown",
    "name": "symbol name or null",
    "parent": "parent class/struct name or null",
    "language": "python | go | sql | json | unknown"
  },
  "syntax_ok": true or false,
  "syntax_errors": [
    "Short description of syntax/indentation problem, or empty if none"
  ],
  "calls": [
    {
      "type": "function | method | procedure | query | other",
      "name": "callee name if visible, otherwise null",
      "class": "class/struct name if this is a method call, otherwise null",
      "module": "import/module name if clearly referenced, otherwise null"
    }
  ],
  "files_touched": [
    "relative/path/to/other/file.ext if clearly referenced, otherwise empty"
  ],
  "summary": "1–3 short sentences describing what this chunk does, in a concrete and non-generic way.",
  "notes": "Any uncertainties, limitations, or requests for more context. If none, use an empty string."
}

DETAILS:
- "target.file": read from the "file" or "path" attribute in the hunk> tag when available, otherwise null.
- "target.kind": read from the "kind" attribute when available, otherwise infer from the code (e.g. class, method, function, struct, interface, procedure, trigger, query, json_chunk).
- "target.name": read from the "name" attribute when available, otherwise infer from the code, or null if unknown.
- "target.parent": read from the "parent" attribute for methods, or infer from context, otherwise null.
- "calls": include only direct calls that appear in the chunk (e.g. user.login(), db.execute(), send_email()).
  - If you see a method call like obj.save(), but cannot infer its class, set "class": null.
  - Do NOT include built-in operators or simple arithmetic as calls.
- "files_touched": only include file paths if they are explicitly referenced as paths or imports that clearly map to files in the project structure. If uncertain, leave empty.

ABSOLUTE CONSTRAINTS:
- OUTPUT MUST BE VALID JSON. No trailing commas.
- DO NOT INCLUDE any XML or code in your JSON.
- DO NOT RETURN the original code.
- DO NOT exceed the given context: analyze ONLY the provided chunk and optional context.
"""


class IndexUpdater:
    """
    Отвечает за индексацию проекта с помощью Qwen:
    - Берёт project_map.json
    - Находит изменённые файлы
    - Чанкирует их через FileReaderTool
    - Посылает чанки в Qwen с системным промптом
    - Обновляет semantic_index.json
    """

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.scanner = ProjectScanner(project_root)
        self.reader = FileReaderTool(project_root)
        self.token_counter = TokenCounter()

        self.api_key = cfg.OPENROUTER_API_KEY
        self.base_url = cfg.OPENROUTER_BASE_URL
        self.model = cfg.MODEL_QWEN  # должен быть qwen/qwen-2.5-coder-32b-instruct

    def _call_qwen(self, messages: List[Dict[str, str]]) -> str:
        """
        Вызов Qwen через OpenRouter.
        Ожидается, что Qwen вернёт JSON-строку с результатами анализа.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 2000,
            "temperature": 0.1,
        }
        resp = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _load_project_map(self) -> Dict[str, Any]:
        path = self.project_root / "project_map.json"
        if not path.exists():
            return self.scanner.scan()
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_semantic_index(self) -> Dict[str, Any]:
        path = self.project_root / "semantic_index.json"
        if not path.exists():
            return {"files": {}}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_semantic_index(self, index: Dict[str, Any]) -> None:
        path = self.project_root / "semantic_index.json"
        path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    def update_index(self) -> None:
        """
        Главный метод: запускает индексацию изменённых файлов.
        """
        project_map = self._load_project_map()
        semantic_index = self._load_semantic_index()

        # Простейшая логика изменений: сравниваем hash
        existing_files = semantic_index.get("files", {})
        changed_files = []

        for f in project_map["files"]:
            path = f["path"]
            file_hash = f["hash"]
            if path not in existing_files or existing_files[path].get("hash") != file_hash:
                changed_files.append(f)

        if not changed_files:
            return

        for f in changed_files:
            rel_path = f["path"]
            file_type = f["type"]

            # Чанкируем файл (в XML) с учётом лимитов Qwen
            # 1. оценка токенов системного промпта
            prompt_tokens = self.token_counter.count(SYSTEM_PROMPT)
            available_for_chunks = max(QWEN_CONTEXT_LIMIT - prompt_tokens - 2000, 2000)

            read_req = ReadRequest(
                project_root=str(self.project_root),
                path=rel_path,
                mode="chunks",
                available_tokens=available_for_chunks,
                with_context=True,
            )
            read_res = self.reader.read(read_req)
            if not read_res.ok:
                continue
            if not read_res.chunks_xml:
                continue

            file_entries = []

            # Шлём чанки батчами, чтобы не превышать лимит
            for chunk_xml in read_res.chunks_xml:
                # Подсчитываем токены чанка
                chunk_tokens = self.token_counter.count(chunk_xml)
                if prompt_tokens + chunk_tokens > QWEN_CONTEXT_LIMIT:
                    continue

                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": chunk_xml},
                ]
                reply = self._call_qwen(messages)

                # reply должен быть JSON — парсим
                try:
                    info = json.loads(reply)
                except json.JSONDecodeError:
                    continue

                # info ожидается вида:
                # {
                #   "target": {"kind": "method", "name": "login", "parent": "User", "file": "app/auth.py"},
                #   "syntax_ok": true,
                #   "syntax_errors": [],
                #   "calls": [{"type": "method", "name": "check_password", "class": "User"}, ...],
                #   "files_touched": ["db/session.py"],
                #   "summary": "Short description..."
                # }
                file_entries.append(info)

            # Обновляем semantic_index для файла
            semantic_index["files"][rel_path] = {
                "hash": f["hash"],
                "type": file_type,
                "tokens_total": f["tokens_total"],
                "entries": file_entries,
            }

        self._save_semantic_index(semantic_index)
