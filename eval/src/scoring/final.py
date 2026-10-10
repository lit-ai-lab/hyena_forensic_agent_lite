import hashlib
import json
from datetime import datetime
from pathlib import Path

from scoring import claims
from scoring.claims import squash

FAS_PROMPT = "f2"
FAS_SYSTEM = """You are a judge that decides whether the final conclusion of an investigation report matches the correct answer to this question.

Input
- conclusion: the final conclusion the agent produced.
- answer.text: the correct-answer sentence. answer.note: the full-credit condition.
- aliases: other spellings of the same person.

Task: decide whether the conclusion matches the correct answer.

Rules
1. Look only at what the conclusion says. Do not fill gaps with background knowledge or guesses.
2. If the content of the correct-answer sentence (who, to whom, what, where) and the full-credit condition are stated as fact in the conclusion, match=true. A different wording of the same fact is fine.
   Whatever the full-credit condition says must be stated together is required to appear together in one conclusion.
3. Anything stated only as a guess, suspicion, or possibility does not count as included. "Circumstances showing that ... are confirmed" counts as stated as fact.
4. Negated sentences, and sentences in which a person's role is swapped relative to the correct answer, do not match. Use aliases to decide whether two names refer to the same person.
5. Extra content that is not in the correct answer is not penalized. Content that contradicts the correct answer makes match=false.
6. If only part of it matches, match=false, and the missing content is written briefly in missing. If match=true, missing is an empty array.
7. If match=true, copy into quote the wording of the conclusion that fills the correct answer, verbatim (several entries if it is in several places). If match=false, quote is an empty array."""

LEAK_PROMPT = "l3"
LEAK_SYSTEM = """You are a judge that marks whether the content of error-path claims appears in the final conclusion.

Input
- conclusion: the final conclusion the agent produced.
- judgments: the error-path claims (claims that did not fill any condition of the answer graph), numbered k.

Task: for each claim, mark whether its core content appears in the conclusion.

Rules
1. If the conclusion has a passage that states the claim's core facts (who, what, where, when, how much) as fact, in_conclusion=true. A different wording of the same fact is fine.
2. A few overlapping words are not enough. Even if the same person or the same subject appears, it is false when the conclusion does not state that claim's fact.
3. Even if the conclusion states it as a guess, it is true when the content is the same.
4. List every claim in judgments exactly once.
5. If in_conclusion=true, copy the corresponding passage of the conclusion into quote verbatim. If false, quote is an empty string."""


def conclusion_of(report):
    return report["conclusion"] or "\n\n".join(c["text"] for c in report["claims"])


def error_paths(report, claim_map):
    if claim_map is None:
        return None
    return [{"k": cl["k"], "text": cl["text"]} for cl in report["claims"] if cl["k"] in claim_map["outside"]]


def fas_input(report, spec):
    ans = spec["answer"]
    return {"conclusion": conclusion_of(report), "answer": {"text": ans["text"], "note": ans.get("note", "")},
            "aliases": spec.get("aliases", {})}


def leak_input(report, suspects):
    return {"conclusion": conclusion_of(report), "judgments": suspects}


def fas_schema():
    return {"name": "final_answer", "strict": True,
            "schema": {"type": "object", "additionalProperties": False, "required": ["match", "quote", "missing"],
                       "properties": {"match": {"type": "boolean"},
                                      "quote": {"type": "array", "items": {"type": "string"}},
                                      "missing": {"type": "array", "items": {"type": "string"}}}}}


def leak_schema():
    item = {"type": "object", "additionalProperties": False, "required": ["k", "in_conclusion", "quote"],
            "properties": {"k": {"type": "integer"}, "in_conclusion": {"type": "boolean"}, "quote": {"type": "string"}}}
    return {"name": "leak_check", "strict": True,
            "schema": {"type": "object", "additionalProperties": False, "required": ["judgments"],
                       "properties": {"judgments": {"type": "array", "items": item}}}}


def aggregate_fas(raw, conclusion):
    if not raw["match"]:
        return False, [], list(raw["missing"])
    pool = squash(conclusion)
    quotes = [q for q in raw["quote"] if squash(q)]
    if not quotes or not all(squash(q) in pool for q in quotes):
        raise ValueError("match was reported but the quote is empty or not in the conclusion: %s" % raw["quote"])
    return True, quotes, []


