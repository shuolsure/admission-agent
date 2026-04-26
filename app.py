"""Admission Agent – FastAPI entrypoint.

- GET  /            landing page
- GET  /health      readiness probe
- POST /chat        synchronous user chat (multi-turn via messages[])
- POST /a2a/dm      EvoMap DM webhook receiver (other agents talk to us)
- Background task: heartbeat every 5 min, drains pending_events.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from advisor import chat, system_prompt
from evomap import EvoMap

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("admission")

evo = EvoMap(
    node_id=os.getenv("EVOMAP_NODE_ID"),
    node_secret=os.getenv("EVOMAP_NODE_SECRET"),
    agent_name=os.getenv("AGENT_NAME", "Admission"),
    model=os.getenv("LONGCAT_MODEL", "LongCat-Flash-Chat"),
    public_url=os.getenv("PUBLIC_URL"),
)


async def heartbeat_loop(interval_ms_default: int = 300_000) -> None:
    interval = interval_ms_default / 1000
    while True:
        try:
            if not evo.node_id or not evo.node_secret:
                log.warning("heartbeat skipped: missing credentials")
                await asyncio.sleep(60)
                continue
            data = await asyncio.to_thread(evo.heartbeat)
            status = data.get("status") or data.get("payload", {}).get("status")
            survival = data.get("survival_status") or data.get("payload", {}).get("survival_status")
            log.info("heartbeat ok status=%s survival=%s", status, survival)
            next_ms = data.get("next_heartbeat_ms")
            if isinstance(next_ms, (int, float)) and next_ms > 0:
                interval = next_ms / 1000
            await _process_events(data.get("pending_events") or [])
        except Exception as e:
            log.exception("heartbeat error: %s", e)
        await asyncio.sleep(max(60, min(interval, 600)))


async def _process_events(events: list[dict]) -> None:
    for ev in events:
        et = ev.get("type") or ev.get("event_type")
        log.info("event received: %s", et)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not evo.node_id or not evo.node_secret:
        log.info("no node creds; calling hello() on startup")
        try:
            data = await asyncio.to_thread(evo.hello)
            p = data.get("payload", {})
            log.info("hello: node_id=%s claim_url=%s", p.get("your_node_id"), p.get("claim_url"))
        except Exception as e:
            log.exception("hello failed: %s", e)
    task = asyncio.create_task(heartbeat_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Admission Agent", version="0.1.0", lifespan=lifespan)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    max_tokens: int | None = 2048


class ChatResponse(BaseModel):
    reply: str


@app.get("/", response_class=HTMLResponse)
def landing() -> str:
    name = os.getenv("AGENT_NAME", "Admission")
    return f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<title>{name} · 高考志愿 Agent</title>
<style>body{{font-family:-apple-system,sans-serif;max-width:680px;margin:60px auto;padding:0 20px;color:#222}}
code{{background:#f4f4f4;padding:2px 6px;border-radius:4px}}h1{{margin-bottom:8px}}</style></head>
<body><h1>{name}</h1>
<p>高考志愿推荐 agent · 张雪峰 思维操作系统 v2.0 · 运行于 EvoMap (GEP-A2A v1.0.0)</p>
<ul>
<li><code>GET /health</code> — 健康检查</li>
<li><code>POST /chat</code> — body <code>{{messages: [...]}}</code></li>
<li><code>POST /a2a/dm</code> — EvoMap DM 入口</li>
</ul>
<p>node_id: <code>{evo.node_id or '(未注册)'}</code></p>
</body></html>"""


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "node_id": evo.node_id,
        "claimed": bool(os.getenv("EVOMAP_OWNER_USER_ID")),
        "system_prompt_chars": len(system_prompt()),
    }


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest) -> ChatResponse:
    msgs = [{"role": m.role, "content": m.content} for m in req.messages]
    reply = await asyncio.to_thread(chat, msgs, req.max_tokens or 2048)
    return ChatResponse(reply=reply)


@app.post("/a2a/dm")
async def dm_inbound(payload: dict[str, Any]) -> JSONResponse:
    sender = payload.get("from") or payload.get("sender_id")
    content = payload.get("content") or payload.get("text") or ""
    log.info("DM from %s: %s", sender, content[:200])
    if not content:
        return JSONResponse({"status": "ignored", "reason": "empty"})
    reply = await asyncio.to_thread(chat, [{"role": "user", "content": content}], 1500)
    if sender:
        try:
            await asyncio.to_thread(evo.dm_send, sender, reply)
        except Exception as e:
            log.exception("dm reply failed: %s", e)
    return JSONResponse({"status": "ok", "reply_chars": len(reply)})
