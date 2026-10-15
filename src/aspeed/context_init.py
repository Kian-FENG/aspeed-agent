"""初始化 aspeed-context 仓库，以及为运行实例准备专用 clone。"""

from __future__ import annotations

import shutil
from pathlib import Path

from .gitstore.git import Git

TEMPLATE = Path(__file__).parent / "templates" / "context"


def init_context(path: str | Path, remote: str | None = None, branch: str = "main") -> dict:
    dest = Path(path).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} 非空")
    shutil.copytree(TEMPLATE, dest, dirs_exist_ok=True)
    git = Git(dest, branch=branch)
    git.run("init", "-q", "-b", branch)
    git.run("add", "-A")
    git.commit("初始化 aspeed-context", "aspeed-agent", "aspeed-agent@localhost")
    if remote:
        git.run("remote", "add", "origin", remote)
        git.run("push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        git.run("branch", "-q", f"--set-upstream-to=origin/{branch}")
    return {"path": str(dest), "remote": remote, "commit": git.head()}


def clone_context(remote: str, path: str | Path, branch: str = "main", partial: bool = True) -> dict:
    """沙箱用：partial clone（按需拉取 blob），只取单分支。"""
    dest = Path(path).resolve()
    args = ["clone", "-q", "--single-branch", "--branch", branch]
    if partial:
        args.append("--filter=blob:none")
    Git(dest.parent).run(*args, remote, str(dest))
    return {"path": str(dest), "commit": Git(dest).head()}
