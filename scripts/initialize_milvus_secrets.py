"""Generate local Docker secrets once; never print or overwrite credentials."""
import os
from pathlib import Path
import secrets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS = {"admin_token": "root", "api_token": "kb_api", "worker_token": "kb_worker"}


def initialize(directory):
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    for name, username in ACCOUNTS.items():
        path = directory / name
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(username + ":" + secrets.token_urlsafe(36) + "\n")
        created.append(name)
    return created


if __name__ == "__main__":
    print("已生成密钥文件（已有文件保持不变）：" + ", ".join(initialize(PROJECT_ROOT / ".secrets" / "milvus")))
