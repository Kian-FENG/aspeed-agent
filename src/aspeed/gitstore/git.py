"""git 命令行的薄封装。只做快进式更新，从不强制推送。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..errors import AspeedError

_CONTENTION_MARKERS = (
    "rejected",
    "non-fast-forward",
    "fetch first",
    "cannot lock ref",
    "failed to update ref",
    "stale info",
    "incorrect old value",
)


class GitError(AspeedError):
    code = "git_error"


class Git:
    def __init__(self, root: str | Path, remote: str = "origin", branch: str | None = None):
        self.root = Path(root).resolve()
        self.remote = remote
        self.branch = branch or os.environ.get("ASPEED_BRANCH", "main")

    def _env(self) -> dict:
        env = dict(os.environ)
        env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "LANG": "C"})
        return env

    def run(self, *args: str, check: bool = True, input: str | None = None) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.root,
            env=self._env(),
            input=input,
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} 失败: {proc.stderr.strip()}", returncode=proc.returncode)
        return proc

    # ---- 查询 ----
    def head(self) -> str | None:
        proc = self.run("rev-parse", "--verify", "-q", "HEAD", check=False)
        return proc.stdout.strip() or None

    def current_branch(self) -> str | None:
        proc = self.run("symbolic-ref", "--short", "-q", "HEAD", check=False)
        return proc.stdout.strip() or None

    def has_remote(self) -> bool:
        return self.remote in self.run("remote").stdout.split()

    def remote_ref(self) -> str:
        return f"{self.remote}/{self.branch}"

    def remote_head(self) -> str | None:
        proc = self.run("rev-parse", "--verify", "-q", self.remote_ref(), check=False)
        return proc.stdout.strip() or None

    def is_clean(self) -> bool:
        return self.run("status", "--porcelain", "--untracked-files=all").stdout.strip() == ""

    def ahead_count(self) -> int:
        if not self.remote_head() or not self.head():
            return 0
        out = self.run("rev-list", "--count", f"{self.remote_ref()}..HEAD").stdout.strip()
        return int(out or 0)

    def show(self, commit: str | None, path: str) -> str | None:
        if not commit:
            return None
        proc = self.run("show", f"{commit}:{path}", check=False)
        return proc.stdout if proc.returncode == 0 else None

    def diff_names(self, a: str | None, b: str, paths: list[str] | None = None) -> list[str]:
        """a 为空时返回 b 中 paths 下的全部文件。"""
        if a is None:
            args = ["ls-tree", "-r", "--name-only", b]
        else:
            args = ["diff", "--name-only", "--no-renames", f"{a}..{b}"]
        if paths:
            args += ["--", *paths]
        return [line for line in self.run(*args).stdout.splitlines() if line]

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.run("merge-base", "--is-ancestor", a, b, check=False).returncode == 0

    def last_commit_for(self, path: str) -> str | None:
        out = self.run("log", "-1", "--format=%H", "--", path, check=False).stdout.strip()
        return out or None

    # ---- 变更 ----
    def fetch(self) -> None:
        self.run("fetch", "--quiet", self.remote, self.branch)

    def reset_hard(self, ref: str) -> None:
        self.run("reset", "--hard", "--quiet", ref)
        self.run("clean", "-fdq")

    def add(self, paths: list[str]) -> None:
        if paths:
            self.run("add", "-A", "--", *paths)

    def commit(self, message: str, name: str, email: str) -> str:
        self.run(
            "-c", f"user.name={name}", "-c", f"user.email={email}",
            "commit", "--quiet", "--no-verify", "-F", "-",
            input=message,
        )
        return self.head()

    def push(self) -> tuple[bool, bool, str]:
        """返回 (成功, 是否为并发冲突, stderr)。不使用 --force。"""
        proc = self.run("push", "--quiet", "--porcelain", self.remote, f"HEAD:refs/heads/{self.branch}", check=False)
        if proc.returncode == 0:
            return True, False, ""
        text = (proc.stderr + proc.stdout).lower()
        return False, any(m in text for m in _CONTENTION_MARKERS), proc.stderr.strip()
