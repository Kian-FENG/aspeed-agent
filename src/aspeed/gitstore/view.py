"""对上下文仓库的读写视图。mutation 只通过 Workspace 修改文件，以便追踪变更集合。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import yaml

from ..timeutil import now as _now
from .git import Git


def dump_yaml(data) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000)


def dump_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


class TreeView:
    def __init__(self, git: Git, head: str | None = None, now: datetime | None = None):
        self.git = git
        self.root = git.root
        self.head = head if head is not None else git.head()
        self.now = now or _now()

    def _abs(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError(f"路径越界: {rel}")
        if ".git" in Path(rel).parts:
            raise ValueError(f"禁止访问 .git: {rel}")
        return p

    def exists(self, rel: str) -> bool:
        return self._abs(rel).exists()

    def read_text(self, rel: str) -> str | None:
        p = self._abs(rel)
        return p.read_text(encoding="utf-8") if p.is_file() else None

    def read_yaml(self, rel: str):
        text = self.read_text(rel)
        return None if text is None else yaml.safe_load(text)

    def read_json(self, rel: str):
        text = self.read_text(rel)
        return None if text is None else json.loads(text)

    def glob(self, pattern: str) -> list[str]:
        return sorted(
            str(p.relative_to(self.root))
            for p in self.root.glob(pattern)
            if p.is_file() and ".git" not in p.relative_to(self.root).parts
        )

    def at(self, commit: str | None, rel: str) -> str | None:
        return self.git.show(commit, rel)


class Workspace(TreeView):
    """一次发布尝试的工作区：基于 base（远端最新提交）。"""

    def __init__(self, git: Git, base: str | None, now: datetime | None = None):
        super().__init__(git, head=base, now=now)
        self.base = base
        self.changed: set[str] = set()
        self.deleted: set[str] = set()

    def at_base(self, rel: str) -> str | None:
        return self.git.show(self.base, rel)

    def write_text(self, rel: str, text: str) -> bool:
        p = self._abs(rel)
        if p.is_file() and p.read_text(encoding="utf-8") == text:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        self.changed.add(rel)
        self.deleted.discard(rel)
        return True

    def write_yaml(self, rel: str, data) -> bool:
        return self.write_text(rel, dump_yaml(data))

    def write_json(self, rel: str, data) -> bool:
        return self.write_text(rel, dump_json(data))

    def delete(self, rel: str) -> bool:
        p = self._abs(rel)
        if not p.exists():
            return False
        p.unlink()
        self.changed.add(rel)
        self.deleted.add(rel)
        return True
