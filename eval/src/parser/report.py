import json
import re
from pathlib import Path

MIN_BLOCK_CHARS = 40

SECTION_RE = re.compile(r"^##\s*(.+?)\s*$", re.M)
JUDGE_RE = re.compile(r"^-\s*(?:Claim|Finding):\s*", re.M)
CITE_RE = re.compile(r"`([^`]+)`|\]\(\s*<?([^)>]+?)>?\s*\)|path=\"([^\"]+)\"")
EXT_RE = re.compile(r"\.[A-Za-z]\w{0,4}$")


def sections(text: str) -> dict:
    parts = SECTION_RE.split(text)
    return dict(zip(parts[1::2], (body.strip() for body in parts[2::2])))


def cites(text: str, bare: bool = False) -> list:
    found = [next((g for g in m.groups() if g), "") for m in CITE_RE.finditer(text)]
    if bare and not found:
        found = re.split(r"[,\n]", text)
    out = {}
    for path in (p.strip() for p in found):
        name = re.split(r"[\\/]", path)[-1]
        if EXT_RE.search(name) or (name == "mbox" and name != path):
            out[path] = {"path": path, "name": name}
    return list(out.values())


def claims(text: str) -> list:
    out = []
    for chunk in JUDGE_RE.split(text)[1:]:
        claim, sep, rest = chunk.partition("Evidence:")
        out.append({"claim": claim.strip(), "cites": cites(rest, bare=bool(sep))})
    return out


def blocks(text: str) -> list:
    return [{"claim": b.strip(), "cites": cites(b)}
            for b in re.split(r"\n\s*\n", text) if len(b.strip()) >= MIN_BLOCK_CHARS]


PROMPT_MARKERS = []


def prompt_version(prompt: str) -> str:
    return next((v for v, marker in PROMPT_MARKERS if marker in prompt), "unknown")


def parse(final_answer: str) -> dict:
    section = sections(final_answer)
    judged = claims(section.get("Claims") or section.get("Findings") or final_answer)
    return {
        "report_format": "structured" if judged else "free",
        "conclusion": section.get("Conclusion", ""),
        "claims": judged or blocks(final_answer),
    }


def write(final_answer: str, out_path: Path, **meta) -> dict:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {**meta, **parse(final_answer)}
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    structured = """## Conclusion
Manager Lee took cash from Mr Koo and favored Suajaju.

## Claims
- Claim: Workbook1.xlsx records 3M won in cash and a golf outing.
  Evidence: `C:\WM_HYENA_TEST\Lee\Workbook1.xlsx`
- Claim: The ranking was reversed in the re-evaluation.
  Evidence: `/tmp/eval_1st.xls`, `/tmp/eval_2nd.xls`

## Limits
- Whether Lee pushed for the re-evaluation is unconfirmed.
"""
    r = parse(structured)
    assert r["report_format"] == "structured", r["report_format"]
    assert r["conclusion"].startswith("Manager Lee"), r["conclusion"]
    assert len(r["claims"]) == 2, r["claims"]
    assert r["claims"][0]["cites"][0]["name"] == "Workbook1.xlsx", r["claims"][0]
    assert [c["name"] for c in r["claims"][1]["cites"]] == ["eval_1st.xls", "eval_2nd.xls"]
    assert "Limits" not in r["claims"][-1]["claim"], r
    assert prompt_version("anything") == "unknown"

    free = ("Short.\n\n"
            "`Suajaju` ranked first once the `delivery` item was added. "
            "See [score sheet](</C:/WM_HYENA_TEST/Lee/eval_2nd.xls>) for details.\n")
    r = parse(free)
    assert r["report_format"] == "free" and len(r["claims"]) == 1, r
    assert [c["name"] for c in r["claims"][0]["cites"]] == ["eval_2nd.xls"], r["claims"][0]["cites"]
    assert r["conclusion"] == "", r

    marker = ('On Feb 2 it ranked first with 187 points. '
              ':codex-file-citation{path="C:\WM_HYENA_TEST\Lee\eval_20210202.xlsx" '
              'purpose="source" sheet="results"}')
    got = cites(marker)
    assert [c["name"] for c in got] == ["eval_20210202.xlsx"], got

    r = parse("- Claim: An unsupported assertion.")
    assert r["report_format"] == "structured" and r["claims"][0]["cites"] == [], r

    r = parse("- Claim: The ranking was reversed.\n  Evidence: C:/a/eval_1.xls, C:/a/eval_2.xls")
    assert [c["name"] for c in r["claims"][0]["cites"]] == ["eval_1.xls", "eval_2.xls"], r
    assert cites("3M won in cash, golf outing, round planned") == []
    assert [c["name"] for c in cites(r"`C:\WM_HYENA_TEST\_mail\Outlook\Sent\mbox`")] == ["mbox"]
    assert cites("unpacked the `mbox` file") == []

    assert parse("")["claims"] == []
    print("ok")
