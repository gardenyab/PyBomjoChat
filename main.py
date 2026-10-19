"""Local web chat for LiteRT-LM models."""

from __future__ import annotations

import html.parser
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request, urlopen

import litert_lm
from litert_lm.interfaces import ToolEventHandler


ROOT = Path(__file__).resolve().parent
MODELS_DIR = ROOT / "models"
CHAT_FILE = ROOT / ".chats.json"
STATIC_DIR = ROOT / "static"
THINKING_CONFIG = litert_lm.ThinkingConfig(
    enable_thinking=True, thinking_token_budget=1024
)
LOGGER = logging.getLogger("pybomjochat")


def discover_models(models_dir: Path = MODELS_DIR) -> list[dict[str, str]]:
    """Return available LiteRT-LM model files, excluding generated caches."""
    if not models_dir.exists():
        return []
    return [
        {"name": path.stem, "filename": path.name}
        for path in sorted(models_dir.glob("*.litertlm"), key=lambda item: item.name.lower())
        if path.is_file()
    ]


def workspace_file(filepath: str) -> Path:
    path = (ROOT / filepath).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as error:
        raise ValueError("Путь должен находиться внутри папки проекта.") from error
    return path


def create_file(filepath: str, content: str) -> str:
    """Create or replace a UTF-8 file inside the project folder."""
    path = workspace_file(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"Файл «{path.relative_to(ROOT)}» сохранён."


def edit_file(filepath: str, content: str) -> str:
    """Replace the contents of a UTF-8 file inside the project folder."""
    return create_file(filepath, content)


def read_file(filepath: str) -> str:
    """Read a UTF-8 file from inside the project folder."""
    path = workspace_file(filepath)
    if not path.is_file():
        raise FileNotFoundError(f"Файл «{filepath}» не найден.")
    return path.read_text(encoding="utf-8")


class SearchResultsParser(html.parser.HTMLParser):
    """Extract DuckDuckGo's result titles, links, and snippets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._capture: str | None = None
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self._current = {"title": "", "url": attributes.get("href") or ""}
            self._capture = "title"
        elif tag == "a" and "result__snippet" in classes and self.results:
            self._current = self.results[-1]
            self._capture = "snippet"
        elif tag == "a":
            self._capture = None

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            if self._capture == "title" and self._current is not None:
                self.results.append(self._current)
            self._capture = None
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._capture and self._current is not None:
            self._current[self._capture] = (
                self._current.get(self._capture, "") + data
            )


def web_search(query: str) -> str:
    """Search the web for recent information and return titles, snippets, and URLs."""
    request = Request(
        "https://html.duckduckgo.com/html/?q=" + quote_plus(query),
        headers={"User-Agent": "Mozilla/5.0 (compatible; PyBomjoChat/1.0)"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            page = response.read().decode("utf-8", errors="replace")
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"Не удалось выполнить веб-поиск: {error}") from error

    parser = SearchResultsParser()
    parser.feed(page)
    lines = []
    for result in parser.results[:5]:
        parsed_url = urlparse(result["url"])
        if parsed_url.hostname == "duckduckgo.com" and parsed_url.path == "/l/":
            result["url"] = parse_qs(parsed_url.query).get("uddg", [result["url"]])[0]
        lines.append(
            f"{result['title'].strip()}\n{result.get('snippet', '').strip()}\n{result['url']}"
        )
    return "\n\n".join(lines) if lines else "По этому запросу ничего не найдено."


TOOLS = [create_file, edit_file, read_file, web_search]


def text_from_message(message: Any) -> str:
    """Extract visible text from a LiteRT-LM streaming message."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content", [])
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") in (None, "text"):
            value = item.get("text")
            if isinstance(value, str):
                parts.append(value)
    return "".join(parts)