def aggregate_leak(raw, ks, conclusion):
    got = sorted(j["k"] for j in raw["judgments"])
    if got != sorted(ks):
        raise ValueError("claim numbers are missing or duplicated: %s (expected %s)" % (got, sorted(ks)))
    pool = squash(conclusion)
    for j in raw["judgments"]:
        if j["in_conclusion"] and not (squash(j["quote"]) and squash(j["quote"]) in pool):
            raise ValueError("in_conclusion was reported but the quote is not in the conclusion: claim %s" % j["k"])
    return sorted(j["k"] for j in raw["judgments"] if j["in_conclusion"]), raw["judgments"]


def sha_of(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def ask(system, user, schema, check):
    from scoring.llm import ask_json
    error = ""
    for _ in range(2):
        try:
            raw = ask_json(system, user, schema)
            return check(raw), raw, ""
        except (RuntimeError, ValueError) as err:
            error = str(err)
    return None, None, error


def judge(run_dir, report, spec, force=False):
    from scoring.llm import MODEL
    if not spec.get("answer", {}).get("text"):
        raise SystemExit("Q%s has no answer.text (correct-answer sentence) - add it to the answer_key yaml first" % spec["problem"])
    path = Path(run_dir) / "final_map.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() and not force else {}
    out = {}

    def meta(prompt, sha):
        return {"model": MODEL, "prompt": prompt, "ruleset_version": spec.get("ruleset_version"), "input_sha": sha,
                "created": datetime.now().isoformat(timespec="seconds")}

    def cached(name, sha):
        return old.get(name) if old.get(name, {}).get("status") == "ok" and old[name]["judge"]["input_sha"] == sha else None

    inp = fas_input(report, spec)
    sha = sha_of(FAS_PROMPT, MODEL, FAS_SYSTEM, inp)
    out["fas"] = cached("fas", sha)
    if not out["fas"]:
        got, raw, error = ask(FAS_SYSTEM, json.dumps(inp, ensure_ascii=False, indent=1), fas_schema(),
                              lambda r: aggregate_fas(r, inp["conclusion"]))
        out["fas"] = {"status": "ok", "judge": meta(FAS_PROMPT, sha), "match": got[0], "quote": got[1], "missing": got[2],
                      "raw": raw, "input": inp} if got else {"status": "failed", "judge": meta(FAS_PROMPT, sha), "error": error, "input": inp}

    suspects = error_paths(report, claims.load(run_dir))
    if suspects is None:
        out["leak"] = {"status": "skipped", "reason": "no claim-linking result (claims_map.json)"}
    else:
        inp = leak_input(report, suspects)
        sha = sha_of(LEAK_PROMPT, MODEL, LEAK_SYSTEM, inp)
        out["leak"] = cached("leak", sha)
        if not out["leak"] and not suspects:
            out["leak"] = {"status": "ok", "judge": meta(LEAK_PROMPT, sha), "suspects": [], "in": [], "detail": [], "input": inp}
        elif not out["leak"]:
            ks = [s["k"] for s in suspects]
            got, raw, error = ask(LEAK_SYSTEM, json.dumps(inp, ensure_ascii=False, indent=1), leak_schema(),
                                  lambda r: aggregate_leak(r, ks, inp["conclusion"]))
            out["leak"] = {"status": "ok", "judge": meta(LEAK_PROMPT, sha), "suspects": ks, "in": got[0], "detail": got[1],
                           "raw": raw, "input": inp} if got else {"status": "failed", "judge": meta(LEAK_PROMPT, sha),
                                                                  "error": error, "input": inp}
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def load(run_dir):
    path = Path(run_dir) / "final_map.json"
    m = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    fas, leak = m.get("fas") or {}, m.get("leak") or {}
    return {"fas": {"match": fas["match"], "missing": fas["missing"]} if fas.get("status") == "ok" else None,
            "leak": {"suspects": leak["suspects"], "in": leak["in"]} if leak.get("status") == "ok" else None}
