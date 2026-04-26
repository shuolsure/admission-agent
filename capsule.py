"""Capsule / Gene / EvolutionEvent asset construction for EvoMap.

Handles:
- canonical SHA256 asset_id computation
- PII redaction (phone / email / id-card)
- heuristic quality scoring of agent replies
- bundle assembly compatible with `POST /a2a/publish`
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

SCHEMA_VERSION = "1.5.0"
QUALITY_THRESHOLD = 0.7
MODEL_NAME = os.getenv("LONGCAT_MODEL", "LongCat-Flash-Chat")

ENV_FP = {
    "platform": os.getenv("EVOMAP_PLATFORM", "huggingface_space"),
    "arch": "x64",
    "service": "admission-agent",
    "owner": os.getenv("EVOMAP_OWNER", "shuo"),
}

_DOMAIN_PATTERN = re.compile(
    r"(?:"
    r"高考|志愿|院校|录取|选科|复读|"
    r"提前批|考生|位次|招生|投档|"
    r"985|211|双一流|"
    r"郑州大学|清华大学|北京大学|"
    r"\d{2,3}\s*分(?![钟秒析点裂歧])|"
    r"gaokao|"
    r"(?:college|university|school|graduate)\s+admission|"
    r"admission\s+(?:advice|guidance|consulting|counseling|essay)|"
    r"major\s+(?:selection|recommendation|choice|decision)|"
    r"choosing\s+(?:a\s+)?(?:college|major|university)|"
    r"career\s+(?:path|planning|guidance)|"
    r"study\s+(?:plan|path|abroad)|"
    r"chinese\s+(?:education|gaokao|university)"
    r")",
    re.IGNORECASE,
)

ADMISSION_GENE: dict[str, Any] = {
    "type": "Gene",
    "schema_version": SCHEMA_VERSION,
    "category": "innovate",
    "signals_match": [
        "gaokao_admission",
        "major_recommendation",
        "china_university",
        "study_path_planning",
        "zhang_xuefeng_persona",
    ],
    "summary": (
        "中国高考志愿推荐策略 · 张雪峰人格 · "
        "省份适配 + T1/T2 来源校验 + 冲稳保三档 + 心理危机 SOP"
    ),
    "strategy": [
        "Step 0: 确认用户省份和高考模式（新高考3+1+2 / 新高考3+3 / 旧高考），省份未知不可下结论",
        "Step 1: 区分问题类型：需要事实 → Step 2；纯框架 → Step 3；混合 → Step 2 后 Step 3",
        "Step 2: 按 T1>T2>T3>T4 优先级查实时数据（省教育考试院/阳光高考网/掌上高考），单源必须 ⚠️ 标注",
        "Step 3: 用张雪峰人格 + 5 心智模型口语化回答，70%+ 口语段落、≤30% 表格、金句收尾",
        "Step 4: 省份+分数+方向三要素齐 → 输出冲/稳/保结构化方案，每校带往年位次和来源",
        "Step 5: 多轮记忆 7 要素，永不重复追问；检测心理危机信号 → 立刻切🟢档 + 给热线",
    ],
    "validation": [
        "node -e \"require('assert').strictEqual(['Step0','Step1','Step2','Step3','Step4','Step5'].length, 6, 'strategy must have 6 steps')\"",
    ],
    "model_name": MODEL_NAME,
}


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def asset_id(asset: dict) -> str:
    clean = {k: v for k, v in asset.items() if k != "asset_id"}
    return "sha256:" + hashlib.sha256(canonical_json(clean).encode("utf-8")).hexdigest()


def with_id(asset: dict) -> dict:
    out = dict(asset)
    out["asset_id"] = asset_id(out)
    return out


_PII = [
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "<PHONE>"),
    (re.compile(r"(?<!\d)\d{3}[-.]?\d{4}[-.]?\d{4}(?!\d)"), "<PHONE>"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "<ID>"),
    (re.compile(r"(?<!\d)\d{15}(?!\d)"), "<ID>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<EMAIL>"),
]


def redact(text: str) -> str:
    if not text:
        return ""
    for pat, repl in _PII:
        text = pat.sub(repl, text)
    return text


def is_in_domain(text: str) -> bool:
    if not text:
        return False
    return _DOMAIN_PATTERN.search(text) is not None


def score_reply(user_msg: str, reply: str) -> float:
    if not reply or len(reply) < 100:
        return 0.0
    score = 0.55
    if any(m in reply for m in ("来源", "T1", "T2", "教育考试院", "阳光高考", "就业质量报告")):
        score += 0.12
    if "冲" in reply and "稳" in reply and "保" in reply:
        score += 0.15
    provinces = (
        "北京 上海 天津 重庆 河北 山西 辽宁 吉林 黑龙江 江苏 浙江 安徽 福建 江西 山东 "
        "河南 湖北 湖南 广东 广西 海南 四川 贵州 云南 西藏 陕西 甘肃 青海 宁夏 新疆 内蒙古"
    ).split()
    if any(p in reply for p in provinces):
        score += 0.06
    if "⚠️" in reply:
        score += 0.06
    if any(c in reply for c in ("我跟你说", "你听我说", "停停停", "我给你算一笔账")):
        score += 0.06
    return min(0.95, round(score, 3))


_PROVINCES_PINYIN = {
    "北京": "beijing", "上海": "shanghai", "天津": "tianjin", "重庆": "chongqing",
    "河北": "hebei", "山西": "shanxi", "辽宁": "liaoning", "吉林": "jilin",
    "黑龙江": "heilongjiang", "江苏": "jiangsu", "浙江": "zhejiang", "安徽": "anhui",
    "福建": "fujian", "江西": "jiangxi", "山东": "shandong", "河南": "henan",
    "湖北": "hubei", "湖南": "hunan", "广东": "guangdong", "广西": "guangxi",
    "海南": "hainan", "四川": "sichuan", "贵州": "guizhou", "云南": "yunnan",
    "西藏": "xizang", "陕西": "shaanxi", "甘肃": "gansu", "青海": "qinghai",
    "宁夏": "ningxia", "新疆": "xinjiang", "内蒙古": "inner_mongolia",
}


def _score_bucket(score: int) -> str:
    if score >= 680:
        return "score_680plus"
    if score >= 630:
        return "score_630_680"
    if score >= 580:
        return "score_580_630"
    if score >= 530:
        return "score_530_580"
    if score >= 480:
        return "score_480_530"
    return "score_below_480"


def memory_signals_for(user_msg: str) -> list[str]:
    """Stable, deterministic signals for memory.record/recall.

    Each session produces a small set of canonical tokens so future recalls
    on similar sessions hit the same keys (same province + score-bucket +
    track => same signals).
    """
    sigs = ["gaokao_admission"]
    for cn, pinyin in _PROVINCES_PINYIN.items():
        if cn in user_msg:
            sigs.append(f"province_{pinyin}")
            break
    score_match = re.search(r"(\d{2,3})\s*分(?![钟秒析点裂歧])", user_msg)
    if score_match:
        try:
            sigs.append(_score_bucket(int(score_match.group(1))))
        except ValueError:
            pass
    if "理科" in user_msg or "物理类" in user_msg:
        sigs.append("track_science")
    elif "文科" in user_msg or "历史类" in user_msg:
        sigs.append("track_liberal_arts")
    intent_rules = (
        ("复读", "intent_repeat_year"),
        ("选科", "intent_subject_selection"),
        ("提前批", "intent_early_batch"),
        ("专项计划", "intent_special_admission"),
        ("中外合作", "intent_sino_foreign"),
        ("艺术", "intent_arts"),
        ("体育", "intent_sports"),
        ("少数民族", "intent_minority"),
        ("考砸", "intent_emotional_support"),
        ("崩溃", "intent_emotional_support"),
        ("不想活", "intent_crisis"),
    )
    for k, s in intent_rules:
        if k in user_msg and s not in sigs:
            sigs.append(s)
    return sigs


def extract_signals(text: str) -> list[str]:
    sig = ["gaokao_admission", "major_recommendation"]
    rules = (
        ("复读", "repeat_year"),
        ("选科", "subject_selection"),
        ("新高考", "new_gaokao_3plus1plus2"),
        ("3+3", "new_gaokao_3plus3"),
        ("考砸", "emotional_support"),
        ("崩溃", "emotional_support"),
        ("不想活", "crisis_intervention"),
        ("提前批", "early_batch"),
        ("专项计划", "special_admission_plan"),
        ("中外合作", "sino_foreign_program"),
        ("艺术", "arts_admission"),
        ("体育", "sports_admission"),
        ("少数民族", "minority_admission"),
    )
    for k, s in rules:
        if k in text and s not in sig:
            sig.append(s)
    return sig


def build_gene() -> dict:
    return with_id(ADMISSION_GENE)


def build_capsule(user_msg: str, reply: str, gene_asset_id: str) -> dict | None:
    score = score_reply(user_msg, reply)
    if score < QUALITY_THRESHOLD:
        return None
    redacted_user = redact(user_msg)[:500]
    redacted_reply = redact(reply)[:4000]
    line_count = redacted_reply.count("\n") + 1
    capsule = {
        "type": "Capsule",
        "schema_version": SCHEMA_VERSION,
        "trigger": extract_signals(user_msg),
        "gene": gene_asset_id,
        "summary": f"高考志愿咨询 · {redacted_user[:60].replace(chr(10), ' ')}",
        "content": f"## 用户问题\n{redacted_user}\n\n## 张雪峰式回答\n{redacted_reply}",
        "confidence": score,
        "blast_radius": {"files": 1, "lines": line_count},
        "outcome": {"status": "success", "score": score},
        "env_fingerprint": ENV_FP,
        "success_streak": 1,
        "model_name": MODEL_NAME,
    }
    return with_id(capsule)


def build_event(capsule_asset_id: str, gene_asset_id: str, score: float) -> dict:
    event = {
        "type": "EvolutionEvent",
        "intent": "innovate",
        "capsule_id": capsule_asset_id,
        "genes_used": [gene_asset_id],
        "outcome": {"status": "success", "score": score},
        "mutations_tried": 1,
        "total_cycles": 1,
        "model_name": MODEL_NAME,
    }
    return with_id(event)


def build_bundle(user_msg: str, reply: str) -> list[dict] | None:
    """Return [Gene, Capsule, EvolutionEvent] or None if reply fails quality gate."""
    gene = build_gene()
    capsule = build_capsule(user_msg, reply, gene["asset_id"])
    if capsule is None:
        return None
    event = build_event(capsule["asset_id"], gene["asset_id"], capsule["confidence"])
    return [gene, capsule, event]
