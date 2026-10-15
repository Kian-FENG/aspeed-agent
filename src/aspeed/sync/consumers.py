"""下游消费进度：水位线是“已处理到的 Git commit”，待处理变化用 git diff 取得。

消费者在同一个 mutation 中写出处理结果并推进水位线，二者原子提交，
不会出现“已采集但尚未处理”因中断被跳过。
"""

from __future__ import annotations

from ..gitstore.view import TreeView, Workspace
from ..models.sync import ConsumerRecord
from ..repo import load_consumer, save_consumer


def pending(view: TreeView, name: str, paths: list[str]) -> tuple[str | None, list[str]]:
    rec = load_consumer(view, name)
    since = rec.through_commit if rec else None
    if since and not view.git.is_ancestor(since, view.head):
        since = None  # 不应发生（禁止改写历史）；保守地全量处理
    return since, view.git.diff_names(since, view.head, paths)


def advance(ws: Workspace, name: str, paths: list[str], through: str | None = None) -> None:
    """through 默认为本次 mutation 的基准 commit；若处理的是更早读取的视图，传入那个 commit。"""
    save_consumer(ws, ConsumerRecord(name=name, paths=paths, through_commit=through or ws.base, updated_at=ws.now))
