"""ContextStore：一个专用的 aspeed-context clone，以及在其上的发布器。"""

from __future__ import annotations

from pathlib import Path

from .gitstore import Git, Publisher, PublishResult, TreeView
from .gitstore.validate import publish_validator
from .models.policy import Policy
from .models import Roster
from .repo import load_policy, load_roster
from .runtime import RunContext


class ContextStore:
    def __init__(self, root: str | Path, ctx: RunContext | None = None, max_attempts: int = 8):
        self.git = Git(root)
        if not (self.git.root / ".git").exists():
            raise FileNotFoundError(f"{self.git.root} 不是 aspeed-context 的 git clone")
        self.ctx = ctx or RunContext()
        self.publisher = Publisher(self.git, self.ctx, validator=publish_validator, max_attempts=max_attempts)

    @property
    def root(self) -> Path:
        return self.git.root

    def view(self, refresh: bool = True) -> TreeView:
        """读取视图。专用 clone 会先更新到远端最新；有本地改动时按原样读取。"""
        if refresh and self.git.has_remote() and self.git.is_clean():
            try:
                if self.git.ahead_count() == 0:
                    self.publisher.refresh()
            except Exception:
                pass
        return TreeView(self.git)

    def publish(self, mutation, message: str, **trailers: str) -> PublishResult:
        names = {k.replace("_", "-").title(): v for k, v in trailers.items()}
        return self.publisher.publish(mutation, message, names)

    def policy(self) -> Policy:
        return load_policy(TreeView(self.git))

    def roster(self) -> Roster:
        return load_roster(TreeView(self.git))
