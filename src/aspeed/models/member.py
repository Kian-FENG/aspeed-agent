from .common import MemberId, Model


class Member(Model):
    id: MemberId
    name: str
    im: str | None = None
    aone: str | None = None
    git_email: str | None = None
    roles: list[str] = []
    areas: list[str] = []


class Roster(Model):
    members: list[Member] = []

    def get(self, member_id: str) -> Member | None:
        return next((m for m in self.members if m.id == member_id), None)

    def by_aone(self, account: str | None) -> Member | None:
        if not account:
            return None
        return next((m for m in self.members if m.aone == account), None)

    def by_im(self, im_id: str | None) -> Member | None:
        if not im_id:
            return None
        return next((m for m in self.members if m.im == im_id), None)

    def with_role(self, role: str) -> list[Member]:
        return [m for m in self.members if role in m.roles]
