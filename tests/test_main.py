import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cli import TerminalChat, parse_command
from main import (
    ChatApplication,
    ChatStore,
    ChatToolEvents,
    SearchResultsParser,
    discover_models,
    text_from_message,
    workspace_file,
)


class ModelDiscoveryTests(unittest.TestCase):
    def test_discovers_only_model_files_in_stable_order(self):
        with tempfile.TemporaryDirectory() as directory:
            models = Path(directory)
            (models / "zeta.litertlm").touch()
            (models / "alpha.litertlm").touch()
            (models / "alpha.litertlm_cache").touch()
            (models / "folder.litertlm").mkdir()

            self.assertEqual(
                discover_models(models),
                [
                    {"name": "alpha", "filename": "alpha.litertlm"},
                    {"name": "zeta", "filename": "zeta.litertlm"},
                ],
            )


class CLICommandTests(unittest.TestCase):
    def test_slash_commands_are_parsed_and_messages_are_not(self):
        self.assertEqual(parse_command("/model gemma-4-E2B-it"), ("model", ["gemma-4-E2B-it"]))
        self.assertEqual(parse_command('/open "chat id"'), ("open", ["chat id"]))
        self.assertIsNone(parse_command("ordinary message"))

    def test_malformed_slash_command_reports_the_parse_error(self):
        with self.assertRaisesRegex(ValueError, "Некорректная команда"):
            parse_command('/open "unfinished')

    def test_terminal_exits_cleanly_on_eof_and_persists_new_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models_dir = root / "models"
            models_dir.mkdir()
            (models_dir / "local.litertlm").touch()
            store = ChatStore(root / "chats.json")
            app = ChatApplication(models_dir, store)
            output = StringIO()

            TerminalChat(
                app,
                input_stream=StringIO(""),
                output_stream=output,
            ).run()

            self.assertEqual(len(store.summaries()), 1)
            self.assertIn("PyBomjoChat", output.getvalue())


class ChatStoreTests(unittest.TestCase):
    def test_chat_history_survives_store_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chats.json"
            store = ChatStore(path)
            chat = store.create("gemma.litertlm")
            chat["messages"].append({"role": "user", "content": "Привет"})
            chat["history"].append({"role": "user", "content": "Привет"})
            store.save()

            restored = ChatStore(path).get(chat["id"])

            self.assertIsNotNone(restored)
            self.assertEqual(restored["messages"][0]["content"], "Привет")
            self.assertEqual(restored["history"][0]["role"], "user")

    def test_corrupt_chat_file_reports_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chats.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "историю чатов"):
                ChatStore(path)


class StreamingTests(unittest.TestCase):
    def test_streams_output_and_replays_history_after_model_switch(self):
        class FakeConversation:
            def __init__(self, messages):
                self.messages = messages
                self.closed = False

            def send_message_async(self, prompt):
                yield {"content": [{"type": "text", "text": f"Ответ: {prompt}"}]}

            def close(self):
                self.closed = True

        class FakeEngine:
            def __init__(self, model_path):
                self.model_path = model_path
                self.closed = False

            def create_conversation(self, **kwargs):
                histories.append(list(kwargs["messages"]))
                conversation = FakeConversation(list(kwargs["messages"]))
                conversations.append(conversation)
                return conversation

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models_dir = root / "models"
            models_dir.mkdir()
            (models_dir / "first.litertlm").touch()
            (models_dir / "second.litertlm").touch()
            store = ChatStore(root / "chats.json")
            chat = store.create("first.litertlm")
            histories = []
            conversations = []
            events = []
            app = ChatApplication(models_dir, store)

            with patch("main.litert_lm.Engine", side_effect=FakeEngine):
                app.stream_reply(chat, "первый вопрос", lambda name, data: events.append((name, data)))
                app.select_model(chat, "second.litertlm")
                app.stream_reply(chat, "второй вопрос", lambda name, data: events.append((name, data)))
                app.close()

            self.assertEqual(histories[0], [])
            self.assertEqual(
                [message["content"] for message in histories[1]],
                ["первый вопрос", "Ответ: первый вопрос"],
            )
            self.assertIn(
                ("delta", {"text": "Ответ: второй вопрос"}),
                events,
            )
            self.assertTrue(conversations[0].closed)


class ToolAndMessageTests(unittest.TestCase):
    def test_tool_events_add_replayable_tool_messages_and_emit_status(self):
        history = []
        events = []
        handler = ChatToolEvents(history, lambda event, data: events.append((event, data)))
        call = {
            "id": "call-1",
            "type": "function",
            "function": {"name": "web_search", "arguments": '{"query":"новости"}'},
        }

        self.assertTrue(handler.approve_tool_call(call))
        handler.process_tool_response("Результаты поиска")

        self.assertEqual(history[0]["tool_calls"], [call])
        self.assertEqual(history[1]["content"][0]["name"], "web_search")
        self.assertEqual(events[0][0], "tool_start")
        self.assertEqual(events[1][0], "tool_end")

    def test_streamed_message_text_is_extracted(self):
        self.assertEqual(
            text_from_message(
                {"content": [{"type": "text", "text": "поток "}, {"type": "text", "text": "текста"}]}
            ),
            "поток текста",
        )

    def test_search_parser_extracts_titles_and_snippets(self):
        parser = SearchResultsParser()
        parser.feed(
            '<a class="result__a" href="https://example.com">Заголовок</a>'
            '<a class="result__snippet">Краткое описание</a>'
        )
        self.assertEqual(parser.results[0]["title"], "Заголовок")
        self.assertEqual(parser.results[0]["snippet"], "Краткое описание")


class WorkspacePathTests(unittest.TestCase):
    def test_rejects_paths_outside_project(self):
        with self.assertRaisesRegex(ValueError, "внутри папки проекта"):
            workspace_file("..\\outside.txt")


if __name__ == "__main__":
    unittest.main()
