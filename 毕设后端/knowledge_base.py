"""独立工业诊断知识库的加载、切块与本地检索。"""
import hashlib
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path


KNOWLEDGE_DIR = Path(__file__).with_name("knowledge")
KNOWLEDGE_VERSION = "industrial-kb-v1"

QUERY_ALIASES = {
    "oee": ["设备综合效率", "开动率", "性能", "质量"],
    "开动率": ["availability", "停机", "可用性"],
    "性能": ["performance", "节拍", "速度", "空转"],
    "质量": ["quality", "次品", "良品率", "批次"],
    "cnc": ["数控机床", "主轴", "进给", "刀具"],
    "机器人": ["robot", "抓取", "示教", "上下料"],
    "plc": ["控制器", "cpu", "通信", "联锁"],
    "opcua": ["opc ua", "采集", "nodeid", "会话"],
    "数据质量": ["缺失", "空档", "重叠", "计数器", "覆盖"],
    "根因": ["诊断", "证据", "假设", "排查"],
}


def _normalize(value):
    return re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff]+", "", str(value)).lower()


def _bigrams(value):
    value = _normalize(value)
    return Counter(value[i:i + 2] for i in range(max(0, len(value) - 1)))


def _parse_frontmatter(text, path):
    if not text.startswith("---\n"):
        raise ValueError(f"知识文档缺少 frontmatter: {path.name}")
    _, raw_meta, body = text.split("---", 2)
    meta = {}
    for line in raw_meta.strip().splitlines():
        key, separator, value = line.partition(":")
        if separator:
            meta[key.strip()] = value.strip()
    required = {"id", "title", "version", "scope", "keywords", "authority", "reviewed_at"}
    if not required.issubset(meta):
        raise ValueError(f"知识文档元数据不完整: {path.name}")
    return meta, body.strip()


def _split_sections(meta, body, path):
    chunks = []
    current_title = "概述"
    current_lines = []

    def flush():
        content = "\n".join(current_lines).strip()
        if content:
            chunks.append({
                "id": f"{meta['id']}#{len(chunks) + 1}",
                "document_id": meta["id"], "title": meta["title"],
                "section": current_title, "version": meta["version"],
                "scope": meta["scope"], "authority": meta["authority"],
                "reviewed_at": meta["reviewed_at"],
                "content_hash": meta["content_hash"],
                "keywords": [item.strip() for item in meta["keywords"].split(",") if item.strip()],
                "content": content, "source_path": f"knowledge/{path.name}",
            })

    for line in body.splitlines():
        if line.startswith("## "):
            flush()
            current_title = line[3:].strip()
            current_lines = []
        elif not line.startswith("# "):
            current_lines.append(line)
    flush()
    return chunks


def _knowledge_signature():
    return tuple((path.name, path.stat().st_mtime_ns, path.stat().st_size)
                 for path in sorted(KNOWLEDGE_DIR.glob("*.md")))


def knowledge_revision():
    digest = hashlib.sha256()
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


@lru_cache(maxsize=4)
def _load_knowledge_chunks(signature):
    chunks = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw, path)
        meta["content_hash"] = hashlib.sha256(raw.encode()).hexdigest()[:16]
        chunks.extend(_split_sections(meta, body, path))
    if not chunks:
        raise ValueError("诊断知识库为空")
    return chunks


def load_knowledge_chunks():
    """知识文件发生变化时自动切换到新缓存，无需重启进程。"""
    return _load_knowledge_chunks(_knowledge_signature())


def _expanded_query(query):
    normalized = _normalize(query)
    additions = []
    for key, aliases in QUERY_ALIASES.items():
        if _normalize(key) in normalized or any(_normalize(alias) in normalized for alias in aliases):
            additions.extend([key, *aliases])
    return query + " " + " ".join(additions)


def _score(query, chunk):
    original = _normalize(query)
    expanded = _expanded_query(query)
    searchable = " ".join([chunk["title"], chunk["section"], *chunk["keywords"], chunk["content"]])
    normalized_searchable = _normalize(searchable)
    score = 0.0
    matched = []
    for keyword in chunk["keywords"]:
        token = _normalize(keyword)
        if token and token in original:
            score += 8.0
            matched.append(keyword)
    for token in re.findall(r"[a-zA-Z_]+|[\u4e00-\u9fff]{2,}", query):
        normalized_token = _normalize(token)
        if len(normalized_token) >= 2 and normalized_token in normalized_searchable:
            score += 3.0
    query_pairs = _bigrams(expanded)
    chunk_pairs = _bigrams(searchable)
    score += sum(min(count, chunk_pairs.get(pair, 0)) for pair, count in query_pairs.items()) * 0.28
    section_pairs = _bigrams(chunk["section"])
    original_pairs = _bigrams(query)
    score += sum(min(count, section_pairs.get(pair, 0)) for pair, count in original_pairs.items()) * 1.2
    return score, matched


def search_knowledge(query, top_k=3):
    """检索独立的项目知识库；知识文档与论文、数据库分开维护。"""
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ValueError("query 必须为 1–1000 字符的非空文本")
    if type(top_k) is not int or not 1 <= top_k <= 5:
        raise ValueError("top_k 必须是 1–5 的整数")
    ranked = []
    for chunk in load_knowledge_chunks():
        score, matched = _score(query, chunk)
        if score >= 1.0:
            ranked.append((score, matched, chunk))
    ranked.sort(key=lambda item: (-item[0], item[2]["id"]))
    matches = []
    for score, matched, chunk in ranked[:top_k]:
        matches.append({
            **{key: chunk[key] for key in ("id", "document_id", "title", "section", "version", "scope", "authority", "reviewed_at", "content_hash", "content", "source_path")},
            "matched_keywords": matched, "relevance_score": round(score, 3),
            "citation": f"[知识库 {chunk['document_id']} § {chunk['section']}]",
        })
    return {
        "status": "ok" if matches else "no_match", "query": query,
        "knowledge_base": {"version": KNOWLEDGE_VERSION,
                           "content_revision": knowledge_revision(),
                           "document_count": len(list(KNOWLEDGE_DIR.glob('*.md'))),
                           "chunk_count": len(load_knowledge_chunks()), "mode": "read_only"},
        "matches": matches,
        "limitations": "这是项目自建的通用诊断知识库，不是设备厂商维修手册。内容用于形成排查假设，物理根因仍需现场信号、报警历史和厂商资料确认。",
    }
