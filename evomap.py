"""EvoMap GEP-A2A v1.0.0 client (minimal).

Wraps hello / heartbeat / publish / fetch / dm / task endpoints with
the protocol envelope, Authorization header, and basic retry policy.
Designed to run inside a long-lived FastAPI process (Render/Railway).
"""
from __future__ import annotations

import json
import os
import secrets
import time
from typing import Any

import httpx

HUB = os.getenv("EVOMAP_HUB", "https://evomap.ai")
PROTOCOL = "gep-a2a"
PROTOCOL_VERSION = "1.0.0"


def _envelope(message_type: str, payload: dict, sender_id: str | None) -> dict:
    env: dict[str, Any] = {
        "protocol": PROTOCOL,
        "protocol_version": PROTOCOL_VERSION,
        "message_type": message_type,
        "message_id": f"msg_{int(time.time() * 1000)}_{secrets.token_hex(4)}",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "payload": payload,
    }
    if sender_id:
        env["sender_id"] = sender_id
    return env


class EvoMap:
    def __init__(
        self,
        node_id: str | None = None,
        node_secret: str | None = None,
        agent_name: str = "Admission",
        model: str = "longcat-flash-chat",
        public_url: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.node_id = node_id or os.getenv("EVOMAP_NODE_ID")
        self.node_secret = node_secret or os.getenv("EVOMAP_NODE_SECRET")
        self.agent_name = agent_name
        self.model = model
        self.public_url = public_url or os.getenv("PUBLIC_URL")
        self._client = httpx.Client(timeout=timeout_s)

    def _post(self, path: str, body: dict, auth: bool = True) -> dict:
        headers = {"Content-Type": "application/json", "User-Agent": f"{self.agent_name}/0.1"}
        if auth and self.node_secret:
            headers["Authorization"] = f"Bearer {self.node_secret}"
        url = f"{HUB}{path}"
        resp = self._client.post(url, headers=headers, content=json.dumps(body))
        try:
            data = resp.json()
        except Exception:
            data = {"_raw": resp.text}
        if resp.status_code >= 400:
            data["_status"] = resp.status_code
        return data

    def _get(self, path: str, params: dict | None = None, auth: bool = True) -> dict:
        headers = {"User-Agent": f"{self.agent_name}/0.1"}
        if auth and self.node_secret:
            headers["Authorization"] = f"Bearer {self.node_secret}"
        url = f"{HUB}{path}"
        resp = self._client.get(url, headers=headers, params=params or {})
        try:
            data = resp.json()
        except Exception:
            data = {"_raw": resp.text}
        if resp.status_code >= 400:
            data["_status"] = resp.status_code
        return data

    # ---- Module 1+2: hello (register or resume) ----
    def hello(self, capabilities: dict | None = None) -> dict:
        env_fp = {
            "platform": os.getenv("EVOMAP_PLATFORM", "render"),
            "arch": "x64",
            "service": "admission-agent",
            "owner": os.getenv("EVOMAP_OWNER", "shuo"),
        }
        # EvoMap directory indexes capabilities as flat boolean keys
        # (see Muse's profile: { code_review: true, debugging: true, ... }),
        # not as nested {signals:[], languages:[], domains:[]} arrays.
        payload = {
            "capabilities": capabilities
            or {
                "gaokao_admission": True,
                "major_recommendation": True,
                "china_university": True,
                "study_path_planning": True,
                "zhang_xuefeng_persona": True,
                "emotional_support": True,
                "chinese_education": True,
                "college_admission_consulting": True,
                "subject_selection": True,
                "career_path_planning": True,
            },
            "model": self.model,
            "name": self.agent_name,
            "description": "Chinese gaokao admission advisor in 张雪峰 persona. T1/T2 source-cited recommendations with chong/wen/bao tier.",
            "env_fingerprint": env_fp,
        }
        if self.public_url:
            payload["url"] = self.public_url
        body = _envelope("hello", payload, sender_id=self.node_id)
        data = self._post("/a2a/hello", body, auth=bool(self.node_secret))
        p = data.get("payload", {})
        if p.get("your_node_id"):
            self.node_id = p["your_node_id"]
        if p.get("node_secret"):
            self.node_secret = p["node_secret"]
        return data

    # ---- Module 4: heartbeat (REST, no envelope) ----
    def heartbeat(self) -> dict:
        if not self.node_id or not self.node_secret:
            raise RuntimeError("heartbeat requires node_id + node_secret")
        body = {"node_id": self.node_id}
        return self._post("/a2a/heartbeat", body, auth=True)

    # ---- Module 6: publish bundle ----
    def publish(self, assets: list[dict]) -> dict:
        body = _envelope("publish", {"assets": assets}, sender_id=self.node_id)
        return self._post("/a2a/publish", body, auth=True)

    def validate(self, assets: list[dict]) -> dict:
        body = _envelope("validate", {"assets": assets}, sender_id=self.node_id)
        return self._post("/a2a/validate", body, auth=True)

    # ---- Service marketplace (REST, no envelope) ----
    def service_publish(
        self,
        title: str,
        description: str,
        capabilities: list[str],
        price_per_task: int = 5,
        max_concurrent: int = 3,
    ) -> dict:
        body = {
            "sender_id": self.node_id,
            "title": title,
            "description": description,
            "capabilities": capabilities,
            "price_per_task": price_per_task,
            "max_concurrent": max_concurrent,
        }
        return self._post("/a2a/service/publish", body, auth=True)

    def service_list_mine(self) -> dict:
        return self._get("/a2a/service/list", params={"node_id": self.node_id}, auth=True)

    # ---- Module 7: fetch promoted assets ----
    def fetch(self, asset_type: str = "Capsule", include_tasks: bool = True) -> dict:
        body = _envelope(
            "fetch",
            {"asset_type": asset_type, "include_tasks": include_tasks},
            sender_id=self.node_id,
        )
        return self._post("/a2a/fetch", body, auth=True)

    # ---- DM ----
    def dm_send(self, to_node_id: str, content: str) -> dict:
        body = {"to": to_node_id, "from": self.node_id, "content": content}
        return self._post("/a2a/dm", body, auth=True)

    # ---- Tasks ----
    def task_list(self) -> dict:
        return self._get("/task/list", auth=True)

    def task_claim(self, task_id: str) -> dict:
        body = {"task_id": task_id, "node_id": self.node_id}
        return self._post("/task/claim", body, auth=True)

    def task_complete(self, task_id: str, asset_id: str) -> dict:
        body = {"task_id": task_id, "asset_id": asset_id, "node_id": self.node_id}
        return self._post("/task/complete", body, auth=True)

    # ---- Evolution Memory (per-node experience log) ----
    def memory_record(
        self,
        signals: list[str],
        status: str,
        score: float | None = None,
        context: str | None = None,
        gene: str | None = None,
    ) -> dict:
        if status not in ("success", "failed"):
            raise ValueError("status must be 'success' or 'failed'")
        body: dict = {
            "sender_id": self.node_id,
            "signals": signals,
            "status": status,
        }
        if score is not None:
            body["score"] = score
        if context:
            body["context"] = context[:1500]
        if gene:
            body["gene"] = gene
        return self._post("/a2a/memory/record", body, auth=True)

    def memory_recall(
        self,
        signals: list[str] | None = None,
        query: str | None = None,
        limit: int = 5,
    ) -> dict:
        body: dict = {"sender_id": self.node_id, "limit": limit}
        if signals:
            body["signals"] = signals
        if query:
            body["query"] = query
        return self._post("/a2a/memory/recall", body, auth=True)

    def memory_status(self) -> dict:
        return self._get("/a2a/memory/status", params={"node_id": self.node_id}, auth=True)

    # ---- Worker pool (passive task assignment) ----
    def worker_register(
        self,
        enabled: bool = True,
        domains: list[str] | None = None,
        max_load: int = 1,
    ) -> dict:
        body = {
            "sender_id": self.node_id,
            "enabled": enabled,
            "domains": domains
            or [
                "education",
                "chinese_gaokao",
                "college_admission",
                "career_consulting",
            ],
            "max_load": max_load,
        }
        return self._post("/a2a/worker/register", body, auth=True)

    def work_available(self) -> dict:
        return self._get("/a2a/work/available", params={"node_id": self.node_id}, auth=True)

    def work_claim(self, task_id: str) -> dict:
        body = {"sender_id": self.node_id, "task_id": task_id}
        return self._post("/a2a/work/claim", body, auth=True)

    def work_accept(self, assignment_id: str) -> dict:
        body = {"sender_id": self.node_id, "assignment_id": assignment_id}
        return self._post("/a2a/work/accept", body, auth=True)

    def work_complete(self, assignment_id: str, result_asset_id: str) -> dict:
        body = {
            "sender_id": self.node_id,
            "assignment_id": assignment_id,
            "result_asset_id": result_asset_id,
        }
        return self._post("/a2a/work/complete", body, auth=True)

    # ---- Session (collaboration) ----
    def session_message(self, session_id: str, content: str) -> dict:
        body = {"session_id": session_id, "node_id": self.node_id, "content": content}
        return self._post("/a2a/session/message", body, auth=True)


def credentials_from_env() -> tuple[str | None, str | None]:
    return os.getenv("EVOMAP_NODE_ID"), os.getenv("EVOMAP_NODE_SECRET")
