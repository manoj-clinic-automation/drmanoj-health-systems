#!/usr/bin/env python3
"""
patch_gutlog_v3110.py -- GutLog v3.11.0  SCAN QUALITY

Why: the scanner in GutLog is the clinic's widget (S219 v2.3, the newest of the
three versions), and it was tuned for pharmacy bills -- half-A4, large print,
lying on a desk. A pathology report is the opposite: 8pt print on a full A4
page, usually held close enough that the page fills the frame. Two things went
wrong there, both reported by the owner:

  auto-crop        with no desk visible the edge fit has nothing to sit on, so
                   the brightest "document" it can find is a block of printing,
                   and the outline landed inside the page. When it gave up
                   instead, the 8% inset fallback cut past the margin into the
                   text.
  shadow removal   the flattening window was min-side/8, which at report
                   resolution is wider than the shadow it is meant to remove;
                   blank paper was divided by its own local mean, which turns
                   paper grain into speckle; and the final stretch used the
                   min and max, so one staple set the black point and the print
                   came out pale.

Measured offline on synthetic report photographs (scan_lab/):

  page fills the frame   ink kept   82.4% -> 100%     (the 8% inset was cutting text)
  strong shadow          contrast, shadowed half  71.8 -> 168.9
  every case             resolution  ~100-118 dpi -> 184-220 dpi at A4

This patch changes app.py only (three edits). The widget itself is a separate
file and is deployed by copy.

Idempotent (MARKER), PREV-gated, anchor-verified, compile-checked, .bak before
write, self-restoring, --check.
"""
import hashlib, os, py_compile, shutil, sys, time

MARKER = "GUTLOG_V3110_SCANQ"
PREV   = "GUTLOG_V3100_AUTOREAD"
TARGET = "app.py"
CHECK  = "--check" in sys.argv

EDITS = [
 ("upload-ceiling",
  "MAX_FILE_MB = 12",
  "MAX_FILE_MB = 25   # v3.11.0: report pages are saved at ~220dpi now, not ~110"),

 ("scanner-config",
  """window.SCANNER_CONFIG = {title: "Scan a report", uploadUrl: "/api/upload", fileField: "file",
  uploadFields: {ftype: "Lab report", day: "__DAY__"}, nameBase: "Lab_report", backUrl: "/?open=records",
  allowIdCard: false, allowBatch: true};""",
  """window.SCANNER_CONFIG = {title: "Scan a report", uploadUrl: "/api/upload", fileField: "file",
  uploadFields: {ftype: "Lab report", day: "__DAY__"}, nameBase: "Lab_report", backUrl: "/?open=records",
  allowIdCard: false, allowBatch: true,
  /* v3.11.0 -- a report is not a bill.
     captureMax/warpMax  1400/1600 put an A4 page at ~110dpi, which is where an
                         OCR starts guessing at 8pt print. 2600 is ~220dpi.
     wholePageFirst      the whole page is what a report scan is FOR, so it is
                         the big button and the crop is the small one; and when
                         the edges are not obvious the whole photo is kept
                         instead of an 8% inset that cuts into the print. */
  captureMax: 2600, warpMax: 2600, jpegQuality: 0.92, wholePageFirst: true};"""),

 ("schema-marker",
  'GUTLOG_V390_SCAN GUTLOG_V3100_AUTOREAD\n',
  'GUTLOG_V390_SCAN GUTLOG_V3100_AUTOREAD GUTLOG_V3110_SCANQ\n'),
]

def main():
    src = open(TARGET, encoding="utf-8").read()
    if MARKER in src:
        print("already patched (%s)" % MARKER); return 0
    if PREV not in src:
        print("REFUSING: %s not present -- apply the previous patch first" % PREV); return 2
    for name, anchor, _ in EDITS:
        n = src.count(anchor)
        if n != 1:
            print("REFUSING: anchor '%s' found %d times" % (name, n)); return 2
    print("all %d anchors verified" % len(EDITS))
    if CHECK: return 0
    out = src
    for name, anchor, new in EDITS:
        out = out.replace(anchor, new, 1)
    # GutLog house rule: no Jinja tokens may appear in anything we add
    for tok in ("{{", "}}", "{%", "%}", "{#", "#}"):
        for _, _, new in EDITS:
            if tok in new:
                print("REFUSING: Jinja token %s in new code" % tok); return 2
    tmp = TARGET + ".tmp"
    open(tmp, "w", encoding="utf-8").write(out)
    try:
        py_compile.compile(tmp, doraise=True)
    except Exception as e:
        print("REFUSING: syntax error %s" % e); os.remove(tmp); return 2
    bak = "%s.bak-v3110-%s" % (TARGET, time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(TARGET, bak); shutil.move(tmp, TARGET)
    print("patched. backup %s  md5 %s" % (bak, hashlib.md5(out.encode()).hexdigest()))
    return 0

sys.exit(main())
