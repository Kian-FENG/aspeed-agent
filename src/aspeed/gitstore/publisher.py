"""Git 发布器：读取远端版本 → 生成变更 → 校验 → 提交 → 仅快进更新。

远端变化时重新拉取，并在新基准上重新执行 mutation（重新判断业务条件），
而不是对文本做合并。重试有上限，采用指数退避加抖动；禁止强制覆盖历史。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..errors import Abort, AspeedError, Contention, DirtyWorkspace
from ..runtime import RunContext
from ..timeutil import now as _now
from .git import Git
from .view import Workspace

Mutation = Callable[[Workspace], Any]
Validator = Callable[[Workspace, set[str]], None]


@dataclass
class PublishResult:
    status: str  # committed | noop | aborted
    commit: str | None
    attempts: int
    value: Any = None
    changed: list[str] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "commit": self.commit,
            "attempts": self.attempts,
            "changed": self.changed,
            **({"reason": self.reason} if self.reason else {}),
        }


class Publisher:
    def __init__(
        self,
        git: Git,
        ctx: RunContext | None = None,
        validator: Validator | None = None,
        max_attempts: int = 8,
        backoff_base: float = 0.05,
        backoff_cap: float = 2.0,
    ):
        self.git = git
        self.ctx = ctx or RunContext()
        self.validator = validator
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap

    # 发布前确认这是一个专用 clone：无未提交改动、无未推送提交，避免误丢人的工作
    def precheck(self) -> None:
        branch = self.git.current_branch()
        if branch != self.git.branch:
            raise DirtyWorkspace(f"上下文仓库需位于 {self.git.branch} 分支，当前为 {branch}")
        if not self.git.is_clean():
            raise DirtyWorkspace("上下文仓库有未提交改动；ASpeed 需要专用 clone")
        if self.git.has_remote():
            self.git.fetch()
            if self.git.ahead_count() > 0:
                raise DirtyWorkspace("上下文仓库有未推送的本地提交；ASpeed 需要专用 clone")

    def refresh(self) -> str | None:
        """把专用 clone 更新到远端最新版本，返回基准 commit。"""
        if self.git.has_remote():
            self.git.fetch()
            if self.git.remote_head():
                self.git.reset_hard(self.git.remote_ref())
        return self.git.head()

    def publish(self, mutation: Mutation, message: str, trailers: dict[str, str] | None = None) -> PublishResult:
        self.precheck()
        has_remote = self.git.has_remote()
        for attempt in range(1, self.max_attempts + 1):
            base = self.refresh()
            ws = Workspace(self.git, base, now=_now())
            try:
                value = mutation(ws)
            except Abort as exc:
                self._discard(base)
                return PublishResult("aborted", None, attempt, reason=str(exc), value=exc.details or None)
            except Exception:
                self._discard(base)
                raise
            changed = sorted(ws.changed)
            if not changed or self.git.is_clean():
                self._discard(base)
                return PublishResult("noop", base, attempt, value=value)
            try:
                if self.validator:
                    self.validator(ws, set(changed))
                self.git.add(changed)
                commit = self.git.commit(self._message(message, trailers), self.ctx.git_name, self.ctx.git_email)
            except Exception:
                self._discard(base)
                raise
            if not has_remote:
                return PublishResult("committed", commit, attempt, value=value, changed=changed)
            ok, contention, err = self.git.push()
            if ok:
                return PublishResult("committed", commit, attempt, value=value, changed=changed)
            self._discard(base)
            if not contention:
                raise AspeedError(f"推送失败: {err}")
            if attempt < self.max_attempts:
                delay = min(self.backoff_cap, self.backoff_base * (2 ** (attempt - 1)))
                time.sleep(delay * (0.5 + random.random()))
        raise Contention(f"远端持续变化，{self.max_attempts} 次尝试后放弃", message=message)

    def _discard(self, base: str | None) -> None:
        if base:
            self.git.reset_hard(base)
        else:
            self.git.run("clean", "-fdq")

    def _message(self, subject: str, trailers: dict[str, str] | None) -> str:
        lines = [subject.strip(), ""]
        merged = {"Run-Id": self.ctx.run_id, "Actor": self.ctx.actor, "Code-Version": self.ctx.code_version}
        merged.update({k: v for k, v in (trailers or {}).items() if v})
        lines += [f"{k}: {v}" for k, v in merged.items()]
        return "\n".join(lines) + "\n"
