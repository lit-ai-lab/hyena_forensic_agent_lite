import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

PROMPT = "j4"
SYSTEM = """You are a judge that marks which conditions of an answer-key specification each claim in an investigation report states.

Input
- claims: the claims in the report, numbered k.
- hypotheses: the assertions of the answer-key specification. Each assertion has conditions.
- aliases: other spellings of the same person.

Task: for each claim, list the conditions (hypothesis id, condition id) that the claim states as fact, together with the wording copied verbatim from that claim.

Rules
1. Look only at what the claim sentence says. Read file names inside the sentence as part of the sentence, but do not fill gaps with background knowledge or guesses.
2. To fill a condition, the claim must assert its content as fact. Anything stated as a guess, suspicion, or possibility does not fill it.
3. Negated sentences, and sentences in which a person's role is swapped relative to the condition, do not fill it.
4. Use aliases to decide whether two names refer to the same person.
5. One claim may fill several conditions and several assertions, or none at all. Do not try to fill every condition.
6. Write each claim separately. Even if another claim states the same condition, if this claim states it, list it in this claim's fills.
7. quote must be copied verbatim from that claim's sentence.
8. List every claim in claims exactly once. fact is true if the claim states a fact, and false if it is a recommendation, an opinion, or a limitation.
   hedged is true if the claim is phrased as a guess, suspicion, or possibility."""


def conditions(spec):
    return {n["id"]: [c["id"] for c in n.get("conditions", [])] for n in spec["hypotheses"] + spec.get("aux", [])}


def judge_input(report, spec):
    nodes = spec["hypotheses"] + spec.get("aux", [])
    return {"claims": [{"k": c["k"], "text": c["text"]} for c in report["claims"]],
            "hypotheses": [{"id": n["id"], "kind": "required" if n in spec["hypotheses"] else "auxiliary", "label": n["label"],
                            "conditions": n["conditions"]} for n in nodes],
            "aliases": spec.get("aliases", {})}


def schema(spec):
    conds = conditions(spec)
    fill = {"type": "object", "additionalProperties": False, "required": ["h", "cond", "quote"],
            "properties": {"h": {"type": "string", "enum": sorted(conds)},
                           "cond": {"type": "string", "enum": sorted({c for cs in conds.values() for c in cs})},
                           "quote": {"type": "string"}}}
    claim = {"type": "object", "additionalProperties": False, "required": ["k", "fact", "hedged", "fills"],
             "properties": {"k": {"type": "integer"}, "fact": {"type": "boolean"}, "hedged": {"type": "boolean"},
                            "fills": {"type": "array", "items": fill}}}
    return {"name": "claim_fills", "strict": True,
            "schema": {"type": "object", "additionalProperties": False, "required": ["claims"],
                       "properties": {"claims": {"type": "array", "items": claim}}}}


def squash(s):
    return re.sub(r"[\s\"'“”‘’`]+", "", s)


def aggregate(raw, report, spec):
    ks = [c["k"] for c in report["claims"]]
    got = sorted(c["k"] for c in raw["claims"])
    if got != sorted(ks):
        raise ValueError("claim numbers are missing or duplicated: %s (expected %s)" % (got, ks))
    text = {c["k"]: squash(c["text"]) for c in report["claims"]}
    conds, covered, links, non_fact, dropped = conditions(spec), {}, {k: set() for k in ks}, set(), []
    for c in raw["claims"]:
        if not c["fact"] or c["hedged"]:
            non_fact.add(c["k"])
            continue
        for f in c["fills"]:
            q = squash(f["quote"])
            if f["cond"] not in conds.get(f["h"], []) or not q or q not in text[c["k"]]:
                dropped.append(dict(f, k=c["k"]))
                continue
            covered.setdefault(f["h"], set()).add(f["cond"])
            links[c["k"]].add(f["h"])
    asserted = {h: bool(cs) and set(cs) <= covered.get(h, set()) for h, cs in conds.items()}
    outside = [k for k in ks if k not in non_fact and not links[k]]
    return {str(k): sorted(v) for k, v in links.items()}, asserted, outside, sorted(non_fact), dropped


def judge(run_dir, report, spec, force=False):
    from scoring.llm import MODEL, ask_json
    missing = [h for h, cs in conditions(spec).items() if not cs]
    if missing:
        raise SystemExit("Q%s: %s has no conditions - add them to the answer_key yaml first" % (spec["problem"], missing))
    path = Path(run_dir) / "claims_map.json"
    inp = judge_input(report, spec)
    sha = hashlib.sha256(json.dumps([PROMPT, MODEL, SYSTEM, inp], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if path.exists() and not force:
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("status") == "ok" and old.get("judge", {}).get("input_sha") == sha:
            return old
    meta = {"model": MODEL, "prompt": PROMPT, "ruleset_version": spec.get("ruleset_version"), "input_sha": sha,
            "created": datetime.now().isoformat(timespec="seconds")}
    user = json.dumps(inp, ensure_ascii=False, indent=1)
    result, error = None, ""
    for _ in range(2):
        try:
            raw = ask_json(SYSTEM, user, schema(spec))
            links, asserted, outside, non_fact, dropped = aggregate(raw, report, spec)
            result = {"status": "ok", "judge": meta, "links": links, "asserted": asserted, "outside": outside,
                      "non_fact": non_fact, "dropped": dropped, "raw": raw, "input": inp}
            break
        except (RuntimeError, ValueError) as err:
            error = str(err)
    result = result or {"status": "failed", "judge": meta, "error": error, "input": inp}
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def load(run_dir):
    path = Path(run_dir) / "claims_map.json"
    if not path.exists():
        return None
    m = json.loads(path.read_text(encoding="utf-8"))
    if m.get("status") != "ok":
        return None
    return {"links": {int(k): v for k, v in m["links"].items()}, "asserted": m["asserted"],
            "outside": set(m["outside"]), "non_fact": set(m["non_fact"])}
