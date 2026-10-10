import json
import re

REVIEWED_SESSION = re.compile(r"Reviewed Codex session id:\s*([0-9a-f-]+)", re.I)


def output_text(content):
    if not isinstance(content, list):
        return ""
    return "\n".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") in {"input_text", "output_text"} and part.get("text")
    )


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def is_approval_review(text):
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(value, dict) and {"risk_level", "outcome"}.issubset(value)


def analyze_rollout(path):
    session_id = ""
    final_answer = ""
    reviewed_session_id = ""
    last_timestamp = ""

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = record.get("payload", {})
            if not session_id and isinstance(payload, dict):
                session_id = payload.get("id") or payload.get("session_id") or payload.get("thread_id") or ""
            for text in strings(payload):
                match = REVIEWED_SESSION.search(text)
                if match:
                    reviewed_session_id = match.group(1)
            if (
                record.get("type") == "response_item"
                and isinstance(payload, dict)
                and payload.get("role") == "assistant"
                and payload.get("phase") == "final_answer"
            ):
                candidate = output_text(payload.get("content"))
                if candidate:
                    final_answer = candidate
                    last_timestamp = record.get("timestamp", "")

    return {
        "jsonl_file": path.name,
        "path": path,
        "session_id": session_id,
        "parent_session_id": "",
        "reviewed_session_id": reviewed_session_id,
        "final_answer": final_answer,
        "last_timestamp": last_timestamp,
        "role": "",
        "selection_note": "",
    }


def resolve_rollouts(paths):
    rows = [analyze_rollout(path) for path in paths]
    known_ids = {row["session_id"] for row in rows if row["session_id"]}
    for row in rows:
        if row["reviewed_session_id"] in known_ids or is_approval_review(row["final_answer"]):
            row["role"] = "approval_child"
            row["parent_session_id"] = row["reviewed_session_id"]
            row["selection_note"] = "approval-review session: excluded from scoring"
        elif row["final_answer"]:
            row["role"] = "main"
            row["selection_note"] = "final answer for human readers"
        else:
            row["role"] = "ambiguous"
            row["selection_note"] = "final answer not found"

    main_by_session = {}
    for row in rows:
        if row["role"] == "main" and row["session_id"]:
            main_by_session.setdefault(row["session_id"], []).append(row)
    for duplicates in main_by_session.values():
        if len(duplicates) < 2:
            continue
        latest_time = max(row["last_timestamp"] for row in duplicates)
        latest = [row for row in duplicates if row["last_timestamp"] == latest_time]
        if len(latest) != 1:
            for row in duplicates:
                row["role"] = "ambiguous"
                row["selection_note"] = "several final answers in the same session; cannot tell which is the latest"
            continue
        for row in duplicates:
            if row is not latest[0]:
                row["role"] = "ambiguous"
                row["selection_note"] = "earlier final answer of the same session: excluded from scoring"
    return rows