class ChatStore:
    def __init__(self, path: Path = CHAT_FILE) -> None:
        self.path = path
        self.lock = threading.RLock()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                chats = data["chats"]
                if not isinstance(chats, list):
                    raise ValueError("Поле chats должно быть списком.")
                self.chats = {chat["id"]: chat for chat in chats}
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                raise RuntimeError(f"Не удалось прочитать историю чатов: {error}") from error
        else:
            self.chats: dict[str, dict[str, Any]] = {}

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary_path.write_text(
                json.dumps({"chats": list(self.chats.values())}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary_path, self.path)

    def create(self, model: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        chat = {
            "id": uuid.uuid4().hex,
            "title": "Новый чат",
            "model": model,
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "history": [],
        }
        with self.lock:
            self.chats[chat["id"]] = chat
            self.save()
        return chat

    def get(self, chat_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self.chats.get(chat_id)

    def summaries(self) -> list[dict[str, str]]:
        with self.lock:
            return [
                {
                    "id": chat["id"],
                    "title": chat["title"],
                    "model": chat["model"],
                    "updated_at": chat["updated_at"],
                }
                for chat in sorted(
                    self.chats.values(),
                    key=lambda item: item["updated_at"],
                    reverse=True,
                )
            ]

    def delete(self, chat_id: str) -> bool:
        with self.lock:
            if chat_id not in self.chats:
                return False
            del self.chats[chat_id]
            self.save()
            return True


class ChatToolEvents(ToolEventHandler):
    def __init__(
        self,
        history: list[dict[str, Any]],
        emit: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self.history = history
        self.emit = emit
        self.pending_tools: list[tuple[str, str]] = []

    def approve_tool_call(self, tool_call: dict[str, Any]) -> bool:
        function = tool_call.get("function", {})
        name = str(function.get("name", "инструмент"))
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"arguments": arguments}
        if not isinstance(arguments, dict):
            arguments = {"arguments": arguments}

        last = self.history[-1] if self.history else {}
        if last.get("role") == "assistant" and "tool_calls" in last:
            last["tool_calls"].append(tool_call)
        else:
            self.history.append({"role": "assistant", "tool_calls": [tool_call]})
        self.pending_tools.append((name, str(arguments.get("query", ""))))
        self.emit("tool_start", {"name": name, "arguments": arguments})
        return True

    def process_tool_response(self, tool_response: Any) -> Any:
        name, query = self.pending_tools.pop(0) if self.pending_tools else ("инструмент", "")
        self.history.append(
            {
                "role": "tool",
                "content": [{"type": "tool_response", "name": name, "response": tool_response}],
            }
        )
        self.emit(
            "tool_end",
            {"name": name, "query": query, "result": str(tool_response)[:1600]},
        )
        return tool_response


class ChatApplication:
    def __init__(self, models_dir: Path = MODELS_DIR, store: ChatStore | None = None) -> None:
        self.models_dir = models_dir
        self.store = store or ChatStore()
        self.operation_lock = threading.RLock()
        self.engine: Any = None
        self.engine_model: str | None = None
        self.conversations: dict[str, Any] = {}

    def models(self) -> list[dict[str, str]]:
        return discover_models(self.models_dir)

    def close(self) -> None:
        with self.operation_lock:
            for conversation in self.conversations.values():
                conversation.close()
            self.conversations.clear()
            if self.engine is not None:
                self.engine.close()
                self.engine = None
                self.engine_model = None

    def select_model(self, chat: dict[str, Any], model_name: str) -> dict[str, Any]:
        with self.operation_lock:
            available = self.models()
            selected = next(
                (
                    model
                    for model in available
                    if model["name"] == model_name
                    or model["filename"] == model_name
                ),
                None,
            )
            if selected is None:
                raise ValueError(f"Модель «{model_name}» не найдена в папке models.")
            chat["model"] = selected["filename"]
            chat["updated_at"] = datetime.now(timezone.utc).isoformat()
            conversation = self.conversations.pop(chat["id"], None)
            if conversation is not None:
                conversation.close()
            self.store.save()
            return selected

    def _ensure_conversation(
        self, chat: dict[str, Any], emit: Callable[[str, dict[str, Any]], None]
    ) -> Any:
        model_filename = chat["model"]
        if self.engine_model != model_filename:
            self.close()
            model_path = (self.models_dir / model_filename).resolve()
            if model_path.parent != self.models_dir.resolve() or not model_path.is_file():
                raise FileNotFoundError(f"Файл модели «{model_filename}» не найден.")
            emit("phase", {"label": "Загружаю модель", "kind": "loading"})
            self.engine = litert_lm.Engine(str(model_path))
            self.engine_model = model_filename

        conversation = self.conversations.get(chat["id"])
        if conversation is None:
            events = ChatToolEvents(chat["history"], emit)
            conversation = self.engine.create_conversation(
                messages=chat["history"],
                tools=TOOLS,
                tool_event_handler=events,
                thinking_config=THINKING_CONFIG,
                system_message=(
                    "You are a helpful assistant. Reply in the user's language. "
                    "Use web_search when the user asks for current information, "
                    "news, or facts that may have changed. Cite the URLs returned by search. "
                    "Use file tools only when the user asks you to work with project files."
                ),
            )
            self.conversations[chat["id"]] = conversation
        else:
            conversation.tool_event_handler.emit = emit
        return conversation

    def stream_reply(
        self,
        chat: dict[str, Any],
        prompt: str,
        emit: Callable[[str, dict[str, Any]], None],
    ) -> None:
        with self.operation_lock:
            try:
                emit("phase", {"label": "Обдумываю запрос", "kind": "thinking"})
                conversation = self._ensure_conversation(chat, emit)
                now = datetime.now(timezone.utc).isoformat()
                chat["messages"].append({"role": "user", "content": prompt})
                chat["history"].append({"role": "user", "content": prompt})
                if chat["title"] == "Новый чат":
                    chat["title"] = prompt.replace("\n", " ").strip()[:48] or "Новый чат"
                chat["updated_at"] = now
                self.store.save()

                answer_parts: list[str] = []
                emitted_answer = False
                for chunk in conversation.send_message_async(prompt):
                    if not emitted_answer:
                        emit("phase", {"label": "Формирую ответ", "kind": "writing"})
                        emitted_answer = True
                    text = text_from_message(chunk)
                    if text:
                        answer_parts.append(text)
                        emit("delta", {"text": text})

                answer = "".join(answer_parts)
                chat["messages"].append({"role": "assistant", "content": answer})
                chat["history"].append({"role": "assistant", "content": answer})
                chat["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.store.save()
                emit("complete", {"message": chat["messages"][-1]})
            except Exception as error:
                LOGGER.exception("Generation failed for chat %s", chat["id"])
                self.store.save()
                emit("error", {"message": str(error) or error.__class__.__name__})


class ChatRequestHandler(BaseHTTPRequestHandler):
    server: "ChatHTTPServer"
    protocol_version = "HTTP/1.0"

    def log_message(self, format_string: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), format_string % args)

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError("Размер запроса должен быть от 1 байта до 1 МБ.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Ожидался JSON-объект.")
            return payload
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("Некорректный JSON в запросе.") from error

    def _chat_id(self, path: str) -> str | None:
        parts = path.strip("/").split("/")
        return parts[2] if len(parts) >= 3 and parts[:2] == ["api", "chats"] else None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/app.js", "/style.css"):
            filename = "index.html" if path in ("/", "/index.html") else path[1:]
            target = STATIC_DIR / filename
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
            }[target.suffix]
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/bootstrap":
            self._json(
                200,
                {"models": self.server.app.models(), "chats": self.server.app.store.summaries()},
            )
            return
        if path == "/api/models":
            self._json(200, {"models": self.server.app.models()})
            return
        chat_id = self._chat_id(path)
        if chat_id and path == f"/api/chats/{chat_id}":
            chat = self.server.app.store.get(chat_id)
            if chat is None:
                self._json(404, {"error": "Чат не найден."})
            else:
                self._json(200, chat)
            return
        self._json(404, {"error": "Страница не найдена."})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        app = self.server.app
        try:
            payload = self._body()
            if path == "/api/chats":
                models = app.models()
                if not models:
                    self._json(503, {"error": "В папке models нет файлов .litertlm."})
                    return
                requested = payload.get("model")
                model = (
                    next(
                        (
                            item
                            for item in models
                            if item["filename"] == requested or item["name"] == requested
                        ),
                        None,
                    )
                    if requested
                    else models[0]
                )
                if model is None:
                    raise ValueError(f"Модель «{requested}» не найдена в папке models.")
                self._json(201, app.store.create(model["filename"]))
                return

            chat_id = self._chat_id(path)
            chat = app.store.get(chat_id) if chat_id else None
            if chat is None:
                self._json(404, {"error": "Чат не найден."})
                return
            if path == f"/api/chats/{chat_id}/model":
                model = app.select_model(chat, str(payload.get("model", "")))
                self._json(200, {"model": model, "chat": chat})
                return
            if path == f"/api/chats/{chat_id}/messages":
                prompt = payload.get("content")
                if not isinstance(prompt, str) or not prompt.strip():
                    self._json(400, {"error": "Сообщение не должно быть пустым."})
                    return
                self._stream(chat, prompt.strip())
                return
            self._json(404, {"error": "Страница не найдена."})
        except ValueError as error:
            self._json(400, {"error": str(error)})
        except Exception as error:
            LOGGER.exception("Request failed: %s %s", self.command, path)
            self._json(500, {"error": str(error) or error.__class__.__name__})

    def _stream(self, chat: dict[str, Any], prompt: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        connected = True

        def emit(event: str, data: dict[str, Any]) -> None:
            nonlocal connected
            if not connected:
                return
            payload = json.dumps({"type": event, **data}, ensure_ascii=False)
            try:
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                connected = False

        self.server.app.stream_reply(chat, prompt, emit)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        chat_id = self._chat_id(path)
        if chat_id is None or path != f"/api/chats/{chat_id}":
            self._json(404, {"error": "Страница не найдена."})
            return
        app = self.server.app
        with app.operation_lock:
            conversation = app.conversations.pop(chat_id, None)
            if conversation is not None:
                conversation.close()
            if not app.store.delete(chat_id):
                self._json(404, {"error": "Чат не найден."})
            else:
                self._json(200, {"deleted": True})


class ChatHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: ChatApplication) -> None:
        self.app = app
        super().__init__(address, ChatRequestHandler)


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = ChatApplication()
    models = app.models()
    if not models:
        raise FileNotFoundError(
            f"Не найдены модели LiteRT-LM в папке {MODELS_DIR}. "
            "Поместите туда файл с расширением .litertlm."
        )
    server = ChatHTTPServer((host, port), app)
    print(f"PyBomjoChat started: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__":
    run_server()
