"""Advisor: build system prompt and call LongCat (Anthropic-compatible)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from anthropic import Anthropic

ROOT = Path(__file__).resolve().parent
SOUL_FILE = ROOT / "soul.md"
SYSTEM_FILE = ROOT / "prompts" / "system.md"
KG_FILE = ROOT / "data" / "kg.md"

LONGCAT_BASE = os.getenv("LONGCAT_BASE_URL", "https://api.longcat.chat/anthropic/")
LONGCAT_MODEL = os.getenv("LONGCAT_MODEL", "LongCat-Flash-Chat")


@lru_cache(maxsize=1)
def system_prompt() -> str:
    parts: list[str] = []
    if SOUL_FILE.exists():
        parts.append(SOUL_FILE.read_text(encoding="utf-8"))
    parts.append(SYSTEM_FILE.read_text(encoding="utf-8"))
    if KG_FILE.exists():
        parts.append("\n---\n# 内置知识图谱（专业 ↔ 能力 ↔ 岗位）\n")
        parts.append(
            "使用提示：当用户问'XX 专业适合谁/能干嘛'或要做方向匹配时，先查这里。"
            "录取分数线 / 院校排名 / 实时就业数据不在此图内，按 Step 2 数据源规则现查。\n\n"
        )
        parts.append(KG_FILE.read_text(encoding="utf-8"))
    return "\n".join(parts)


@lru_cache(maxsize=1)
def get_client() -> Anthropic:
    api_key = os.getenv("LONGCAT_API_KEY")
    if not api_key:
        raise RuntimeError("LONGCAT_API_KEY env var is required")
    return Anthropic(
        api_key=api_key,
        base_url=LONGCAT_BASE,
        default_headers={"Authorization": f"Bearer {api_key}", "x-api-key": ""},
    )


def chat(messages: list[dict], max_tokens: int = 2048) -> str:
    """messages: [{role: 'user'|'assistant', content: str}, ...]"""
    client = get_client()
    resp = client.messages.create(
        model=LONGCAT_MODEL,
        max_tokens=max_tokens,
        system=system_prompt(),
        messages=messages,
    )
    blocks = getattr(resp, "content", []) or []
    out: list[str] = []
    for b in blocks:
        text = getattr(b, "text", None)
        if text:
            out.append(text)
    return "\n".join(out).strip()


def one_shot(user_message: str, max_tokens: int = 2048) -> str:
    return chat([{"role": "user", "content": user_message}], max_tokens=max_tokens)
