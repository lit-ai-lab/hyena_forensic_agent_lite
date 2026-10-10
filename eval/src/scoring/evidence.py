import re
from dataclasses import dataclass

WINDOW_AFTER = 2500
RANK = {"none": 0, "list": 1, "read": 2}
NAME_END = re.compile(r"[^\n\"'|<>]{0,160}?\.[A-Za-z0-9]{2,5}(?=$|[\s\"'|,;)\]#*]|\\[nrt])")
HEADER_WORDS = {"", "file", "path", "name"}
FOLDER_ARG = re.compile(r"(?:Path|walk|listdir|scandir|chdir)\(\s*r?['\"]([^'\"\n]+)['\"]|-(?:Literal)?Path\s+['\"]([^'\"\n]+)['\"]")


@dataclass
class Evidence:
    id: str
    label: str
    path: re.Pattern
    text: re.Pattern
    ignore: re.Pattern
    place: bool = False
    lookup: bool = False


def compile_evidence(spec, prefix=""):
    ign = re.compile(spec["ignore"])
    return [Evidence(prefix + e["id"], e["label"], re.compile(e["path"]),
                     re.compile(e["text"]) if e.get("text") else None, ign,
                     bool(e.get("need_place")), bool(e.get("lookup"))) for e in spec["evidence"]]


def folder_before(text, pos):
    left = text[text.rfind("\n", 0, pos) + 1:pos]
    left = left[max(left.rfind('"'), left.rfind("'")) + 1:]
    if not left or left[-1] not in "\\/":
        return None
    parts = [p for p in re.split(r"[\\/]+", left) if p.strip()]
    return parts[-1].strip() if parts else ""


def at_header(text, pos):
    start = max(text.rfind("\n", 0, pos) + 1, text.rfind("\\n", 0, pos) + 2)
    rest = re.sub(r"[\s#\-=*<>:\[\]{}\"'|,.()]+", " ", text[start:pos]).strip().lower()
    return rest in HEADER_WORDS


def full_name(text, m):
    x = NAME_END.match(text, m.start())
    return (x.group(0) if x else m.group(0)).strip()


def marks_of(out, rx):
    found = []
    for m in rx.finditer(out):
        folder = folder_before(out, m.start())
        if folder is None and not at_header(out, m.start()):
            continue
        found.append((m.start(), full_name(out, m), folder))
    return found


def file_cuts(calls, rxs):
    return {c.t: sorted((p, n) for rx in rxs for p, n, _ in marks_of(c.out, rx)) for c in calls if c.ok}


def region(out, pos, name, cuts):
    end = min([p for p, n in cuts if p >= pos + len(name) and n.lower() != name.lower()] + [pos + WINDOW_AFTER])
    return out[pos:end]


def quote(text, m):
    return re.sub(r"(?:\\[nrt]|\s)+", " ", text[max(0, m.start() - 30):m.end() + 30]).strip() if m else ""


def cmd_folder(cmd):
    found = {re.split(r"[\\/]+", s.rstrip("\\/"))[-1] for a, b in FOLDER_ARG.findall(cmd) for s in [a or b]
             if not re.search(r"\.[A-Za-z0-9]{2,5}$|[#*]", s)}
    return found.pop() if len(found) == 1 else ""


def joinf(folder, name):
    return "%s\\%s" % (folder, name) if folder else name


def name_rx(name):
    return re.compile(r"(?<![\w.\-])" + re.escape(name) + r"(?![\w])", re.I)


def split_path(path):
    parts = [p for p in re.split(r"[\\/]+", path) if p]
    return parts[-1], (parts[-2] if len(parts) > 1 else "")


