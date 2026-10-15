"""接入层契约。ASpeed 不实现 Aone / NodeManager / IM / 知识库的底层 API，
只定义调用契约；具体实现由已有 SKILL 通过脚本桥接（script.py）或 Agent 中介票据提供。

列表接口统一按 (updated_at, id) 升序返回并使用游标分页，保证分页稳定。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..errors import AspeedError
from ..snapshots import SourceObject


class AdapterError(AspeedError):
    code = "adapter_error"


class TransientError(AdapterError):
    """可重试，且确定没有产生副作用（如限流、请求未送达）。"""

    code = "transient"


class PermanentError(AdapterError):
    """确定失败，没有产生副作用。"""

    code = "permanent"


class UnknownOutcome(AdapterError):
    """结果未知（如写入后超时）：不得直接重试，必须先按操作标记查询。"""

    code = "unknown_outcome"


class ForbiddenError(AdapterError):
    code = "forbidden"


class StaleVersion(AdapterError):
    """条件更新失败：对象已被他人修改。"""

    code = "stale_version"


class NotConfigured(AdapterError):
    code = "not_configured"


@dataclass
class Page:
    items: list[SourceObject] = field(default_factory=list)
    next_token: str | None = None


class SyncSource(ABC):
    """可同步来源的统一读取面。"""

    source_name: str = ""
    capabilities: set[str] = set()

    @abstractmethod
    def list_objects(self, scope: str, updated_since: str | None = None, page_token: str | None = None, page_size: int = 50) -> Page: ...

    @abstractmethod
    def get_object(self, scope: str, object_id: str) -> SourceObject | None: ...


class AoneAdapter(SyncSource):
    """Aone 需求 / 缺陷。规范化后的 data 字段：
    id, type(defect|requirement|task), title, status, owner, due(YYYY-MM-DD), severity,
    participants[{account, role}], description, project
    """

    source_name = "aone"

    @abstractmethod
    def create_item(self, scope: str, fields: dict, op_marker: str) -> dict: ...

    @abstractmethod
    def add_participant(self, scope: str, item_id: str, account: str, role: str, op_marker: str) -> dict: ...

    @abstractmethod
    def transition(self, scope: str, item_id: str, to_status: str, expected_version: int | None, op_marker: str) -> dict: ...

    @abstractmethod
    def find_by_marker(self, scope: str, op_marker: str) -> dict | None: ...


class NodeManagerAdapter(ABC):
    source_name = "nodemanager"
    capabilities: set[str] = set()

    @abstractmethod
    def submit(self, plan: dict, dedupe_key: str) -> dict: ...

    @abstractmethod
    def find_by_dedupe(self, dedupe_key: str) -> dict | None: ...

    @abstractmethod
    def status(self, job_id: str) -> dict: ...

    @abstractmethod
    def results(self, job_id: str) -> dict: ...

    @abstractmethod
    def cancel(self, job_id: str) -> dict: ...

    @abstractmethod
    def quota(self) -> dict: ...


class IMAdapter(SyncSource):
    source_name = "im"

    @abstractmethod
    def send(self, target: str, text: str, op_marker: str) -> dict: ...

    @abstractmethod
    def find_sent(self, op_marker: str) -> dict | None: ...

    @abstractmethod
    def get_message(self, message_id: str) -> dict | None: ...

    @abstractmethod
    def resolve_user(self, im_user: str) -> dict | None: ...


class KBAdapter(SyncSource):
    source_name = "kb"


@dataclass
class Adapters:
    aone: AoneAdapter | None = None
    nodemanager: NodeManagerAdapter | None = None
    im: IMAdapter | None = None
    kb: KBAdapter | None = None

    def get(self, name: str):
        adapter = getattr(self, name, None)
        if adapter is None:
            raise NotConfigured(f"未配置 {name} 接入")
        return adapter

    def source(self, name: str) -> SyncSource:
        adapter = self.get(name)
        if not isinstance(adapter, SyncSource):
            raise NotConfigured(f"{name} 不是可同步来源")
        return adapter
