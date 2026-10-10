import argparse
import io
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from parser.jsonl import extract_json, make_call_csv, user_text
from parser.report import prompt_version, write as write_report
from parser.session import resolve_rollouts
from parser.traj import convert as convert_traj
from scoring import claims, final, load
from scoring.pipeline import score_run, summary

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
OUTPUT = REPO / os.environ.get("HYENA_OUTPUT", "output")
QUESTION_RE = re.compile(r"^Q0*(\d+)$")
METRICS = ["Prompt", "SpecVersion", "FPS", "FAS", "EBC", "UCR", "RVR", "Assess.", "C_on", "V_on", "C_off", "V_off", "U",
           "Focus", "Drift", "ToolErr", "Onset", "Return", "Leak", "FirstHit", "Time"]


def problem_of(jsonl: Path, root: Path) -> str:
    return next((part for part in jsonl.parts if QUESTION_RE.search(part)), root.name)


def why(error) -> str:
    return str(error) if isinstance(error, SystemExit) else "%s: %s" % (type(error).__name__, error)


def parse(folder: Path) -> list:
    print("=== Parsing: %s" % folder)
    by_attempt = defaultdict(list)
    for jsonl in sorted(folder.rglob("*.jsonl")):
        by_attempt[jsonl.parent].append(jsonl)
    failed = []
    for attempt, paths in sorted(by_attempt.items()):
        try:
            for row in resolve_rollouts(paths):
                jsonl = row["path"]
                if row["role"] == "approval_child":
                    print(f"{jsonl} -> skipped (approval-review session)")
                    continue
                out = OUTPUT / problem_of(jsonl, folder) / attempt.name / (jsonl.stem + ".csv")
                make_call_csv(extract_json(jsonl), out)
                print(f"{jsonl} -> {out}")
                if row["final_answer"]:
                    report = write_report(row["final_answer"], out.with_suffix(".report.json"),
                                          prompt_version=prompt_version(user_text(jsonl)))
                    print(f"  + report {report.get('prompt_version', '')} {report['report_format']} · {len(report['claims'])} claims")
        except (SystemExit, Exception) as error:
            failed.append(attempt)
            print(f"{attempt} -> parse failed: {why(error)}")
    return failed


def parse_traj(folder: Path) -> list:
    print("=== Converting: %s" % folder)
    failed = []
    for path in sorted(folder.glob("q*/r*.traj.json"), key=lambda p: (int(re.search(r"q(\d+)", p.parent.name).group(1)), p.name)):
        q, r = re.search(r"q(\d+)", path.parent.name).group(1), re.search(r"r(\d+)", path.name).group(1)
        out = OUTPUT / ("Q%s" % q) / ("%02d" % int(r))
        try:
            info = convert_traj(path, out)
            print("%s/%s -> %s  %d calls (%d report-writing/submit calls excluded) · %d claims · %s" % (
                path.parent.name, path.name, out.relative_to(REPO).as_posix(), info["calls"], info["excluded"], info["claims"], info["format"]))
        except (SystemExit, Exception) as error:
            failed.append(path)
            print("%s/%s -> conversion failed: %s" % (path.parent.name, path.name, why(error)))
    return failed


def runs(only: list) -> list:
    out = []
    for d in sorted(OUTPUT.glob("Q*/*")):
        if not (list(d.glob("rollout-*.csv")) and list(d.glob("*.report.json"))):
            continue
        if only and not any(re.match(r"Q0*%s$" % q, d.parent.name) for q in only):
            continue
        out.append(d)
    return out


def score(folder: Path) -> dict:
    rel = folder.relative_to(REPO).as_posix()
    try:
        return {"Run": rel, **summary(score_run(folder))}
    except (SystemExit, Exception) as error:
        return {"Run": rel, "Error": why(error)[:60]}


def write_scores(rows: list) -> None:
    import csv
    fields = ["Run"] + METRICS + ["Required", "Trace", "Error"]
    by_question = {}
    for r in rows:
        by_question.setdefault((REPO / r["Run"]).parent, []).append(r)
    for qdir, group in by_question.items():
        out = qdir / "scores.csv"
        with out.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(group)
        print("Score file: %s (%d rows)" % (out.relative_to(REPO), len(group)))


