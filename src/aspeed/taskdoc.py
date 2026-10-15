"""任务上下文包文档：TaskPack（frontmatter）+ 固定小节正文。"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import frontmatter
from .models.task import SECTIONS, TaskPack

PLACEHOLDER = "（待补充）"


@dataclass
class TaskDoc:
    meta: TaskPack
    sections: dict[str, str] = field(default_factory=dict)
    preamble: str = ""

    def section(self, name: str) -> str:
        text = (self.sections.get(name) or "").strip()
        return "" if text == PLACEHOLDER else text

    def render(self) -> str:
        parts = []
        if self.preamble:
            parts.append(self.preamble)
        names = SECTIONS + [k for k in self.sections if k not in SECTIONS]
        for name in names:
            parts.append(f"## {name}\n\n{(self.sections.get(name) or '').strip() or PLACEHOLDER}")
        return frontmatter.join(self.meta.dump(), "\n\n".join(parts))


def parse_meta_and_body(text: str) -> tuple[dict, str, dict[str, str]]:
    meta, body = frontmatter.split(text)
    preamble, secs = frontmatter.sections(body)
    return meta, preamble, secs


def parse(text: str) -> TaskDoc:
    meta, preamble, secs = parse_meta_and_body(text)
    return TaskDoc(meta=TaskPack.model_validate(meta), sections=secs, preamble=preamble)
