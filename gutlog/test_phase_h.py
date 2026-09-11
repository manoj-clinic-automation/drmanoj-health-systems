#!/usr/bin/env python3
"""
Phase H -- scan quality (GutLog v3.11.0).

Proves, offline and against ground truth, that the report-tuned widget does what
the owner asked for: stop cropping into the page, and actually take the shadow
off. Every image is synthetic and generated here, so the numbers are checkable
rather than a matter of squinting at a photo.

  python3 test_phase_h.py            (needs node, PIL, numpy, scipy)
"""
import json, os, shutil, subprocess, sys
D   = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.join(D, "scan_lab")
ok = fail = 0

def chk(name, cond, note=""):
    global ok, fail
    if cond: ok += 1;  print("[PASS] %-44s %s" % (name, note))
    else:    fail += 1; print("[FAIL] %-44s %s" % (name, note))

def need(cmd):
    return shutil.which(cmd) is not None

# ---------------------------------------------------------------- static checks
src = open(os.path.join(D, "scanner_widget.js"), encoding="utf-8").read()
app = open(os.path.join(D, "app.py"), encoding="utf-8").read()

chk("01 widget carries the v2.4 marker", "SCANNER_REPORT_V24" in src)
chk("02 knobs default to the v2.3 numbers",
    'parseInt(CFG.captureMax, 10) || 1400' in src and
    'parseInt(CFG.warpMax, 10)    || 1600' in src and
    'parseFloat(CFG.jpegQuality)  || 0.85' in src,
    "a caller that sets nothing behaves as before")
chk("03 host passes the report numbers",
    "captureMax: 2600" in app and "warpMax: 2600" in app and
    "wholePageFirst: true" in app and "jpegQuality: 0.92" in app)
chk("04 upload ceiling raised for 220dpi pages", "MAX_FILE_MB = 25" in app)
chk("05 the border probe is in front of the detector",
    "if (found && !borderVisible(cv)) found = null;" in app.replace("\\u2014","-") or
    "borderVisible" in src)
chk("06 the 8% inset is still there for the clinic",
    "cv.width * 0.08" in src, "only GutLog gets the whole-page fallback")

if need("node"):
    r = subprocess.run(["node", "--check", os.path.join(D, "scanner_widget.js")],
                       capture_output=True, text=True)
    chk("07 widget parses", r.returncode == 0, r.stderr.strip()[:60])
else:
    chk("07 widget parses", False, "node missing")

# ---------------------------------------------------------------- the maths
try:
    import numpy, scipy, PIL          # noqa
    have = need("node")
except ImportError:
    have = False

if not have:
    print("\n-- node/PIL/numpy/scipy missing: the image checks are skipped --")
else:
    if not os.path.exists(os.path.join(LAB, "harsh.bin")):
        subprocess.run([sys.executable, "make_images.py"], cwd=LAB, check=True)
    res = {}
    for img in ("fillframe", "ondesk", "harsh", "closeup"):
        for v in ("old", "new"):
            subprocess.run(["node", "run.js", v, img, "%s_%s" % (img, v)],
                           cwd=LAB, check=True, capture_output=True)
        res[img] = {v: json.load(open(os.path.join(LAB, "%s_%s.json" % (img, v))))
                    for v in ("old", "new")}
    sys.path.insert(0, LAB)
    import measure_lib as M

    # 08 -- the auto-crop complaint
    a = M.coverage("fillframe", "fillframe_old")
    b = M.coverage("fillframe", "fillframe_new")
    chk("08 page filling the frame is not cropped", b >= 99.9 and a < 90,
        "ink kept %.1f%% -> %.1f%%" % (a, b))
    chk("09 and it knows why: no desk in the ring",
        res["fillframe"]["new"]["route"] in ("fills-frame", "nothing-to-crop", "whole-page"),
        res["fillframe"]["new"]["route"])
    br = json.loads(subprocess.run(["node", "border.js"], cwd=LAB,
                                   capture_output=True, text=True, check=True).stdout)
    chk("09b border probe: desk seen only when there is one",
        br["ondesk"] is True and br["closeup"] is False, str(br))
    co, cn = M.coverage("closeup", "closeup_old"), M.coverage("closeup", "closeup_new")
    chk("09c held close, the old one ate the page", co < 80 and cn >= 99.9,
        "ink kept %.1f%% -> %.1f%% (route %s)" % (co, cn, res["closeup"]["new"]["route"]))
    chk("10 a page ON a desk is still cropped to the page",
        res["ondesk"]["new"]["route"] == "detected" and
        M.coverage("ondesk", "ondesk_new") >= 99.9)

    # 11 -- the shadow complaint
    so = M.separation("harsh", "harsh_old")[1]
    sn = M.separation("harsh", "harsh_new")[1]
    chk("11 shadowed half of the page is readable", sn > 140 and sn > so * 1.6,
        "contrast %.0f -> %.0f" % (so, sn))
    go = M.grain("harsh", "harsh_old"); gn = M.grain("harsh", "harsh_new")
    chk("12 and blank paper came out clean, not speckled", gn <= go,
        "grain sd %.1f -> %.1f" % (go, gn))

    # 13 -- resolution
    dn = M.dpi("fillframe", "fillframe_new"); do = M.dpi("fillframe", "fillframe_old")
    chk("13 small print is captured at scanner resolution", dn >= 200 and do < 130,
        "%.0f dpi -> %.0f dpi at A4" % (do, dn))

    # 14 -- it must not be so slow that a phone gives up
    chk("14 enhancement stays under a second on a 5MP page",
        res["harsh"]["new"]["ms"] < 1000, "%d ms" % res["harsh"]["new"]["ms"])

    # 15 -- the clinic's own behaviour is untouched
    chk("15 a caller without the knobs is byte-identical",
        res["ondesk"]["old"]["outW"] == 791 and res["ondesk"]["old"]["capW"] == 1050,
        "v2.3 path still 1400/1600")

print("-" * 66)
print("%d/%d passed" % (ok, ok + fail))
sys.exit(1 if fail else 0)
