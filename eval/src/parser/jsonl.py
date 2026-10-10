import csv
import json
import re
from pathlib import Path
from typing import List

CALL_TYPES = {"custom_tool_call", "function_call"}
OUTPUT_TYPES = {"custom_tool_call_output", "function_call_output"}
NON_RESULT_ITEMS = {"UserMessage", "Reasoning", "AgentMessage", None}
CELL_RE = re.compile(r"Script running with cell ID (\d+)")
TRACE_STEPS = {"TRACE_HYPOTHESIS", "TRACE_LOG"}

FIELDS = ["step", "kind",
          "hypothesis",
          "situation", "situation_label",
          "purpose_action", "purpose_action_label",
          "observation", "observation_label",
          "input", "output", "verdict",
          "item_ordinals", "item_status", "cell_id", "linked_wait_call",
          "ts"]

CALL_FIELDS = ["call_no", "input", "output", "verdict", "ts"]


def _output_text(payload) -> str:
    o = payload.get("output")
    if isinstance(o, str):
        return o
    if isinstance(o, list):
        return "".join(x.get("text", "") for x in o if isinstance(x, dict))
    return ""


def _trace(rec):
    p = rec.get("payload", {})
    if rec.get("type") != "event_msg" or p.get("type") != "item_completed":
        return None
    it = p.get("item", {})
    if it.get("type") != "AgentMessage":
        return None
    txt = "".join(c.get("text", "") for c in it.get("content") or []
                  if isinstance(c, dict)).strip()
    if not txt.startswith("{") or "TRACE_" not in txt:
        return None
    try:
        d = json.loads(txt)
    except ValueError:
        return None
    return d if isinstance(d, dict) and str(d.get("type", "")).startswith("TRACE_") else None


def extract_json(path: Path) -> List[dict]:
    path = Path(path)
    if path.suffix != ".jsonl":
        raise ValueError(f"not a jsonl file: {path}")
    with path.open(encoding="utf-8") as f:
        recs = [json.loads(l) for l in f if l.strip()]

    out_by_call = {}
    wait_by_cell = {}
    for r in recs:
        p = r.get("payload", {})
        t = p.get("type")
        if t in OUTPUT_TYPES:
            out_by_call[p.get("call_id")] = _output_text(p)
        elif t == "function_call" and p.get("name") == "wait":
            try:
                cid = json.loads(p.get("arguments") or "{}").get("cell_id")
            except (ValueError, TypeError):
                cid = None
            if cid is not None:
                wait_by_cell[str(cid)] = p.get("call_id")

    rows = []
    cur = None
    cur_step = cur_hyp = cur_situ = cur_situ_l = cur_purp = cur_purp_l = ""
    for r in recs:
        p = r.get("payload", {})
        t = p.get("type")
        d = _trace(r)
        if d is not None:
            if rows:
                rows[-1]["observation"] = d.get("last_observation", "")
                rows[-1]["observation_label"] = d.get("last_observation_label", "")
            if d["type"] in TRACE_STEPS:
                cur_step = d.get("step", "")
                cur_hyp = d.get("hypothesis", "")
                cur_situ, cur_situ_l = d.get("situation", ""), d.get("situation_label", "")
                cur_purp, cur_purp_l = d.get("purpose_action", ""), d.get("purpose_action_label", "")
        elif r.get("type") == "response_item" and t in CALL_TYPES:
            cur = {
                "step": cur_step,
                "hypothesis": cur_hyp,
                "situation": cur_situ,
                "situation_label": cur_situ_l,
                "purpose_action": cur_purp,
                "purpose_action_label": cur_purp_l,
                "observation": "",
                "observation_label": "",
                "kind": "tool" if t == "custom_tool_call" else "function",
                "call_id": p.get("call_id"),
                "call_status": p.get("status"),
                "ts": r.get("timestamp", ""),
                "input": p.get("input") if t == "custom_tool_call" else p.get("arguments"),
                "_item_ord": [], "_item_st": [],
            }
            rows.append(cur)
        elif r.get("type") == "event_msg" and t == "item_completed" and cur is not None:
            it = p.get("item", {})
            if it.get("type") not in NON_RESULT_ITEMS:
                cur["_item_ord"].append(r.get("ordinal"))
                cur["_item_st"].append(it.get("status"))

    result = []
    for row in rows:
        text = out_by_call.get(row["call_id"], "")
        m = CELL_RE.search(text)
        cell = m.group(1) if m else ""
        broke = (row["call_status"] == "failed"
                 or any(s == "failed" for s in row["_item_st"]))
        pending = bool(cell) and not broke
        fail = broke or (bool(cell) and cell not in wait_by_cell)
        result.append({
            "step": row["step"],
            "kind": row["kind"],
            "hypothesis": row["hypothesis"],
            "situation": row["situation"],
            "situation_label": row["situation_label"],
            "purpose_action": row["purpose_action"],
            "purpose_action_label": row["purpose_action_label"],
            "observation": row["observation"],
            "observation_label": row["observation_label"],
            "input": row["input"] or "",
            "output": text,
            "verdict": "fail" if fail else "pending" if pending and cell in wait_by_cell else "ok",
            "item_ordinals": ";".join(str(x) for x in row["_item_ord"]),
            "item_status": ";".join(str(x) for x in row["_item_st"]),
            "cell_id": cell,
            "linked_wait_call": wait_by_cell.get(cell, "") if cell else "",
            "ts": row["ts"],
        })
    return result


def make_csv(rows: List[dict], out_path: Path, fields: List[str] = FIELDS) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def make_call_csv(rows: List[dict], out_path: Path) -> None:
    make_csv([dict(r, call_no=i) for i, r in enumerate(rows, 1)], out_path, CALL_FIELDS)


def user_text(path: Path) -> str:
    parts = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            p = json.loads(line).get("payload", {}) if line.strip() else {}
            if p.get("type") == "message" and p.get("role") == "user":
                parts += [c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict)]
    return "\n".join(parts)


if __name__ == "__main__":
    data = Path(__file__).parents[2] / "data"
    for f in sorted(data.rglob("rollout-*.jsonl")):
        rows = extract_json(f)
        if not any(r["step"] != "" for r in rows):
            continue
        old = any(r["hypothesis"] for r in rows)
        key = "hypothesis" if old else "purpose_action"
        assert all(r["step"] != "" and r[key] and r["input"] for r in rows), f
        assert not any(r["situation"] for r in rows) if old else True, f
        assert all(r["purpose_action_label"] for r in rows) if not old else True, f
        print(f"OK: {f.parent.name}/{f.name[:20]} {len(rows):3} rows "
              f"{'old' if old else 'new'} format, {sum(r['verdict'] == 'fail' for r in rows)} failed")