def write_all() -> None:
    import csv
    fields = ["Question", "Attempt", "Run"] + METRICS + ["Required", "Trace", "Error"]
    rows = []
    for path in OUTPUT.glob("Q*/scores.csv"):
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                rows.append({"Question": int(re.match(r"Q(\d+)", path.parent.name).group(1)), "Attempt": r["Run"].rsplit("/", 1)[-1], **r})
    rows.sort(key=lambda r: (r["Question"], r["Attempt"]))
    out = OUTPUT / "scores_all.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print("Combined score file: %s (%d rows)" % (out.relative_to(REPO), len(rows)))


def table(rows: list) -> None:
    cols = ["Run"] + METRICS + ["Required"]
    width = {c: max(len(c), *(len(str(r.get(c, "-"))) for r in rows)) for c in cols}
    print("\n" + " | ".join(c.ljust(width[c]) for c in cols))
    print("-+-".join("-" * width[c] for c in cols))
    for r in rows:
        if "Error" in r:
            print(r["Run"].ljust(width["Run"]), "| error:", r["Error"])
            continue
        print(" | ".join(str(r.get(c, "-")).ljust(width[c]) for c in cols))


def judge(found: list, force: bool) -> None:
    print("=== Claim linking")
    golds = load.load_golds()
    for d in found:
        rel = d.relative_to(REPO).as_posix()
        try:
            res = claims.judge(d, load.load_report(d), golds[load.question_of(d)], force)
        except (SystemExit, Exception) as error:
            print("%s: skipped - %s" % (rel, why(error)))
            continue
        print("%s: %s" % (rel, res["status"]) + ("" if res["status"] == "ok" else " - %s" % res.get("error")))
    print("=== Final judgment (FAS · Leak)")
    for d in found:
        rel = d.relative_to(REPO).as_posix()
        try:
            res = final.judge(d, load.load_report(d), golds[load.question_of(d)], force)
        except (SystemExit, Exception) as error:
            print("%s: skipped - %s" % (rel, why(error)))
            continue
        bad = {k: v.get("error") or v.get("reason") for k, v in res.items() if v["status"] != "ok"}
        print("%s: FAS %s · Leak %s" % (rel, "ok" if "fas" not in bad else "N/A - %s" % bad["fas"],
                                         "ok" if "leak" not in bad else "N/A - %s" % bad["leak"]))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Parse the jsonl files in data/, write csv and report.json to output/, score them against the per-question specs, and save the table and scores.csv.",
        epilog="e.g. python src/main.py            parse all of data + score\n"
               "    python src/main.py 3 6        parse everything, score only questions 3 and 6\n"
               "    python src/main.py --no-parse score only, if already parsed\n"
               "    python src/main.py 1 --judge  link claims to hypotheses (LLM) for question 1, then score",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("questions", nargs="*", metavar="N",
                    help="question numbers to score (output/Q<N>). All if omitted. Parsing always covers all of data regardless")
    ap.add_argument("--no-parse", action="store_true",
                    help="skip parsing and score only from the csv and report.json already in output/")
    ap.add_argument("--traj", metavar="DIR",
                    help="convert and score a mini-swe-agent result folder (DIR/q<N>/r<R>.traj.json) instead of data/")
    ap.add_argument("--judge", action="store_true",
                    help="before scoring, run the LLM claim-to-hypothesis judgment and write claims_map.json (needs API_KEY in .env). "
                         "If the same input was already judged, it is not called again")
    ap.add_argument("--force", action="store_true",
                    help="with --judge: ignore the existing claims_map.json and judge again")
    args = ap.parse_args()
    try:
        OUTPUT.resolve().relative_to(REPO)
    except ValueError:
        raise SystemExit("HYENA_OUTPUT must be a path inside the repository: %s" % OUTPUT)
    failed = [] if args.no_parse else parse_traj(Path(args.traj)) if args.traj else parse(REPO / "data")
    found = runs(args.questions)
    if not found:
        print("No runs to score - output/Q<N>/NN/ must contain a csv and report.json.")
        return 1
    if args.judge:
        judge(found, args.force)
    rows = [score(d) for d in found]
    table(rows)
    write_scores(rows)
    write_all()
    bad = [r for r in rows if "Error" in r]
    print("\n%d runs · %d scoring failures%s" % (len(rows), len(bad), " · %d parse failures" % len(failed) if failed else ""))
    return 1 if bad or failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
