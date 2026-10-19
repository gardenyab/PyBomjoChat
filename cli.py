"""Interactive terminal chat for the local LiteRT-LM assistant."""

from __future__ import annotations

import shlex
import shutil
import sys
import threading
from typing import TextIO

from main import ChatApplication, MODELS_DIR


RESET = "\033[0m"
DIM = "\033[2m"
ACCENT = "\033[38;5;180m"
GREEN = "\033[38;5;108m"
ERROR = "\033[38;5;167m"
SPINNER = "◐◓◑◒"


def parse_command(line: str) -> tuple[str, list[str]] | None:
    """Parse a slash command, leaving ordinary messages untouched."""
    stripped = line.strip()
    if not stripped.startswith("/"):
        return None
    try:
        parts = shlex.split(stripped[1:])
    except ValueError as error:
        raise ValueError(f"Некорректная команда: {error}") from error
    if not parts:
        return None
    return parts[0].lower(), parts[1:]


def enable_terminal_colors() -> bool:
    return sys.stdout.isatty()


class TerminalChat:
    def __init__(
        self,
        app: ChatApplication | None = None,
        *,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
    ) -> None:
        self.app = app or ChatApplication()
        self.input_stream = input_stream or sys.stdin
        self.output_stream = output_stream or sys.stdout
        self.colors = enable_terminal_colors() if self.output_stream is sys.stdout else False
        self.active_chat: dict[str, object] | None = None
        self._write_lock = threading.Lock()
        self._spinner_stop: threading.Event | None = None
        self._spinner_thread: threading.Thread | None = None
        self._answer_started = False

    def _styled(self, text: str, color: str) -> str:
        return f"{color}{text}{RESET}" if self.colors else text

    def _write(self, text: str, *, flush: bool = True) -> None:
        with self._write_lock:
            self.output_stream.write(text)
            if flush:
                self.output_stream.flush()

    def _stop_status(self, *, clear: bool = True) -> None:
        stop = self._spinner_stop
        thread = self._spinner_thread
        self._spinner_stop = None
        self._spinner_thread = None
        if stop is not None:
            stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        if clear and stop is not None and self.colors:
            width = max(20, shutil.get_terminal_size((80, 20)).columns - 1)
            self._write("\r" + (" " * width) + "\r")

    def _show_status(self, label: str) -> None:
        self._stop_status()
        if not self.colors:
            self._write(f"  · {label}\n")
            return

        stop = threading.Event()
        self._spinner_stop = stop

        def animate() -> None:
            index = 0
            while not stop.wait(0.11):
                symbol = SPINNER[index % len(SPINNER)]
                index += 1
                width = max(20, shutil.get_terminal_size((80, 20)).columns - 1)
                line = f"  {self._styled(symbol, ACCENT)}  {self._styled(label, DIM)}"
                self._write("\r" + line[:width].ljust(width))

        self._spinner_thread = threading.Thread(target=animate, daemon=True)
        self._spinner_thread.start()

    def _show_banner(self) -> None:
        model_filename = (
            str(self.active_chat["model"])
            if self.active_chat
            else self.app.models()[0]["filename"]
        )
        model = next(
            (
                item["name"]
                for item in self.app.models()
                if item["filename"] == model_filename
            ),
            model_filename.removesuffix(".litertlm"),
        )
        title = " PyBomjoChat "
        self._write("\n")
        self._write(self._styled(f"╭─{title}{'─' * 34}╮\n", ACCENT))
        self._write("│  Локальный AI-помощник · LiteRT-LM                         │\n")
        self._write(
            f"│  Модель: {model[:46]:<46}│\n"
        )
        self._write("╰──────────────────────────────────────────────────────────╯\n")
        self._write(
            self._styled(
                "  /help — команды   /model — сменить модель   /new — новый чат\n",
                DIM,
            )
        )

    def _summaries(self) -> list[dict[str, str]]:
        return self.app.store.summaries()

    def _print_chats(self) -> None:
        chats = self._summaries()
        if not chats:
            self._write("  Сохранённых чатов пока нет.\n")
            return
        self._write("  Сохранённые чаты:\n")
        for index, chat in enumerate(chats, start=1):
            marker = "›" if self.active_chat and chat["id"] == self.active_chat["id"] else " "
            self._write(f"  {marker} {index:>2}. {chat['title']}  {self._styled(chat['model'], DIM)}\n")

    def _create_chat(self) -> None:
        models = self.app.models()
        if not models:
            raise FileNotFoundError(f"В папке {MODELS_DIR} нет моделей .litertlm.")
        self.active_chat = self.app.store.create(models[0]["filename"])
        self._write(f"  Новый чат · {models[0]['name']}\n\n")

    def _open_chat(self, reference: str) -> None:
        chats = self._summaries()
        target = None
        if reference.isdigit():
            index = int(reference) - 1
            if 0 <= index < len(chats):
                target = chats[index]
        else:
            target = next(
                (chat for chat in chats if chat["id"].startswith(reference)),
                None,
            )
        if target is None:
            raise ValueError("Чат не найден. Используйте /chats, чтобы увидеть список.")

        chat = self.app.store.get(target["id"])
        if chat is None:
            raise ValueError("Чат уже удалён.")
        self.active_chat = chat
        self._write(f"\n  {self._styled(chat['title'], ACCENT)}\n")
        self._write(f"  {self._styled(chat['model'], DIM)}\n\n")
        for message in chat["messages"]:
            role = message.get("role")
            if role == "user":
                self._write(f"  {self._styled('Вы  ›', GREEN)} {message.get('content', '')}\n")
            elif role == "assistant":
                self._write(f"  {self._styled('AI  ◆', ACCENT)} {message.get('content', '')}\n")
        self._write("\n")

    def _select_model(self, requested: str | None) -> None:
        if self.active_chat is None:
            raise RuntimeError("Сначала создайте или откройте чат.")
        models = self.app.models()
        if not models:
            raise FileNotFoundError(f"В папке {MODELS_DIR} нет моделей .litertlm.")

        if requested:
            search = requested.casefold()
            selected = next(
                (
                    model
                    for model in models
                    if model["name"].casefold() == search
                    or model["filename"].casefold() == search
                ),
                None,
            )
            if selected is None and requested.isdigit():
                index = int(requested) - 1
                if 0 <= index < len(models):
                    selected = models[index]
            if selected is None:
                raise ValueError(f"Модель «{requested}» не найдена в папке models.")
        else:
            current = self.active_chat["model"]
            self._write("  Доступные модели:\n")
            for index, model in enumerate(models, start=1):
                marker = "›" if model["filename"] == current else " "
                self._write(f"  {marker} {index}. {model['name']}\n")
            choice = self._readline("  Номер или имя модели (Enter — оставить текущую): ").strip()
            if not choice:
                return
            return self._select_model(choice)

        self.app.select_model(self.active_chat, selected["filename"])
        self._write(f"  Модель переключена: {self._styled(selected['name'], ACCENT)}\n")

    def _delete_chat(self) -> None:
        if self.active_chat is None:
            raise RuntimeError("Нет активного чата.")
        chat_id = self.active_chat["id"]
        with self.app.operation_lock:
            conversation = self.app.conversations.pop(chat_id, None)
            if conversation is not None:
                conversation.close()
            if not self.app.store.delete(chat_id):
                raise ValueError("Чат уже удалён.")
        self._write("  Чат удалён.\n")
        chats = self._summaries()
        if chats:
            self._open_chat(chats[0]["id"])
        else:
            self._create_chat()

    def _handle_command(self, command: str, args: list[str]) -> bool:
        if command in {"exit", "quit", "q"}:
            return False
        if command in {"help", "?"}:
            self._write(
                "  Команды:\n"
                "    /new                 создать чат\n"
                "    /chats               показать сохранённые чаты\n"
                "    /open <номер|id>     открыть сохранённый чат\n"
                "    /model [номер|имя]   выбрать модель из папки models\n"
                "    /delete              удалить активный чат\n"
                "    /help                показать эту справку\n"
                "    /exit                выйти\n\n"
            )
        elif command == "new":
            self._create_chat()
        elif command in {"chats", "history"}:
            self._print_chats()
        elif command == "open":
            if len(args) != 1:
                raise ValueError("Использование: /open <номер или начало ID>")
            self._open_chat(args[0])
        elif command == "model":
            if len(args) > 1:
                raise ValueError("Использование: /model [номер или имя модели]")
            self._select_model(args[0] if args else None)
        elif command == "delete":
            self._delete_chat()
        else:
            raise ValueError(f"Неизвестная команда /{command}. Введите /help.")
        return True

    def _handle_stream_event(self, event: str, data: dict[str, object]) -> None:
        if event == "phase":
            self._show_status(str(data.get("label", "Работаю")))
        elif event == "tool_start":
            self._stop_status()
            name = str(data.get("name", "инструмент"))
            arguments = data.get("arguments", {})
            detail = ""
            if isinstance(arguments, dict):
                detail = str(arguments.get("query") or arguments.get("filepath") or "")
            self._write(f"  {self._styled('↳', ACCENT)} {name}")
            if detail:
                self._write(f"  {detail}")
            self._write("\n")
            self._show_status("Выполняю инструмент")
        elif event == "tool_end":
            self._stop_status()
            result = str(data.get("result", ""))
            result_preview = " ".join(result.split())
            if len(result_preview) > 240:
                result_preview = result_preview[:237] + "..."
            self._write(f"  {self._styled('✓', GREEN)} Инструмент завершён")
            if result_preview:
                self._write(f" · {result_preview}")
            self._write("\n")
            self._show_status("Обдумываю результаты")
        elif event == "delta":
            self._stop_status()
            if not self._answer_started:
                self._write(f"\n  {self._styled('AI  ◆', ACCENT)} ")
                self._answer_started = True
            self._write(str(data.get("text", "")))
        elif event == "complete":
            self._stop_status()
            if self._answer_started:
                self._write("\n\n")
            self._answer_started = False
        elif event == "error":
            self._stop_status()
            self._write(
                f"\n  {self._styled('Ошибка:', ERROR)} {data.get('message', 'Неизвестная ошибка')}\n\n"
            )
            self._answer_started = False

    def _send(self, prompt: str) -> None:
        if self.active_chat is None:
            raise RuntimeError("Сначала создайте или откройте чат.")
        self._write(f"  {self._styled('Вы  ›', GREEN)} {prompt}\n")
        self._answer_started = False
        try:
            self.app.stream_reply(
                self.active_chat,
                prompt,
                self._handle_stream_event,
            )
        finally:
            self._stop_status()
            self._answer_started = False
        self.active_chat = self.app.store.get(self.active_chat["id"]) or self.active_chat

    def _readline(self, prompt: str) -> str:
        self._write(self._styled(prompt, ACCENT), flush=False)
        self.output_stream.flush()
        line = self.input_stream.readline()
        if line == "":
            raise EOFError
        return line.rstrip("\r\n")

    def run(self) -> None:
        if hasattr(self.output_stream, "reconfigure"):
            self.output_stream.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(self.input_stream, "reconfigure"):
            self.input_stream.reconfigure(encoding="utf-8", errors="replace")
        models = self.app.models()
        if not models:
            raise FileNotFoundError(f"Не найдены модели LiteRT-LM в папке {MODELS_DIR}.")

        chats = self._summaries()
        if chats:
            self.active_chat = self.app.store.get(chats[0]["id"])
        self._show_banner()
        if chats:
            self._open_chat(chats[0]["id"])
        else:
            self._create_chat()

        try:
            while True:
                title = str(self.active_chat["title"]) if self.active_chat else "нет чата"
                prompt = self._readline(f"{title}  › ")
                if not prompt.strip():
                    continue
                if prompt.lstrip().startswith("/"):
                    try:
                        parsed = parse_command(prompt)
                        if parsed is not None and not self._handle_command(*parsed):
                            break
                    except (ValueError, RuntimeError, FileNotFoundError) as error:
                        self._write(f"  {self._styled('Ошибка:', ERROR)} {error}\n")
                    continue
                self._send(prompt.strip())
        except (EOFError, KeyboardInterrupt):
            self._write("\n  До встречи!\n")
        finally:
            self._stop_status()
            self.app.close()


def main() -> None:
    try:
        TerminalChat().run()
    except (FileNotFoundError, RuntimeError) as error:
        print(f"Ошибка запуска: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
