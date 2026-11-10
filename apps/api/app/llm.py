"""模型网关：OpenAI 协议，默认指向书生·端砚。

两件事：
1. 配置优先级 —— data/settings.json（界面里改）> .env > 默认值。
2. 调用与探测 —— 拿不到结果就抛错，由调用方回落规则话术，会议不能因为模型抖动开不下去。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT
from .paths import DATA_DIR

SETTINGS = DATA_DIR / "settings.json"


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _load() -> dict[str, str]:
    cfg = {"base_url": LLM_BASE_URL, "api_key": LLM_API_KEY, "model": LLM_MODEL}
    if SETTINGS.exists() and os.getenv("AIECOM_NO_SETTINGS") != "1":
        try:
            saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
            for k in cfg:
                if isinstance(saved.get(k), str) and saved[k].strip():
                    cfg[k] = saved[k].strip()
        except Exception:
            pass
    cfg["base_url"] = cfg["base_url"].rstrip("/")
    return cfg


_cfg = _load()
_cache: dict[str, Any] = {"at": 0.0, "value": None}
CACHE_TTL = 30


def enabled() -> bool:
    return bool(_cfg["api_key"])


def current() -> dict[str, str]:
    """给接口用：永远不回传明文密钥。"""
    key = _cfg["api_key"]
    hint = f"{key[:3]}…{key[-4:]}" if len(key) > 8 else ("已配置" if key else "")
    return {"base_url": _cfg["base_url"], "model": _cfg["model"], "key_hint": hint, "has_key": bool(key)}


def update(base_url: str | None = None, api_key: str | None = None, model: str | None = None) -> None:
    if base_url is not None and base_url.strip():
        _cfg["base_url"] = base_url.strip().rstrip("/")
    if api_key is not None and api_key.strip():
        _cfg["api_key"] = api_key.strip()
    if model is not None and model.strip():
        _cfg["model"] = model.strip()
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(_cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(SETTINGS, 0o600)
    except OSError:
        pass
    _cache.update(at=0.0, value=None)


def quick_ready() -> bool:
    """已知不通就别再等：探测结果缓存里没连通，直接走兜底。"""
    v = _cache["value"]
    if not v:
        return True  # 还没探过，允许试一次
    return bool(v.get("connected"))


def _headers(cfg: dict[str, str] | None = None) -> dict[str, str]:
    c = cfg or _cfg
    return {"Authorization": f"Bearer {c['api_key']}", "Content-Type": "application/json"}


class ChatText(str):
    """保持字符串接口，同时携带网关实际报告的计量信息。"""
    def __new__(cls, content, usage=None, model=""):
        value = super().__new__(cls, content)
        value.usage = usage or {}
        value.model = model
        return value


async def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.6,
    max_tokens: int = 900,
    thinking: bool = False,
) -> str:
    if not enabled():
        raise LLMError("没有配置 API Key")
    payload = {
        "model": model or _cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # 这些模型默认带长思考，把正文挤掉；不需要推理时显式关掉
    if not thinking:
        payload["thinking"] = {"type": "disabled"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(LLM_TIMEOUT, connect=6.0)) as client:
            r = await client.post(f"{_cfg['base_url']}/chat/completions", headers=_headers(), json=payload)
            if r.status_code == 400 and "thinking" in r.text:
                payload.pop("thinking", None)
                r = await client.post(f"{_cfg['base_url']}/chat/completions", headers=_headers(), json=payload)
    except httpx.HTTPError as e:
        raise LLMError(f"网络异常：{type(e).__name__} {e}") from e

    if r.status_code != 200:
        raise LLMError(_brief(r), r.status_code)

    body = r.json()
    choices = body.get("choices") or []
    if not choices:
        raise LLMError("模型没有返回内容")
    msg = choices[0].get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:
        reason = choices[0].get("finish_reason") or "unknown"
        raise LLMError(f"模型没有给出正文（finish_reason={reason}，可能是 max_tokens 被思考过程吃掉了）")
    return ChatText(content, body.get("usage"), body.get("model", payload["model"]))


def _brief(r: httpx.Response) -> str:
    try:
        body = r.json()
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
            if body.get("msg"):
                return str(body["msg"])
        return str(body)[:200]
    except Exception:
        return r.text[:200]


async def _probe(cfg: dict[str, str]) -> dict[str, Any]:
    info: dict[str, Any] = {
        "enabled": bool(cfg["api_key"]),
        "connected": False,
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "models": [],
        "error": "",
    }
    if not cfg["api_key"]:
        info["error"] = "未配置 API Key"
        return info
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(f"{cfg['base_url']}/models", headers=_headers(cfg))
        if r.status_code != 200:
            info["error"] = _brief(r)
        else:
            data = r.json()
            ids = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
            info["models"] = [i for i in ids if i]
            info["connected"] = True
            if info["models"] and cfg["model"] not in info["models"]:
                info["model"] = info["models"][0]
    except httpx.HTTPError as e:
        info["error"] = f"网络异常：{e}"
    return info


async def status(force: bool = False) -> dict[str, Any]:
    now = time.time()
    if not force and _cache["value"] and now - _cache["at"] < CACHE_TTL:
        return {**_cache["value"], **current()}

    info = await _probe(_cfg)
    _cache.update(at=now, value=info)
    return {**info, **current()}


async def test_connection(base_url: str | None = None, api_key: str | None = None, model: str | None = None) -> dict[str, Any]:
    """试连不落盘：界面里点「测试连接」用。密钥留空表示沿用已保存的。"""
    cfg = dict(_cfg)
    if base_url and base_url.strip():
        cfg["base_url"] = base_url.strip().rstrip("/")
    if api_key and api_key.strip():
        cfg["api_key"] = api_key.strip()
    if model and model.strip():
        cfg["model"] = model.strip()
    return await _probe(cfg)
