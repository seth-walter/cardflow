import os
from pathlib import Path

# The project's .env, wherever the app was launched from.
ENV_PATH = str(Path(__file__).resolve().parents[1] / ".env")


def save_env_value(key: str, value: str, env_path: str = ENV_PATH) -> None:
    lines = []
    found = False

    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
