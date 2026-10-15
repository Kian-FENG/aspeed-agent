"""团队规则（存放在 aspeed-context 的 control/policy.yaml，随团队一起版本化）。"""

from .common import Model


class OpPolicy(Model):
    mode: str = "confirm"  # dry_run | confirm | auto
    approver_roles: list[str] = []


class QuietHours(Model):
    start: str = "22:00"
    end: str = "08:30"


class Budget(Model):
    max_external_writes_per_day: int = 200
    reminders_per_person_per_day: int = 3
    quiet_hours: QuietHours | None = QuietHours()


class Leases(Model):
    op_seconds: int = 600
    sync_seconds: int = 900


class SourceSync(Model):
    overlap_seconds: int = 600
    page_size: int = 50
    reconcile_every_hours: int = 24
    stale_after_hours: int = 6
    heartbeat_seconds: int = 3600  # 无数据变化时，最多每隔这么久提交一次“同步成功”心跳


class DefectRules(Model):
    scopes: list[str] = []
    close_status: str = "closed"      # 回归通过后流转到的 Aone 状态
    reopen_status: str = "reopened"   # 回归失败后重新打开的 Aone 状态
    due_soon_days: int = 2
    escalate_after_days: int = 3
    repeat_days: dict[str, int] = {
        "missing_owner": 1,
        "missing_due": 2,
        "due_soon": 1,
        "overdue": 1,
        "overdue_escalate": 2,
        "missing_verification": 1,
    }


class Policy(Model):
    version: int = 1
    timezone: str = "Asia/Shanghai"
    coordinators: list[str] = []
    sources: dict[str, list[str]] = {}  # 定时同步的范围，例如 {aone: [proj-x], kb: [team-space]}
    ops: dict[str, OpPolicy] = {
        "default": OpPolicy(mode="confirm"),
        "im.remind": OpPolicy(mode="dry_run"),
        "aone.create": OpPolicy(mode="confirm", approver_roles=["coordinator"]),
        "aone.add_participant": OpPolicy(mode="auto"),
        "aone.transition": OpPolicy(mode="confirm", approver_roles=["coordinator", "acceptor"]),
        "nm.submit": OpPolicy(mode="confirm", approver_roles=["tester", "coordinator"]),
    }
    budget: Budget = Budget()
    leases: Leases = Leases()
    sync: dict[str, SourceSync] = {"default": SourceSync()}
    defects: DefectRules = DefectRules()
    stage_map: dict[str, dict[str, str]] = {
        "aone": {
            "open": "claimable",
            "reopened": "claimable",
            "in_progress": "in_progress",
            "fixed": "in_review",
            "verified": "done",
            "closed": "done",
            "cancelled": "cancelled",
        }
    }
    failure_rules: dict[str, list[str]] = {
        "environment": [r"(?i)no space left", r"(?i)connection refused", r"(?i)device (not found|lost)", r"(?i)driver"],
        "scheduling": [r"(?i)quota exceeded", r"(?i)queue timeout", r"(?i)preempted", r"(?i)no available node"],
    }

    def op(self, kind: str) -> OpPolicy:
        return self.ops.get(kind) or self.ops.get("default") or OpPolicy()

    def source_sync(self, source: str) -> SourceSync:
        return self.sync.get(source) or self.sync.get("default") or SourceSync()
