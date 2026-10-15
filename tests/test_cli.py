import json

import yaml

from aspeed.adapters.fake import FakeAone, FakeIM
from aspeed.cli import main

DRAFT = """---
title: 训练集群显存碎片排查
coordinator: m:alice
acceptor: m:bob
skills_needed: [pytorch]
acceptance:
  - {id: AC1, check: "复现脚本与根因说明", mode: human}
---

## 为什么做

大模型训练 OOM 频发。

## 如何参与

复现、分析 allocator 日志。
"""


def run(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr().out
    try:
        return code, json.loads(out)
    except json.JSONDecodeError:
        return code, out


def test_cli_end_to_end(env, tmp_path, capsys, monkeypatch):
    ctx = str(env.clone())
    monkeypatch.setenv("ASPEED_CONTEXT", ctx)
    draft = tmp_path / "draft.md"
    draft.write_text(DRAFT)
    code, created = run(capsys, "task", "create", "--file", str(draft), "--as", "m:carol")
    assert code == 0 and created["stage"] == "claimable"
    tid = created["id"]
    code, related = run(capsys, "task", "related", "--query", "显存 OOM")
    assert related[0]["path"] == f"tasks/{tid}.md"
    code, joined = run(capsys, "task", "join", tid, "--member", "m:dave", "--role", "lead")
    assert joined["stage"] == "in_progress"
    code, err = run(capsys, "task", "join", tid, "--member", "m:nobody")
    assert code == 2 and err["error"] == "permission_denied"
    code, md = run(capsys, "views", "show", "board")
    assert "训练集群显存碎片排查" in md

    # 缺陷跟进：同步 → 扫描（dry-run）→ 切换为确认模式后批准执行
    FakeAone().seed("proj-x", type="defect", title="逾期缺陷", status="in_progress", owner="dave", due="2026-10-01")
    code, synced = run(capsys, "sync", "run", "aone", "--scope", "proj-x")
    assert synced["status"] == "ok"
    code, scan = run(capsys, "defects", "scan")
    assert {c["check"] for c in scan["created"]} == {"overdue", "overdue_escalate"}
    op_id = next(c["op_id"] for c in scan["created"] if c["check"] == "overdue")
    code, approved = run(capsys, "op", "approve", op_id, "--by", "m:alice", "--evidence", "im:msg/7")
    assert approved["state"] == "approved"
    code, done = run(capsys, "op", "run", op_id)
    assert done["state"] == "recorded"
    assert FakeIM().sent()[0]["target"] == "im:u004"

    # 定时任务与日报
    code, tick = run(capsys, "schedule", "tick")
    assert code == 0 and all(s["ok"] for s in tick["steps"])
    facts = tmp_path / "facts.json"
    code, collected = run(capsys, "daily", "collect", "--out", str(facts))
    assert collected["facts"] > 0
    code, published = run(capsys, "daily", "publish", "--facts", str(facts))
    assert code == 0 and published["path"].startswith("reports/daily/")
    code, val = run(capsys, "validate")
    assert val["ok"], val["errors"]


def test_schema_export(tmp_path, capsys):
    code, out = run(capsys, "schema", "export", str(tmp_path / "schemas"))
    assert code == 0 and "task-pack" in out["exported"]
    schema = json.loads((tmp_path / "schemas" / "task-pack.schema.json").read_text())
    assert "title" in schema["properties"]


def test_validate_catches_bad_human_edit(env, capsys, monkeypatch):
    import subprocess

    ctx = env.clone()
    (ctx / "control" / "members.yaml").write_text(yaml.safe_dump({"members": [{"id": "m:a", "name": "A"}, {"id": "m:a", "name": "B"}]}))
    subprocess.run(["git", "-C", str(ctx), "commit", "-qam", "bad"], check=True)
    code, out = run(capsys, "--context", str(ctx), "validate", "--base", "origin/main")
    assert not out["ok"] and "重复" in out["errors"][0]["error"]


def test_committed_schemas_are_current(tmp_path, capsys):
    from pathlib import Path

    run(capsys, "schema", "export", str(tmp_path))
    repo = Path(__file__).parent.parent / "schemas"
    for f in tmp_path.glob("*.json"):
        assert (repo / f.name).read_text() == f.read_text(), f"schemas/{f.name} 已过期：运行 aspeed schema export schemas"
