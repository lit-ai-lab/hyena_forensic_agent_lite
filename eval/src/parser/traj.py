import json
import re
from datetime import datetime, timezone
from pathlib import Path

from parser import report
from parser.jsonl import make_call_csv

RC_RE = re.compile(r"<returncode>(-?\d+)</returncode>")
OUT_RE = re.compile(r"<output>\n?(.*?)</output>", re.S)
SPLIT_RE = re.compile(r"<output_head>\n?(.*?)</output_head>\s*<output_tail>\n?(.*?)</output_tail>", re.S)
EXC_RE = re.compile(r"<exception>(.*?)</exception>", re.S)
CUT = "\n[… middle omitted …]\n"


def observation(content: str):
    rc = RC_RE.search(content)
    split = SPLIT_RE.search(content)
    if split:
        out = split.group(1) + CUT + split.group(2)
    else:
        m = OUT_RE.search(content)
        out = m.group(1) if m else ""
    exc = EXC_RE.search(content)
    if exc:
        out += ("\n" if out else "") + exc.group(1)
    return (int(rc.group(1)) if rc else None), out


def is_report_call(cmd: str) -> bool:
    return "/tmp/report.md" in cmd or cmd.strip() == "submit"


def iso(epoch) -> str:
    return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat().replace("+00:00", "Z") if epoch else ""


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") for c in content or [] if isinstance(c, dict))


def clean(s: str) -> str:
    return s.encode("utf-8", "replace").decode("utf-8")


def calls(traj: dict):
    order, cmd_of, started, result = [], {}, {}, {}
    for m in traj["messages"]:
        if m["role"] == "assistant":
            for a in (m.get("extra") or {}).get("actions", []):
                order.append(a["tool_call_id"])
                cmd_of[a["tool_call_id"]] = clean(a["command"] if isinstance(a["command"], str) else " ".join(a["command"]))
                started[a["tool_call_id"]] = (m.get("extra") or {}).get("timestamp")
        elif m["role"] == "tool":
            result[m.get("tool_call_id")] = m
    rows, excluded = [], 0
    for cid in order:
        if is_report_call(cmd_of[cid]):
            excluded += 1
            continue
        m = result.get(cid)
        if m is None:
            rows.append({"input": cmd_of[cid], "output": "", "verdict": "fail", "ts": iso(started[cid])})
            continue
        rc, out = observation(clean(text_of(m["content"])))
        rc = (m.get("extra") or {}).get("returncode", rc)
        rows.append({"input": cmd_of[cid], "output": out, "verdict": "ok" if rc == 0 else "fail",
                     "ts": iso((m.get("extra") or {}).get("timestamp"))})
    return rows, excluded


def user_prompt(traj: dict) -> str:
    return "\n".join(text_of(m["content"]) for m in traj["messages"] if m["role"] == "user")


def convert(traj_path: Path, out_dir: Path) -> dict:
    traj_path, out_dir = Path(traj_path), Path(out_dir)
    traj = json.loads(traj_path.read_text(encoding="utf-8"))
    submission = (traj.get("info") or {}).get("submission") or ""
    if not submission.strip():
        raise ValueError("no report was submitted (exit_status=%s)" % (traj.get("info") or {}).get("exit_status"))
    rows, excluded = calls(traj)
    stem = "rollout-" + traj_path.name.split(".")[0]
    make_call_csv(rows, out_dir / (stem + ".csv"))
    rep = report.write(submission, out_dir / (stem + ".report.json"), prompt_version=report.prompt_version(user_prompt(traj)),
                       source=traj_path.name, model=((traj.get("info") or {}).get("config") or {}).get("model", {}).get("model_name", ""),
                       excluded_calls=excluded)
    return {"calls": len(rows), "excluded": excluded, "claims": len(rep["claims"]), "format": rep["report_format"]}
