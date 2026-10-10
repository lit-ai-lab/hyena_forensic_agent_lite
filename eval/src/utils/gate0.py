import sys, io, os, re, glob, zipfile, datetime, subprocess, hashlib, functools, shutil
import yaml

ROOT = os.environ.get("HYENA_ROOT") or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "HYENA_CTF"))
READPST = os.environ.get("READPST", "readpst")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mailcache")
ZIPEXT = (".docx", ".xlsx", ".pptx")


def mail(path):
    tag = hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]
    out = os.path.join(CACHE, "%s-%s" % (re.sub(r"[^\w.]", "_", os.path.basename(path)), tag))
    if not os.path.isdir(out):
        if not shutil.which(READPST):
            return None
        os.makedirs(out, exist_ok=True)
        subprocess.run([shutil.which(READPST), "-o", out, "-r", path], capture_output=True, timeout=900)
    box = glob.glob(out + "/**/mbox", recursive=True)
    return "".join(open(p, "rb").read().decode("utf-8", "ignore") for p in box) or None


def cells(path):
    try:
        import xlrd
    except ImportError:
        return None
    wb = xlrd.open_workbook(path)
    return "\n".join("|".join(str(sh.cell_value(r, c)) for c in range(sh.ncols))
                     for sh in wb.sheets() for r in range(sh.nrows))


def pdf(path):
    try:
        import pypdf
    except ImportError:
        return None
    r = pypdf.PdfReader(path)
    meta = " ".join("%s=%s" % (k, v) for k, v in (r.metadata or {}).items())
    text = "\n".join(p.extract_text() or "" for p in r.pages)
    return (meta + "\n" + text).strip() or None


def exif(path):
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS
    except ImportError:
        return None
    ex = Image.open(path)._getexif() or {}
    named = {TAGS.get(k, k): v for k, v in ex.items()}
    gps = {GPSTAGS.get(k, k): v for k, v in (named.get("GPSInfo") or {}).items()}
    out = ["%s=%s" % (k, v) for k, v in named.items() if k != "GPSInfo"]
    if gps.get("GPSLatitude"):
        def dd(t, ref):
            d = [float(x) for x in t]
            v = d[0] + d[1] / 60 + d[2] / 3600
            return -v if ref in ("S", "W") else v
        out.append("GPS=%.6f, %.6f" % (dd(gps["GPSLatitude"], gps.get("GPSLatitudeRef")),
                                       dd(gps["GPSLongitude"], gps.get("GPSLongitudeRef"))))
    return " ".join(out) or None


def body(path):
    low = path.lower()
    try:
        if low.endswith(ZIPEXT):
            with zipfile.ZipFile(path) as z:
                got = " ".join(z.read(n).decode("utf-8", "ignore")
                               for n in z.namelist() if n.endswith((".xml", ".rels")))
        elif low.endswith((".pst", ".ost")):
            got = mail(path)
        elif low.endswith(".xls"):
            got = cells(path)
        elif low.endswith(".pdf"):
            got = pdf(path)
        elif low.endswith((".jpg", ".jpeg")):
            got = exif(path)
        else:
            raw = open(path, "rb").read()
            for e in ("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8", "cp949"):
                try:
                    got = raw.decode(e)
                    break
                except UnicodeDecodeError:
                    pass
            else:
                got = "".join(raw.decode(e, "ignore") for e in ("utf-8", "utf-16-le", "cp949"))
    except Exception as ex:
        return "exception: %s" % ex, None
    if got is None:
        return None, None
    st = os.stat(path)
    stamp = " ".join(datetime.datetime.fromtimestamp(t).isoformat(" ")
                     for t in (st.st_mtime, st.st_ctime))
    return got + " " + stamp, stamp


@functools.cache
def files():
    return [os.path.join(root, n).replace(os.sep, "/") for root, _, names in os.walk(ROOT) for n in names]


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    print("%d files in the evidence set" % len(files()))

    targets = sys.argv[1:] or sorted(glob.glob("answer_key/Q*/q*.yaml"))
    fails = []

    for spec_path in targets:
        spec = yaml.safe_load(open(spec_path, encoding="utf-8"))
        print("\n=== %s (question %s) ===" % (spec_path, spec.get("problem")))
        for e in spec["evidence"]:
            rx = re.compile(e["path"])
            hit = [f for f in files() if rx.search(f)]
            names = sorted(set(os.path.basename(x) for x in hit))
            if not hit and e.get("reach") == "REQUIRES_EXTERNAL_LOOKUP":
                print("SKIP %-4s %-30s external lookup - no file in the evidence set" % (e["id"], e["label"][:28]))
                continue
            if not hit:
                fails.append("%s %s: path matches no file" % (spec_path, e["id"]))
            print("%s %-4s %-30s %3d files  %s" % ("OK " if hit else "FAIL", e["id"],
                                               e["label"][:28], len(hit), " · ".join(names[:3])))
            if not e.get("text"):
                continue
            trx = re.compile(e["text"])
            found, unread = [], 0
            for f in hit:
                got, _ = body(f)
                if got is None:
                    unread += 1
                elif trx.search(got):
                    found.append(os.path.basename(f))
            if found:
                print("        text %d/%d files matched: %s" % (len(found), len(hit), " · ".join(sorted(set(found))[:3])))
            elif unread == len(hit):
                print("        text SKIP - no way to read these files (%d files)" % unread)
            else:
                print("        FAIL text - not found in any file")
                fails.append("%s %s: text is not in the matched files" % (spec_path, e["id"]))

    print("\n" + "=" * 64)
    if fails:
        print("%d failures" % len(fails))
        for f in fails:
            print("  ✗", f)
        raise SystemExit(1)
    print("all passed")


if __name__ == "__main__":
    main()
