"""检索：frontmatter 结构化信息 + BM25 全文（中文按字与二元组切分）。

M0 不引入向量库；结果附 path@commit 与原始链接，供 LLM 阅读后作答并引用。
"""

from __future__ import annotations

import math
import re
from collections import Counter

from .. import frontmatter
from ..gitstore.view import TreeView
from ..models import RawSnapshot

KNOWLEDGE = ["tasks/P-*.md", "goals/*.md", "decisions/*.md", "research/*.md", "methods/*.md", "pending/*.md"]
RAW = ["raw/aone/*/*.json", "raw/kb/*/*.json", "raw/im/*/*.json"]
_CJK = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")


def tokenize(text: str) -> list[str]:
    tokens = [w.lower() for w in _WORD.findall(text)]
    for run in re.findall(r"[一-鿿]+", text):
        tokens += list(run)
        tokens += [run[i : i + 2] for i in range(len(run) - 1)]
    return tokens


def _docs(view: TreeView, visibility: set[str]):
    for pattern in KNOWLEDGE:
        for rel in view.glob(pattern):
            if rel.endswith("README.md") or "/_" in rel:
                continue
            text = view.read_text(rel)
            meta, body = frontmatter.split(text)
            yield rel, str(meta.get("title") or meta.get("id") or rel), f"{meta.get('title', '')}\n{body}", None
    for pattern in RAW:
        for rel in view.glob(pattern):
            snap = RawSnapshot.model_validate(view.read_json(rel))
            if snap.deleted or snap.visibility not in visibility:
                continue
            d = snap.data
            title = str(d.get("title") or d.get("text", "")[:40] or snap.object_id)
            body = "\n".join(str(d.get(k, "")) for k in ("title", "description", "body", "text", "status"))
            yield rel, f"[{snap.source}] {snap.object_id} {title}", body, snap.link


def search(view: TreeView, query: str, k: int = 8, visibility: tuple[str, ...] = ("team",)) -> list[dict]:
    docs = list(_docs(view, set(visibility)))
    if not docs:
        return []
    tokenized = [Counter(tokenize(body)) for _, _, body, _ in docs]
    avg = sum(sum(t.values()) for t in tokenized) / len(tokenized) or 1
    q = set(tokenize(query))
    df = {t: sum(1 for d in tokenized if t in d) for t in q}
    n, k1, b = len(docs), 1.5, 0.75
    scored = []
    for (rel, title, body, link), tf in zip(docs, tokenized):
        length = sum(tf.values()) or 1
        score = 0.0
        for t in q:
            if not tf.get(t):
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * length / avg))
        if score > 0:
            scored.append((score, rel, title, body, link))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, rel, title, body, link in scored[:k]:
        snippet = _snippet(body, q)
        out.append({"path": rel, "commit": view.git.last_commit_for(rel), "title": title, "score": round(score, 3),
                    "snippet": snippet, **({"link": link} if link else {})})
    return out


def _snippet(body: str, q: set[str], width: int = 120) -> str:
    flat = " ".join(body.split())
    pos = min((flat.lower().find(t) for t in q if flat.lower().find(t) >= 0), default=0)
    start = max(0, pos - width // 3)
    return flat[start : start + width]
