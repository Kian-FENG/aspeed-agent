"""RAW 快照写入：按来源版本判断新旧，避免旧观察覆盖新观察，无变化时不写。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .gitstore.view import Workspace
from .ids import digest
from .models import RawSnapshot, compare_versions
from .repo import load_raw, raw_path


@dataclass
class SourceObject:
    """接入层返回的规范化外部对象。"""

    id: str
    type: str | None
    version: int | str | None
    updated_at: datetime | None
    data: dict
    link: str | None = None
    visibility: str = "team"


def make_snapshot(source: str, scope: str, obj: SourceObject, observed_at: datetime, deleted: bool = False) -> RawSnapshot:
    return RawSnapshot(
        source=source,
        scope=scope,
        object_id=str(obj.id),
        object_type=obj.type,
        source_version=obj.version,
        source_updated_at=obj.updated_at,
        observed_at=observed_at,
        visibility=obj.visibility,
        deleted=deleted,
        content_hash=digest({"data": obj.data, "deleted": deleted}),
        link=obj.link,
        data=obj.data,
    )


def upsert(ws: Workspace, snap: RawSnapshot, fresh_read: bool = False) -> str:
    """返回 created / updated / unchanged / stale / incomparable。

    fresh_read=True 表示调用方刚刚单独读取过该对象（权威最新），版本无法比较时也可写入。
    """
    old = load_raw(ws, snap.source, snap.scope, snap.object_id)
    rel = raw_path(snap.source, snap.scope, snap.object_id)
    if old is None:
        ws.write_json(rel, snap.dump())
        return "created"
    if old.content_hash == snap.content_hash:
        return "unchanged"
    cmp = compare_versions(snap.source_version, old.source_version)
    if cmp is not None and cmp < 0:
        return "stale"
    if (cmp is None or cmp == 0) and not fresh_read:
        return "incomparable"
    ws.write_json(rel, snap.dump())
    return "updated"
