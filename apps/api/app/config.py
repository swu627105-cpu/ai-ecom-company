"""配置：从 apps/api/.env 读取，不把密钥写进代码。"""
from __future__ import annotations

import os
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


if os.getenv("AIECOM_SKIP_DOTENV") != "1":
    load_env(Path(__file__).resolve().parent.parent / ".env")

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://discovery-api.intern-ai.org.cn/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "intern-s2")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
