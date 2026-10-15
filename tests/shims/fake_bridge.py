"""测试用脚本桥：按 ScriptBridge 协议把调用转给 Fake 实现。

真实接入时，用同样的协议包装 Aone / NodeManager / IM SKILL 即可。
用法：python fake_bridge.py aone|nodemanager|im|kb
"""

import dataclasses
import json
import sys

from aspeed.adapters import fake
from aspeed.adapters.base import AdapterError
from aspeed.snapshots import SourceObject

CLASSES = {"aone": fake.FakeAone, "nodemanager": fake.FakeNodeManager, "im": fake.FakeIM, "kb": fake.FakeKB}


def encode(value):
    if isinstance(value, SourceObject):
        return {**dataclasses.asdict(value), "updated_at": str(value.updated_at) if value.updated_at else None}
    if hasattr(value, "items") and hasattr(value, "next_token") and not isinstance(value, dict):
        return {"items": [encode(i) for i in value.items], "next_token": value.next_token}
    return value


def main():
    adapter = CLASSES[sys.argv[1]]()
    req = json.loads(sys.stdin.read())
    try:
        result = getattr(adapter, req["method"])(**req["args"])
        print(json.dumps({"result": encode(result)}, ensure_ascii=False))
    except AdapterError as exc:
        print(json.dumps({"error": {"type": exc.code, "message": str(exc)}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
