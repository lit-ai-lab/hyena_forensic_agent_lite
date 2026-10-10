import csv
import json
from pathlib import Path

from scoring import claims, evidence, final, load, metrics, values

EV_FIELDS = ["evidence", "evidence_label", "file", "first_path_call", "first_read_call", "read_calls", "how", "quote_text"]
LINK_FIELDS = ["claim", "claim_text", "cited_file", "linked_evidence", "evidence_read_call", "status"]
REL_FIELDS = ["claim", "claim_text", "attached_hypothesis", "cited_file", "verdict", "reason"]
CALL_FIELDS = ["call", "class", "ok", "basis"]


def score_run(run_dir, write=True):
    run_dir = Path(run_dir)
    q = load.question_of(run_dir)
    golds = load.load_golds()
    spec = golds[q]
    calls = load.load_calls(run_dir)
    report = load.load_report(run_dir)

    own = evidence.compile_evidence(spec)
    others = [ev for p, sp in golds.items() if p != q for ev in evidence.compile_evidence(sp, "Q%s " % p)]
    cite_rx = [evidence.name_rx(evidence.split_path(c)[0]) for cl in report["claims"] for c in cl["cites"]]
    cuts = evidence.file_cuts(calls, [ev.path for ev in own + others if not ev.lookup] + cite_rx)
    reads = evidence.detect(calls, own, cuts)
    reach = evidence.reach(reads, own)
    ehat = {eid for eid, r in reach.items() if r["level"] == "read"}
    links = evidence.link_cites(report["claims"], own, reach)

    vreads = values.off_reads(calls, {r["cited_file"] for r in links if r["linked_evidence"] == "-"})

    claim_map = claims.load(run_dir)
    rel = values.relations(report["claims"], links, claim_map, spec, vreads)
    S = values.proof(spec, claim_map, ehat, rel)
    wrong = values.wrong_cites(report["claims"], claim_map)
    on, off, call_why = values.call_class(reads, {c: v for c, v in vreads.items() if c in (wrong or ())})
    off = None if wrong is None else off

    T = len(calls)
    eps = metrics.episodes(off, on, T)
    n = metrics.cvu(rel)
    fin = final.load(run_dir)
    fas, fas_miss = metrics.fas_llm(fin)
    fas_kw, _ = metrics.fas(report, spec)
    ucr, unsupported, evaluated = metrics.ucr(rel)
    first_read = {r["at"] for r in reach.values() if r["level"] == "read"}
    result = {
        "run": run_dir, "question": q, "spec": spec, "report": report, "calls": calls,
        "reach": reach, "ehat": ehat, "links": links, "claim_map": claim_map, "relations": rel, "S": S,
        "vreads": vreads, "on": on, "off": off, "call_why": call_why, "episodes": eps,
        "metrics": {
            "FPS": metrics.fps(S, spec), "FAS": fas, "EBC": metrics.ebc(spec, ehat), "UCR": ucr,
            "RVR": metrics.rvr(n), "Assess.": metrics.assess(n),
            "Focus": metrics.ratio(len(on), T), "Drift": metrics.drift(off, T), "ToolErr": metrics.tool_err(calls),
            "Onset": metrics.onset(off, T), "Return": metrics.return_rate(eps, on), "Leak": metrics.leak_llm(fin),
            "FirstHit": metrics.first_hit(reach), "Time": metrics.elapsed(calls),
        },
        "fas_miss": fas_miss, "fas_kw": fas_kw, "final": fin, "cvu": n, "unsupported": unsupported, "evaluated": evaluated,
        "strip": "".join(("!" if not c.ok else "O" if c.t in on else "X" if off and c.t in off else ".")
                         + ("+" if c.t in first_read else "") for c in calls),
    }
    if write:
        result["files"] = [write_rows(run_dir / "evidence_reads.csv", EV_FIELDS, evidence.read_rows(reads, own)),
                           write_rows(run_dir / "cite_links.csv", LINK_FIELDS, links),
                           write_rows(run_dir / "call_class.csv", CALL_FIELDS, call_rows(calls, on, off, call_why))]
        off_json = run_dir / "off_calls.json"
        off_json.write_text(json.dumps(vreads, ensure_ascii=False, indent=2), encoding="utf-8")
        result["files"].append(off_json)
        if rel is not None:
            result["files"].append(write_rows(run_dir / "relations.csv", REL_FIELDS,
                                              relation_rows(report["claims"], rel, claim_map)))
    return result


def relation_rows(claims_, rel, claim_map):
    text = {cl["k"]: cl["text"] for cl in claims_}
    attached = lambda k: ",".join(claim_map["links"].get(k, [])) or "outside graph"
    rows = [{"claim": k, "claim_text": text[k], "attached_hypothesis": attached(k), "cited_file": c, "verdict": v, "reason": why}
            for k, c, v, why in rel]
    rows += [{"claim": cl["k"], "claim_text": cl["text"], "attached_hypothesis": "-", "cited_file": ";".join(cl["cites"]), "verdict": "-",
              "reason": "not a factual claim - not judged"} for cl in claims_ if cl["k"] in claim_map["non_fact"]]
    return sorted(rows, key=lambda r: r["claim"])


def call_rows(calls, on, off, why):
    kind = lambda t: "on" if t in on else "off" if off and t in off else "grey"
    return [{"call": c.t, "class": kind(c.t), "ok": "O" if c.ok else "X", "basis": ", ".join(why.get(c.t, []))} for c in calls]


def write_rows(path, fields, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def summary(result):
    m = result["metrics"]
    two = lambda x: "N/A" if x is None else "%.2f" % x
    whole = lambda x: "N/A" if x is None else "%d" % x
    S, n = result["S"], result["cvu"]
    cnt = lambda v: "N/A" if n is None else "%d" % n[v]
    return {
        "Prompt": result["report"].get("prompt_version", "-"), "SpecVersion": result["spec"].get("ruleset_version", "-"),
        "FPS": whole(m["FPS"]), "FAS": whole(m["FAS"]), "EBC": two(m["EBC"]), "UCR": two(m["UCR"]),
        "RVR": two(m["RVR"]), "Assess.": two(m["Assess."]),
        "C_on": cnt("C_on"), "V_on": cnt("V_on"), "C_off": cnt("C_off"), "V_off": cnt("V_off"), "U": cnt("U"),
        "Focus": two(m["Focus"]), "Drift": two(m["Drift"]),
        "ToolErr": two(m["ToolErr"]), "Onset": two(m["Onset"]), "Return": two(m["Return"]), "Leak": whole(m["Leak"]),
        "FirstHit": whole(m["FirstHit"]),
        "Time": "N/A" if m["Time"] is None else "%.0fs" % m["Time"],
        "Required": "N/A" if S is None else str([S[h["id"]] for h in result["spec"]["hypotheses"]]),
        "Trace": result["strip"],
    }
