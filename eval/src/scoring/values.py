import functools
import re

from scoring import evidence
from scoring.evidence import split_path
from utils import gate0

FRAG = 10
MAIL = (".pst", ".ost")
MAIL_MIN = 30
MAIL_RATIO = 0.05


def relations(claims, link_rows, claim_map, spec, vreads):
    if claim_map is None:
        return None
    D = {n["id"]: set(n["supported_by"]) for n in spec["hypotheses"] + spec.get("aux", [])}
    state = {}
    for r in link_rows:
        if r["linked_evidence"] != "-":
            state.setdefault((r["claim"], r["cited_file"]), {})[r["linked_evidence"]] = r["status"]
    out = []
    for cl in claims:
        k = cl["k"]
        if k in claim_map["non_fact"]:
            continue
        if not cl["cites"]:
            out.append((k, "", "U", "no citation"))
            continue
        hs = claim_map["links"].get(k, [])
        want = set().union(*(D[h] for h in hs))
        for c in cl["cites"]:
            linked = state.get((k, c), {})
            if not hs:
                out.append((k, c) + opened(linked, vreads.get(c)))
                continue
            good = sorted(e for e in linked if e in want)
            ok = [e for e in good if linked[e] == "confirmed"]
            if ok:
                out.append((k, c, "C_on", "hypothesis %s, evidence %s" % (",".join(hs), ",".join(ok))))
            elif good:
                out.append((k, c, "V_on", "(3) hypothesis %s, evidence %s, but no record of reading that evidence" % (",".join(hs), ",".join(good))))
            elif linked:
                out.append((k, c, "V_on", "(1) evidence of another hypothesis (%s)" % ",".join(sorted(linked))))
            else:
                out.append((k, c, "V_on", "(2) citation for %s is a file outside the evidence set" % ",".join(hs)))
    return out


def opened(linked, v):
    if linked:
        ok = sorted(e for e in linked if linked[e] == "confirmed")
        return ("C_off", "outside graph · read evidence %s of this question" % ",".join(ok)) if ok else \
               ("V_off", "outside graph · cited without reading evidence %s of this question" % ",".join(sorted(linked)))
    called = sorted(set(v["named_calls"]) | set(v["read_calls"])) if v else []
    if called:
        return "C_off", "outside graph · opened by calls %s" % ",".join(map(str, called))
    return "V_off", "outside graph · file not in the evidence set and its name appears in no call (hallucinated name)" if not v or v["source"] is None else \
                    "outside graph · name appears in no successful call (cited without opening)"


def proof(spec, claim_map, ehat, rel):
    if claim_map is None:
        return None
    S = {}
    for n in spec["hypotheses"] + spec.get("aux", []):
        mine = [v for k, _, v, _ in rel if v != "U" and n["id"] in claim_map["links"].get(k, [])]
        all_violate = bool(mine) and all(v == "V_on" for v in mine)
        S[n["id"]] = int(bool(claim_map["asserted"].get(n["id"])) and bool(set(n["supported_by"]) & ehat)
                         and not all_violate)
    return S


def source_of(cpath):
    m = re.search(r"WM_HYENA_TEST[\\/](.+)", cpath, re.I)
    tail = m.group(1) if m else "/".join(p for p in split_path(cpath)[::-1] if p)
    tail = "/" + tail.replace("\\", "/").lower()
    return next((f for f in gate0.files() if f.lower().endswith(tail)), None)


def squash(s):
    return re.sub(r"\s+", "", s)


def pieces(text):
    o = squash(text)
    return {o[i:i + FRAG] for i in range(len(o) - FRAG + 1)}


def scan(path, index):
    got, stamp = gate0.body(path)
    if stamp is None:
        return set(), 0
    text = squash(re.sub(r"<[^>]+>", "", got[:-len(stamp) - 1]))
    return {w for i in range(len(text) - FRAG + 1) if (w := text[i:i + FRAG]) in index}, len(text)


@functools.cache
def all_paths():
    return squash("\n".join(gate0.files()).lower())


def keep(piece):
    return bool(re.search(r"[^\W\d_]", piece)) and piece.lower() not in all_paths()


def off_reads(calls, vfiles):
    ok = [c for c in calls if c.ok]
    out = {}
    for cpath in sorted(vfiles):
        src = source_of(cpath)
        name = evidence.name_rx(split_path(cpath)[0])
        named = [c.t for c in ok if name.search(c.cmd) or name.search(c.out)]
        mail = bool(src) and src.lower().endswith(MAIL)
        wins = {c.t: pieces(c.out) for c in ok if mail or c.t in named}
        matched, size = scan(src, set().union(*wins.values())) if src else (set(), 0)
        hits = {}
        for t, w in wins.items():
            need = max(MAIL_MIN, MAIL_RATIO * len(w)) if mail else 1
            cand, found = sorted(w & matched), []
            for i, p in enumerate(cand):
                if len(found) + len(cand) - i < need:
                    break
                if keep(p):
                    found.append(p)
                    if len(found) >= max(need, 3):
                        break
            if len(found) >= need:
                hits[t] = found[:3]
        out[cpath] = {"source": src, "text_chars": size, "matched_pieces": len(matched), "read_calls": hits, "named_calls": named}
    return out


def wrong_cites(claims, claim_map):
    if claim_map is None:
        return None
    return {c for cl in claims if cl["k"] in claim_map["outside"] for c in cl["cites"]}


def call_class(own_reads, vreads):
    on, why = set(), {}
    for eid, t, level, *_ in own_reads:
        if level == "read":
            on.add(t)
            if eid not in why.setdefault(t, []):
                why[t].append(eid)
    off = set()
    for cpath, v in vreads.items():
        name = split_path(cpath)[0]
        for t in v["read_calls"]:
            if name not in why.setdefault(t, []):
                why[t].append(name)
            if t not in on:
                off.add(t)
    return on, off, why
