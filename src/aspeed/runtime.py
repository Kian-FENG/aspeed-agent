"""运行上下文：一次 IM 请求或定时任务对应一个运行实例。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import __version__
from .ids import run_id as new_run_id


@dataclass
class RunContext:
    run_id: str = field(default_factory=lambda: os.environ.get("ASPEED_RUN_ID") or new_run_id())
    actor: str = field(default_factory=lambda: os.environ.get("ASPEED_ACTOR", "agent:aspeed"))
    code_version: str = field(default_factory=lambda: os.environ.get("ASPEED_CODE_VERSION", __version__))
    git_name: str = field(default_factory=lambda: os.environ.get("ASPEED_GIT_NAME", "aspeed-agent"))
    git_email: str = field(default_factory=lambda: os.environ.get("ASPEED_GIT_EMAIL", "aspeed-agent@localhost"))
