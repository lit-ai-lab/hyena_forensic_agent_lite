import re
from datetime import datetime

EPISODE_GAP = 1


def ratio(a, b):
    return a / b if b else None


def fps(S, spec):
    return None if S is None else int(all(S[h["id"]] for h in spec["hypotheses"]))


def ebc(spec, ehat):
    req = spec["hypotheses"]
    return sum(len(set(h["supported_by"]) & ehat) / len(h["supported_by"]) for h in req) / len(req)


def fas(report, spec):
    crit = spec["answer"]["criteria"]
    hit = lambda t, c: any(re.search(p, t) for p in c["any"])
    best = max([c["text"] for c in report["claims"]] + [report["conclusion"]], key=lambda t: sum(hit(t, c) for c in crit))
    miss = [c["id"] for c in crit if not hit(best, c)]
    return int(not miss), miss


VALUES = ("C_on", "V_on", "C_off", "V_off", "U")


def cvu(rel):
    return None if rel is None else {v: sum(1 for _, _, x, _ in rel if x == v) for v in VALUES}


def rvr(n):
    if n is None:
        return None
    bad = n["V_on"] + n["V_off"]
    return ratio(bad, bad + n["C_on"] + n["C_off"])


def assess(n):
    if n is None:
        return None
    judged = sum(n[v] for v in VALUES if v != "U")
    return ratio(judged, judged + n["U"])


def ucr(rel):
    if rel is None:
        return None, 0, 0
    facts = {k for k, *_ in rel}
    unsupported = facts - {k for k, _, v, _ in rel if v in ("C_on", "C_off")}
    return ratio(len(unsupported), len(facts)), len(unsupported), len(facts)


def tool_err(calls):
    return ratio(sum(not c.ok for c in calls), len(calls))


def drift(off, T):
    return None if off is None else ratio(len(off), T)


def onset(off, T):
    return min(off) / T if off else None


def episodes(off, on, T, gap=EPISODE_GAP):
    if off is None:
        return None
    out, cur = [], []
    for t in range(1, T + 1):
        if t in off:
            cur.append(t)
        elif t in on:
            if cur:
                out.append(cur)
            cur = []
        elif cur:
            nxt = next((x for x in sorted(off) if x > t), None)
            if nxt is None or (nxt - cur[-1] - 1) > gap or any(x in on for x in range(t, nxt)):
                out.append(cur)
                cur = []
    if cur:
        out.append(cur)
    return out


def return_rate(eps, on):
    return None if eps is None else ratio(sum(1 for e in eps if any(t > e[-1] for t in on)), len(eps))


def fas_llm(final):
    f = final["fas"]
    return (None, []) if f is None else (int(f["match"]), f["missing"])


def leak_llm(final):
    leak = final["leak"]
    return None if leak is None else int(bool(leak["in"]))


def first_hit(reach):
    hits = [r["at"] for r in reach.values() if r["level"] == "read"]
    return min(hits) if hits else None


def elapsed(calls):
    stamps = sorted(c.ts for c in calls if c.ts)
    if len(stamps) < 2:
        return None
    parse = lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))
    return (parse(stamps[-1]) - parse(stamps[0])).total_seconds()
