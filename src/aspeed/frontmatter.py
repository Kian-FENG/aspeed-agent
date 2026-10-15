"""Markdown + YAML frontmatter 的解析与渲染。"""

from __future__ import annotations

import re

import yaml

from .gitstore.view import dump_yaml

_FM = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)


def split(text: str) -> tuple[dict, str]:
    m = _FM.match(text)
    if not m:
        return {}, text
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError("frontmatter 必须是 YAML 映射")
    return meta, text[m.end():]


def join(meta: dict, body: str) -> str:
    return f"---\n{dump_yaml(meta)}---\n\n{body.strip()}\n"


def sections(body: str) -> tuple[str, dict[str, str]]:
    """按二级标题切分正文，返回 (首段前言, {标题: 内容})。"""
    preamble: list[str] = []
    result: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in body.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            current = result.setdefault(m.group(1), [])
            continue
        (current if current is not None else preamble).append(line)
    return "\n".join(preamble).strip(), {k: "\n".join(v).strip() for k, v in result.items()}
