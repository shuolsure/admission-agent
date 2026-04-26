"""Build a compact knowledge graph from word-convergence-engine data.

Reads mvp_dataset.json + all phase_*.json from the source directory,
deduplicates nodes by id, keeps top key implicit words per node, and
writes data/kg.json suitable for direct system-prompt injection.

Run once at build time (or on data refresh):
    python scripts/build_kg.py [SRC_DIR] [DST_FILE]
"""
import glob
import json
import os
import sys

DEFAULT_SRC = "/Users/shuo/Documents/Claude/daxue/02-project/Admission Agent/word-convergence-engine-200nodes/data"
DEFAULT_DST = os.path.join(os.path.dirname(__file__), "..", "data", "kg.md")
TOP_KEY_WORDS = 5


def load_nodes(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return data.get("nodes", []) if isinstance(data, dict) else []


def compact_node(n: dict) -> dict:
    words = n.get("implicitWords", []) or []
    key_words = [w for w in words if w.get("isKeyTag")]
    if len(key_words) < TOP_KEY_WORDS:
        seen = {w["word"] for w in key_words}
        for w in sorted(words, key=lambda x: -x.get("weight", 0)):
            if w["word"] not in seen:
                key_words.append(w)
                seen.add(w["word"])
            if len(key_words) >= TOP_KEY_WORDS:
                break
    return {
        "id": n["id"],
        "name": n["name"],
        "category": n.get("category", ""),
        "level1": n.get("categoryLevel1", ""),
        "level2": n.get("categoryLevel2", ""),
        "desc": n.get("description", ""),
        "tags": [
            {"w": w["word"], "d": w.get("dimension", ""), "s": round(w.get("weight", 0), 2)}
            for w in key_words[:TOP_KEY_WORDS]
        ],
    }


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC
    dst = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DST
    src_files = sorted(glob.glob(os.path.join(src, "*.json")))

    by_id: dict[str, dict] = {}
    for f in src_files:
        nodes = load_nodes(f)
        for n in nodes:
            if not isinstance(n, dict) or "id" not in n:
                continue
            if n["id"] not in by_id:
                by_id[n["id"]] = compact_node(n)

    majors = sorted(
        [n for n in by_id.values() if n["category"] == "专业"],
        key=lambda x: (x["level1"], x["level2"], x["name"]),
    )
    jobs = sorted(
        [n for n in by_id.values() if n["category"] == "职位"],
        key=lambda x: x["name"],
    )

    def render(node: dict) -> str:
        tags = " · ".join(f"{t['w']}({t['d']})" for t in node["tags"])
        path = "/".join(p for p in [node["level1"], node["level2"]] if p)
        return f"- **{node['name']}** [{path}]: {tags}"

    lines = [
        "# Admission KG (compact)",
        f"> {len(majors)} 个专业 · {len(jobs)} 个岗位 · 每条最多 {TOP_KEY_WORDS} 个关键标签",
        "",
        "## 专业 (按学科大类排序)",
        *(render(n) for n in majors),
        "",
        "## 岗位",
        *(render(n) for n in jobs),
    ]
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    size_kb = os.path.getsize(dst) / 1024
    print(f"wrote {dst}: {len(majors)} majors + {len(jobs)} jobs, {size_kb:.1f} KB")


if __name__ == "__main__":
    main()
