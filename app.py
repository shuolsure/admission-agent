"""Admission Agent – FastAPI entrypoint.

Endpoints:
- GET  /             landing page
- GET  /health       readiness probe
- POST /chat         synchronous user chat (multi-turn via messages[])
- POST /a2a/dm       EvoMap DM webhook receiver

Background lifespan tasks:
- heartbeat every 5 min, drains pending_events
- on each heartbeat: scan available_tasks, auto-claim & solve relevant ones
- on startup: hello (if creds missing) + publish service listing
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
from capsule import build_bundle, is_in_domain
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

# Track tasks we've already attempted, so a single task isn't claimed/solved
# repeatedly across heartbeats.
_seen_tasks: set[str] = set()
_service_published = False


async def publish_bundle_async(user_msg: str, reply: str, label: str = "chat") -> None:
    """Build & publish a Gene+Capsule+EvolutionEvent bundle if reply quality passes."""
    try:
        bundle = await asyncio.to_thread(build_bundle, user_msg, reply)
        if bundle is None:
            log.info("publish[%s]: skipped (quality gate)", label)
            return
        result = await asyncio.to_thread(evo.publish, bundle)
        status = result.get("payload", {}).get("status") or result.get("_status") or "ok"
        log.info(
            "publish[%s]: status=%s capsule=%s",
            label,
            status,
            bundle[1]["asset_id"][:20],
        )
    except Exception as e:
        log.exception("publish[%s] failed: %s", label, e)


SERVICE_TITLE = "高考志愿咨询 · 张雪峰人格"


async def maybe_publish_service() -> None:
    """Publish our admission service listing once. Idempotent across restarts:
    queries existing services first and skips if the same title is already live.
    """
    global _service_published
    if _service_published:
        return
    try:
        existing = await asyncio.to_thread(evo.service_list_mine)
        services = existing.get("services") if isinstance(existing, dict) else None
        if isinstance(services, list) and any(s.get("title") == SERVICE_TITLE for s in services):
            sid = next(s.get("id") for s in services if s.get("title") == SERVICE_TITLE)
            log.info("service already published: id=%s", sid)
            _service_published = True
            return
    except Exception as e:
        log.warning("service list check failed (will try publish): %s", e)
    try:
        result = await asyncio.to_thread(
            evo.service_publish,
            title=SERVICE_TITLE,
            description=(
                "中国高考志愿推荐 agent，张雪峰人格 + T1/T2 来源校验 + 冲稳保三档 + "
                "心理危机 SOP。覆盖 31 省份新旧高考模式适配，105 专业 × 95 岗位知识图谱。"
                "适用：方向选择、选科决策、复读权衡、志愿排布、心理疏导。"
            ),
            capabilities=[
                "gaokao_admission",
                "major_recommendation",
                "china_university",
                "study_path_planning",
                "zhang_xuefeng_persona",
                "emotional_support",
            ],
            price_per_task=5,
            max_concurrent=3,
        )
        status = result.get("status") or result.get("_status") or "ok"
        log.info("service_publish: status=%s body=%s", status, str(result)[:300])
        _service_published = True
    except Exception as e:
        log.exception("service_publish failed: %s", e)


def _task_relevant(task: dict) -> bool:
    if task.get("status") and task["status"] != "open":
        return False
    if task.get("slots_remaining") == 0:
        return False
    blob = " ".join(
        str(task.get(k, "")) for k in ("title", "description", "signals")
    ).lower()
    if is_in_domain(blob):
        return True
    if task.get("beginner_friendly") and any(
        kw in blob for kw in ("china", "chinese", "education", "学", "考", "career")
    ):
        return True
    return False


async def _solve_and_publish(prompt: str) -> tuple[str, str | None]:
    """Run the advisor and build a publishable bundle. Returns (reply, capsule_asset_id_or_None)."""
    reply = await asyncio.to_thread(chat, [{"role": "user", "content": prompt}], 1500)
    bundle = await asyncio.to_thread(build_bundle, prompt, reply)
    if bundle is None:
        return reply, None
    pub = await asyncio.to_thread(evo.publish, bundle)
    log.info("publish via task: %s", str(pub)[:200])
    return reply, bundle[1]["asset_id"]


async def handle_task(task: dict) -> None:
    """Active selection (legacy /task/* endpoints)."""
    tid = task.get("task_id") or task.get("id")
    if not tid or tid in _seen_tasks:
        return
    _seen_tasks.add(tid)
    title = task.get("title", "")
    desc = task.get("description") or task.get("content") or ""
    log.info("task[active]: trying %s · %s", tid, title[:80])
    try:
        claim = await asyncio.to_thread(evo.task_claim, tid)
        cstatus = claim.get("status") or claim.get("payload", {}).get("status") or claim.get("_status")
        if cstatus and "error" in str(cstatus).lower():
            log.info("task[active]: claim refused %s (%s)", tid, cstatus)
            return
        prompt = (
            f"以下是 EvoMap 平台上一个待处理的志愿咨询任务，请按你的专长给出方案。\n\n"
            f"任务标题: {title}\n任务描述: {desc}\n\n请用张雪峰的语气回答。"
        )
        _, cap_id = await _solve_and_publish(prompt)
        if not cap_id:
            log.info("task[active]: %s skipped (low quality)", tid)
            return
        done = await asyncio.to_thread(evo.task_complete, tid, cap_id)
        log.info("task[active]: completed %s -> %s", tid, str(done)[:200])
    except Exception as e:
        log.exception("task[active] failed for %s: %s", tid, e)


async def handle_work(item: dict) -> None:
    """Worker pool flow: /a2a/work/claim -> accept -> complete."""
    wid = item.get("id") or item.get("task_id")
    if not wid or wid in _seen_tasks:
        return
    _seen_tasks.add(wid)
    title = item.get("title", "")
    signals = item.get("signals", "")
    log.info("work[pool]: trying %s · %s", wid, title[:80])
    try:
        claim = await asyncio.to_thread(evo.work_claim, wid)
        cstatus = claim.get("status") or claim.get("_status")
        if cstatus and "error" in str(cstatus).lower():
            log.info("work[pool]: claim refused %s (%s)", wid, cstatus)
            return
        assignment_id = (
            claim.get("assignment_id")
            or claim.get("assignment", {}).get("id")
            or claim.get("id")
        )
        if assignment_id:
            await asyncio.to_thread(evo.work_accept, assignment_id)
        prompt = (
            f"以下是 EvoMap worker 池派发的志愿咨询任务，请按你的专长给方案。\n\n"
            f"任务标题: {title}\n相关信号: {signals}\n\n请用张雪峰的语气回答。"
        )
        _, cap_id = await _solve_and_publish(prompt)
        if not cap_id or not assignment_id:
            log.info("work[pool]: %s skipped (cap=%s assign=%s)", wid, bool(cap_id), bool(assignment_id))
            return
        done = await asyncio.to_thread(evo.work_complete, assignment_id, cap_id)
        log.info("work[pool]: completed %s -> %s", wid, str(done)[:200])
    except Exception as e:
        log.exception("work[pool] failed for %s: %s", wid, e)


async def heartbeat_loop(interval_ms_default: int = 300_000) -> None:
    interval = interval_ms_default / 1000
    while True:
        try:
            if not evo.node_id or not evo.node_secret:
                log.warning("heartbeat skipped: missing credentials")
                await asyncio.sleep(60)
                continue
            data = await asyncio.to_thread(evo.heartbeat)
            avail_tasks = data.get("available_tasks") or []
            avail_work = data.get("available_work") or []
            pending = data.get("pending_events") or []
            log.info(
                "heartbeat ok survival=%s claimed=%s tasks=%d work=%d events=%d",
                data.get("survival_status"),
                data.get("claimed"),
                len(avail_tasks),
                len(avail_work),
                len(pending),
            )
            next_ms = data.get("next_heartbeat_ms")
            if isinstance(next_ms, (int, float)) and next_ms > 0:
                interval = next_ms / 1000
            for t in avail_tasks:
                if _task_relevant(t):
                    asyncio.create_task(handle_task(t))
            for w in avail_work:
                if _task_relevant(w):
                    asyncio.create_task(handle_work(w))
        except Exception as e:
            log.exception("heartbeat error: %s", e)
        await asyncio.sleep(max(60, min(interval, 600)))


async def maybe_register_worker() -> None:
    """Idempotent worker registration. Sets enabled=true so Hub can match domain tasks."""
    try:
        r = await asyncio.to_thread(
            evo.worker_register,
            enabled=True,
            domains=["education", "chinese_gaokao", "college_admission", "career_consulting"],
            max_load=1,
        )
        log.info(
            "worker register: status=%s enabled=%s load=%s",
            r.get("status"),
            r.get("worker_enabled"),
            r.get("worker_max_load"),
        )
    except Exception as e:
        log.exception("worker_register failed: %s", e)


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
    else:
        # Resume hello to refresh capabilities + url in directory.
        try:
            await asyncio.to_thread(evo.hello)
            log.info("hello (resume): refreshed identity for %s", evo.node_id)
        except Exception as e:
            log.warning("resume hello failed: %s", e)
    asyncio.create_task(maybe_register_worker())
    asyncio.create_task(maybe_publish_service())
    hb_task = asyncio.create_task(heartbeat_loop())
    try:
        yield
    finally:
        hb_task.cancel()


app = FastAPI(title="Admission Agent", version="0.3.0", lifespan=lifespan)


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
<style>body{{font-family:-apple-system,sans-serif;max-width:680px;margin:60px auto;padding:0 20px;color:#222;line-height:1.6}}
code{{background:#f4f4f4;padding:2px 6px;border-radius:4px}}h1{{margin-bottom:8px}}</style></head>
<body><h1>🎓 {name}</h1>
<p>中文高考志愿推荐 agent · 张雪峰人格 · 运行于 EvoMap (GEP-A2A v1.0.0)</p>
<h3>API</h3>
<ul>
<li><code>GET /health</code> — 健康检查 + node 状态</li>
<li><code>POST /chat</code> — body <code>{{messages: [{{role, content}}]}}</code></li>
<li><code>POST /a2a/dm</code> — EvoMap DM 入口</li>
</ul>
<h3>Status</h3>
<p>node_id: <code>{evo.node_id or '(未注册)'}</code></p>
<p>service_published: <code>{_service_published}</code></p>
</body></html>"""


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": app.version,
        "node_id": evo.node_id,
        "node_registered": bool(evo.node_id and evo.node_secret),
        "service_published": _service_published,
        "tasks_seen": len(_seen_tasks),
        "system_prompt_chars": len(system_prompt()),
        "worker_max_load": 1,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest) -> ChatResponse:
    msgs = [{"role": m.role, "content": m.content} for m in req.messages]
    reply = await asyncio.to_thread(chat, msgs, req.max_tokens or 2048)
    last_user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
    asyncio.create_task(publish_bundle_async(last_user, reply, label="chat"))
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
    asyncio.create_task(publish_bundle_async(content, reply, label="dm"))
    return JSONResponse({"status": "ok", "reply_chars": len(reply)})
