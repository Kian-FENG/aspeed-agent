"""按配置装配接入层。配置文件（ASPEED_ADAPTERS 或 --adapters）示例：

aone:        {impl: aspeed.adapters.script:ScriptAone, options: {command: "python skills-bridge/aone.py"}}
nodemanager: {impl: aspeed.adapters.fake:FakeNodeManager}
im:          {impl: aspeed.adapters.fake:FakeIM}
kb:          {impl: aspeed.adapters.fake:FakeKB}

未配置时默认全部使用 Fake（状态目录 ASPEED_FAKE_DIR）。
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import yaml

from .base import Adapters

DEFAULTS = {
    "aone": {"impl": "aspeed.adapters.fake:FakeAone"},
    "nodemanager": {"impl": "aspeed.adapters.fake:FakeNodeManager"},
    "im": {"impl": "aspeed.adapters.fake:FakeIM"},
    "kb": {"impl": "aspeed.adapters.fake:FakeKB"},
}


def _build(spec: dict):
    module, _, attr = spec["impl"].partition(":")
    cls = getattr(importlib.import_module(module), attr)
    return cls(**(spec.get("options") or {}))


def load_adapters(path: str | Path | None = None) -> Adapters:
    path = path or os.environ.get("ASPEED_ADAPTERS")
    config = dict(DEFAULTS)
    if path:
        config.update(yaml.safe_load(Path(path).read_text()) or {})
    return Adapters(**{name: _build(spec) for name, spec in config.items() if spec})