def _norm(s):
    return re.sub(r"\s+", " ", s.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", " ")).strip()


def echoed(m, out, cmd):
    s = _norm(out[max(0, m.start() - 12):m.end()])
    return len(s) >= 10 and s in _norm(cmd)


def hits_in(c, ev, cuts):
    if ev.lookup:
        return lookup_hits(c, ev)
    found = place_hits(c, ev, cuts)
    if not c.ok or ev.place or not ev.text:
        return found
    m = next((m for m in ev.text.finditer(c.out) if not echoed(m, c.out, c.cmd)), None)
    found = [h for h in found if h[0] == "list"]
    if m:
        marks = marks_of(c.out, ev.path)
        pm = ev.path.search(c.cmd)
        name = joinf(marks[0][2] or cmd_folder(c.cmd), marks[0][1]) if marks else \
            joinf(folder_before(c.cmd, pm.start()) or "", full_name(c.cmd, pm)) if pm else "(file name not printed)"
        found.append(("read", name, "fingerprint in output", quote(c.out, m)))
    return found


def lookup_hits(c, ev):
    if not c.ok or ev.ignore.search(c.cmd) or not ev.path.search(c.cmd):
        return []
    m = next((m for m in ev.text.finditer(c.out) if not echoed(m, c.out, c.cmd)), None) if ev.text else None
    return [("read", "(lookup)", "lookup input + output fingerprint", quote(c.out, m))] if m else [("list", "(lookup)", "lookup input", "")]


def place_hits(c, ev, cuts):
    if not c.ok:
        return []
    found = []
    for pos, name, folder in marks_of(c.out, ev.path):
        seg = region(c.out, pos, name, cuts.get(c.t, []))
        f = ev.text.search(seg) if ev.text else None
        how = "path in output" if folder else "name at output head"
        found.append(("read" if f else "list", joinf(folder or cmd_folder(c.cmd), name), how, quote(seg, f)))
    m = ev.path.search(c.cmd)
    if not found and m and not ev.ignore.search(c.cmd):
        f = ev.text.search(c.out) if ev.text else None
        found.append(("read" if f else "list", joinf(folder_before(c.cmd, m.start()) or "", full_name(c.cmd, m)),
                      "path in command", quote(c.out, f)))
    return found


def detect(calls, evs, cuts):
    return [(ev.id, c.t) + h for ev in evs for c in calls for h in hits_in(c, ev, cuts)]


def reach(reads, evs):
    out = {ev.id: {"level": "none", "at": None} for ev in evs}
    for eid, t, level, *_ in sorted(reads, key=lambda r: r[1]):
        if RANK[level] > RANK[out[eid]["level"]]:
            out[eid] = {"level": level, "at": t}
    return out


def read_rows(reads, evs):
    rows = []
    for ev in evs:
        per_file = {}
        for eid, t, level, f, how, q in sorted(reads, key=lambda r: r[1]):
            if eid != ev.id:
                continue
            r = per_file.setdefault(f, {"evidence": ev.id, "evidence_label": ev.label, "file": f, "first_path_call": t,
                                        "first_read_call": "", "read_calls": [], "how": how, "quote_text": ""})
            if level == "read":
                if not r["first_read_call"]:
                    r.update({"first_read_call": t, "how": how, "quote_text": q})
                if t not in r["read_calls"]:
                    r["read_calls"].append(t)
        found = list(per_file.values()) or [{"evidence": ev.id, "evidence_label": ev.label, "file": "-", "how": "not found"}]
        for r in found:
            r["read_calls"] = ";".join(map(str, r.get("read_calls", [])))
        rows += sorted(found, key=lambda r: r.get("first_read_call") or 10 ** 6)
    return rows


def link_cites(claims, evs, reach):
    rows = []
    for cl in claims:
        for cpath in cl["cites"]:
            base = {"claim": cl["k"], "claim_text": cl["text"], "cited_file": cpath}
            linked = [ev for ev in evs if ev.path.search(cpath)]
            for ev in linked:
                ok = reach[ev.id]["level"] == "read"
                rows.append(dict(base, **{"linked_evidence": ev.id, "status": "confirmed" if ok else "evidence_not_read",
                                         "evidence_read_call": reach[ev.id]["at"] if ok else ""}))
            if not linked:
                rows.append(dict(base, **{"linked_evidence": "-", "status": "outside_evidence_set", "evidence_read_call": ""}))
    return rows
