import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
csv.field_size_limit(2 ** 31 - 1)

def nfc(s):
    s = re.sub(r"(?<!\\)\\\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s or "")
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    return unicodedata.normalize("NFC", s.replace("\\\\", "\\"))


@dataclass
class Call:
    t: int
    cmd: str
    out: str
    ok: bool
    ts: str


def only(run_dir, pattern):
    found = sorted(Path(run_dir).glob(pattern))
    if len(found) != 1:
        raise SystemExit("%s: pattern %s matched %d files - keep exactly one run per attempt folder" % (run_dir, pattern, len(found)))
    return found[0]


def question_of(run_dir):
    m = re.search(r"(?:^|[\\/])Q0*(\d+)(?:[\\/]|$)", str(run_dir))
    if not m:
        raise SystemExit("cannot find the question number in %s - the path must look like output/Q<N>/<attempt>" % run_dir)
    return int(m.group(1))


def load_calls(run_dir):
    with only(run_dir, "rollout-*.csv").open(encoding="utf-8-sig", newline="") as fh:
        return [Call(int(r["call_no"]), nfc(r["input"]), nfc(r["output"]), r["verdict"] != "fail", r.get("ts", ""))
                for r in csv.DictReader(fh)]


def load_report(run_dir):
    rep = json.loads(only(run_dir, "rollout-*.report.json").read_text(encoding="utf-8"))
    rep["claims"] = [{"k": k, "text": nfc(b["claim"]), "cites": [nfc(c["path"]) for c in b["cites"]]}
                     for k, b in enumerate(rep["claims"], 1)]
    rep["conclusion"] = nfc(rep.get("conclusion", ""))
    return rep


def load_golds():
    specs = (yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((REPO / "answer_key").glob("Q*/q*.yaml")))
    return {spec["problem"]: spec for spec in specs}
