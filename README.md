---
title: Admission
emoji: 🎓
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: 高考志愿推荐 Agent · 张雪峰人格 · EvoMap GEP-A2A
---

# Admission · 高考志愿推荐 Agent

EvoMap 平台上的中文高考志愿推荐 agent，张雪峰人格 + 词群收敛知识图谱（105 专业 × 95 岗位）。

- **协议**: [GEP-A2A v1.0.0](https://evomap.ai/skill.md)
- **LLM**: LongCat-Flash-Chat（Anthropic-compatible）
- **runtime**: FastAPI + asyncio heartbeat（部署到 Render Web Service Starter）

## 一分钟跑起来

```bash
pip install -r requirements.txt
cp .env.example .env  # 填 LONGCAT_API_KEY；如已有 node_id 也填
python scripts/build_kg.py  # 一次性构建 data/kg.md
uvicorn app:app --reload  # http://127.0.0.1:8000
```

首次启动如未配置 `EVOMAP_NODE_ID` → 自动调用 `POST /a2a/hello` 注册，日志里会打印 `claim_url`，访问该 URL 在 24h 内绑定到你 EvoMap 账号。

## 部署到 Hugging Face Spaces（免费、无需信用卡）

1. 注册 [huggingface.co](https://huggingface.co)（GitHub OAuth 一键登录，不要 CC）
2. New Space → SDK 选 **Docker** → Hardware **CPU basic (free)** → 命名 `admission`
3. 在 Space → Settings → **Variables and secrets**，加 3 个 secret：
   - `LONGCAT_API_KEY`
   - `EVOMAP_NODE_ID`
   - `EVOMAP_NODE_SECRET`
4. 在 Space 页面右上 `…` → **Embed this Space** 拿到 git URL，然后本地：
   ```bash
   git remote add hf https://huggingface.co/spaces/<你的-hf-用户名>/admission
   git push hf main
   ```
5. HF 自动构建 Dockerfile → 几分钟后访问 `https://<hf-user>-admission.hf.space/health` 验证
6. **保活**：去 [uptimerobot.com](https://uptimerobot.com) 注册（免费 50 个监控、不要 CC）→ 加一个 HTTP 监控指向 `https://<hf-user>-admission.hf.space/health`，5 分钟一次。Space 默认 48h 无访问休眠，UptimeRobot 帮你常驻。

## 部署到 Render

1. push 这个 repo 到 GitHub
2. Render → New → Web Service → Connect Repo
3. 用本仓库自带的 `render.yaml`（自动识别）
4. 在 Render Dashboard 配 3 个 secret：
   - `LONGCAT_API_KEY` = `ak_xxx`
   - `EVOMAP_NODE_ID` = `node_xxx`（首次本地启动时拿到）
   - `EVOMAP_NODE_SECRET` = `<64_hex>`
5. 等部署 → 访问 `https://<your-app>.onrender.com/health` 看到 `{status: "ok"}` 即成功

> Starter plan ($7/mo) 才能 always-on，免费档 15 分钟无访问会 sleep 断 heartbeat。

## 端点

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/` | 落地页 |
| GET | `/health` | 健康检查 / 看 node_id |
| POST | `/chat` | `{messages: [{role, content}, ...]}` 直接对话 |
| POST | `/a2a/dm` | 接收其他 EvoMap agent 的 DM 并回复 |

## 文件结构

```
admission-agent/
├── app.py              # FastAPI + 后台 heartbeat
├── advisor.py          # 组装 system prompt + 调 LongCat
├── evomap.py           # GEP-A2A 客户端封装
├── soul.md             # 灵魂/红线（最高优先级）
├── prompts/system.md   # 张雪峰人格 + 工作流压缩版
├── data/kg.md          # 词群收敛知识图谱（构建产物）
├── scripts/build_kg.py # 从 word-convergence 数据生成 kg.md
├── manifest.yaml       # 身份卡 + EvoMap capabilities
├── render.yaml         # Render 一键部署
└── requirements.txt
```

## 数据来源

- **人格 / 工作流**: 基于 [`ZhangXueFeng-skill v2.0`](https://github.com/) 的压缩版（保留人格、Step 0-5、🔴🟡🟢三档、5 心智模型、来源标注规则）
- **知识图谱**: [`word-convergence-engine-200nodes`](../) 的 mvp_dataset + 17 phase 文件合并去重，每节点保留 5 个关键标签

## 三条红线（高于人格）

1. 数据校验：任何分数线 / 位次 / 薪资必须 ≥2 个 T1/T2 来源；不可编造
2. 心理危机：检测自伤 / 极端崩溃信号 → 立即切🟢档 + 给 4 路心理热线
3. 省份未知不下结论：必须先确认省份和高考模式

## License

仅用于学习和参赛。张雪峰人格调研基于公开信息提炼，非本人观点。
