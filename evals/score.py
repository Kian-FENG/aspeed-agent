"""评测打分：对 Agent 输出做确定性检查。用法：python evals/score.py <cases_dir> <out_dir>"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from aspeed import frontmatter
from aspeed.models import TestPlan


def _get(data, dotted):
    for key in dotted.split("."):
        data = (data or {}).get(key)
    return data


def score_intake(expect, text):
    meta, body = frontmatter.split(text)
    _, secs = frontmatter.sections(body)
    errs = []
    for k, v in (expect.get("frontmatter") or {}).items():
        if meta.get(k) != v:
            errs.append(f"frontmatter.{k}={meta.get(k)!r}，期望 {v!r}")
    if "stage_in" in expect and meta.get("stage", "claimable") not in expect["stage_in"]:
        errs.append(f"stage={meta.get('stage')}")
    for s in expect.get("sections_nonempty", []):
        if not (secs.get(s) or "").strip():
            errs.append(f"小节「{s}」为空")
    for s, words in (expect.get("must_mention") or {}).items():
        errs += [f"「{s}」缺少「{w}」" for w in words if w not in secs.get(s, "")]
    for s, words in (expect.get("must_not_mention") or {}).items():
        errs += [f"「{s}」不应包含「{w}」" for w in words if w in secs.get(s, "")]
    return errs


def score_claim(expect, text):
    data = json.loads(text)
    return [f"{k}={data.get(k)!r}，期望 {v!r}" for k, v in expect["json"].items() if data.get(k) != v]


def score_testplan(expect, text):
    data = yaml.safe_load(text)
    errs = [f"{k}={_get(data, k)!r}，期望 {v!r}" for k, v in (expect.get("yaml") or {}).items() if _get(data, k) != v]
    for want in expect.get("checks_contain", []):
        if not any(all(c.get(k) == v for k, v in want.items()) for c in data.get("checks", [])):
            errs.append(f"缺少阈值检查 {want}")
    if expect.get("must_validate"):
        try:
            TestPlan.model_validate({**data, "requested_by": data.get("requested_by", "m:eval")})
        except Exception as exc:  # noqa: BLE001
            errs.append(f"Schema 校验失败：{exc}")
    return errs


SCORERS = {"intake": (score_intake, ".md"), "claim": (score_claim, ".json"), "testplan": (score_testplan, ".yaml")}


def main(cases_dir: str, out_dir: str) -> int:
    total = failed = 0
    for case in sorted(Path(cases_dir).glob("*/*.yaml")):
        category = case.parent.name
        scorer, ext = SCORERS[category]
        out = Path(out_dir) / category / (case.stem + ext)
        total += 1
        if not out.exists():
            print(f"MISSING {category}/{case.stem}")
            failed += 1
            continue
        errs = scorer(yaml.safe_load(case.read_text())["expect"], out.read_text())
        print(("FAIL " if errs else "PASS ") + f"{category}/{case.stem}" + "".join(f"\n  - {e}" for e in errs))
        failed += bool(errs)
    print(f"\n{total - failed}/{total} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
