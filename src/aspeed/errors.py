"""统一异常类型。CLI 将其转为结构化错误输出，供 Agent 判断下一步。"""


class AspeedError(Exception):
    code = "error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.details = details

    def to_dict(self) -> dict:
        return {"error": self.code, "message": str(self), **self.details}


class ValidationFailed(AspeedError):
    code = "validation_failed"


class NotFound(AspeedError):
    code = "not_found"


class PermissionDenied(AspeedError):
    code = "permission_denied"


class Conflict(AspeedError):
    """业务条件不满足（例如租约被他人持有、预期版本已变化）。"""

    code = "conflict"


class Contention(AspeedError):
    """Git 推送多次被拒，超过重试上限。"""

    code = "contention"


class DirtyWorkspace(AspeedError):
    code = "dirty_workspace"


class BudgetExceeded(AspeedError):
    code = "budget_exceeded"


class Abort(AspeedError):
    """mutation 主动放弃：重新判断后业务条件不再成立，不产生提交。"""

    code = "aborted"
