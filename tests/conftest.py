import subprocess
from pathlib import Path

import pytest

from aspeed.context_init import init_context
from aspeed.gitstore.view import dump_yaml

MEMBERS = {
    "members": [
        {"id": "m:alice", "name": "Alice", "im": "im:u001", "aone": "alice", "roles": ["coordinator", "baseline_approver"]},
        {"id": "m:bob", "name": "Bob", "im": "im:u002", "aone": "bob", "roles": ["acceptor", "tester"]},
        {"id": "m:carol", "name": "Carol", "im": "im:u003", "aone": "carol", "roles": []},
        {"id": "m:dave", "name": "Dave", "im": "im:u004", "aone": "dave", "roles": []},
    ]
}


class ContextEnv:
    def __init__(self, root: Path):
        self.root = root
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        seed = root / "seed"
        init_context(seed, remote=str(self.remote))
        policy_path = seed / "control/policy.yaml"
        policy = policy_path.read_text().replace("coordinators: []", "coordinators: [m:alice]")
        policy = policy.replace("scopes: []              # 需要跟进的 Aone 范围，例如 [proj-x]", "scopes: [proj-x]")
        policy_path.write_text(policy)
        (seed / "control/members.yaml").write_text(dump_yaml(MEMBERS))
        subprocess.run(["git", "-C", str(seed), "commit", "-qam", "测试成员与规则"], check=True)
        subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "HEAD:main"], check=True)
        self._n = 0

    def clone(self, name: str | None = None) -> Path:
        self._n += 1
        dest = self.root / (name or f"clone{self._n}")
        subprocess.run(["git", "clone", "-q", str(self.remote), str(dest)], check=True)
        return dest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASPEED_NOW", "2026-10-14T02:00:00Z")
    monkeypatch.setenv("ASPEED_FAKE_DIR", str(tmp_path / "fake"))
    return ContextEnv(tmp_path)
