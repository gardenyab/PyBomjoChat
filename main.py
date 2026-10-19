from pathlib import Path
import litert_lm


def create_file(filepath: str, content: str) -> str:
  """Создает файл с указанным содержимым."""
  try:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"Успех: файл '{filepath}' создан."
  except Exception as e:
    return f"Ошибка при создании файла: {e}"


def edit_file(filepath: str, content: str) -> str:
  """Перезаписывает или редактирует файл."""
  return create_file(filepath, content)


def read_file(filepath: str) -> str:
  """Читает содержимое файла."""
  try:
    path = Path(filepath)
    if path.exists():
      return path.read_text(encoding="utf-8")
    return f"Ошибка: файл '{filepath}' не найден."
  except Exception as e:
    return f"Ошибка при чтении файла: {e}"


tools_list = [create_file, edit_file, read_file]


def run_agent():
  model_path = "./models/gemma-4-E2B-it.litertlm"

  with litert_lm.Engine(model_path) as engine:
    thinking_config = litert_lm.ThinkingConfig(
        enable_thinking=True, thinking_token_budget=1024
    )

    with engine.create_conversation(tools=tools_list) as conversation:
      print("🤖 Агент запущен! Напишите задачу (или 'exit' для выхода).")

      while True:
        user_input = input("\nВы: ")
        if user_input.lower() in ["exit", "quit"]:
          break

        response = conversation.send_message(
            user_input, thinking_config=thinking_config
        )

        thought = response.get("channels", {}).get("thought")
        if thought:
          print(f"\n🧠 Мысли:\n{thought}")

        content = response.get("content", [])
        if content and "text" in content[0]:
          print(f"\n🤖 Ответ:\n{content[0]['text']}")


if __name__ == "__main__":
  run_agent()