#!/usr/bin/env python3
"""Measure what each version produced, against ground truth.
  coverage  = % of the report's ink that is inside the crop at all
  separation= paper minus ink, on page interior only (higher = more readable)
  grain     = sd of blank paper away from any print (lower = cleaner)
  dpi       = the page's own width in the saved file
"""
import json, os
import numpy as np
from PIL import Image
from scipy import ndimage
D = os.path.dirname(os.path.abspath(__file__))

def run(base, out):
    meta = json.load(open(os.path.join(D, base + ".json")))
    mask = np.load(os.path.join(D, base + "_mask.npy"))
    m = json.load(open(os.path.join(D, out + ".json")))
    OW, OH = m["outW"], m["outH"]
    img = np.frombuffer(open(os.path.join(D, out + ".bin"), "rb").read(),
                        np.uint8).reshape(OH, OW, 4)[:, :, 0].astype(np.float32)
    sc = m["capW"] / meta["w"]
    cx, cy, cw, ch = m["crop"]
    # crop rectangle back in original pixels
    ox0, oy0, ow, oh = cx/sc, cy/sc, cw/sc, ch/sc
    inrect = np.zeros_like(mask)
    inrect[int(max(0,oy0)):int(oy0+oh), int(max(0,ox0)):int(ox0+ow)] = True
    coverage = (mask & inrect).sum() / max(1, mask.sum())

    # sample ground truth onto the output grid
    qi = np.clip(np.round((np.arange(OW)*cw/OW + cx)/sc).astype(int), 0, meta["w"]-1)
    ri = np.clip(np.round((np.arange(OH)*ch/OH + cy)/sc).astype(int), 0, meta["h"]-1)
    om = mask[np.ix_(ri, qi)]
    # page interior only: inside the page box, inset 4%, in output coords
    px, py, pw, ph = meta["box"]
    def toout(X, Y):
        return ((X*sc - cx)*OW/cw, (Y*sc - cy)*OH/ch)
    ax, ay = toout(px + pw*0.04, py + ph*0.04)
    bx, by = toout(px + pw*0.96, py + ph*0.96)
    interior = np.zeros_like(om)
    ax, ay, bx, by = [int(max(0, v)) for v in (ax, ay, bx, by)]
    interior[ay:by, ax:bx] = True
    far = ~ndimage.binary_dilation(om, np.ones((9, 9), bool))   # blank paper only
    res = {"coverage": coverage*100, "route": m["route"], "ms": m["ms"],
           "dpi": pw*(OW/cw)*sc/8.27, "out": "%dx%d" % (OW, OH)}
    halves = {"lit half": interior & (np.arange(OH)[:,None] < OH//2),
              "shadowed half": interior & (np.arange(OH)[:,None] >= OH//2)}
    for nm, sel in halves.items():
        ink = img[sel & om]; pap = img[sel & far]
        if ink.size < 200 or pap.size < 200: continue
        res[nm] = (pap.mean()-ink.mean(), pap.std())
    return res

print("%-10s %-5s %-15s %8s %7s %6s %11s %-13s %-13s" %
      ("image","ver","route","coverage","dpi","ms","out","lit sep/grain","shadow sep/grain"))
for base in ("fillframe", "ondesk", "harsh"):
    for v in ("old", "new"):
        r = run(base, "%s_%s" % (base, v))
        a = r.get("lit half", (0,0)); b = r.get("shadowed half", (0,0))
        print("%-10s %-5s %-15s %7.1f%% %7.0f %6d %11s %6.1f /%5.1f %6.1f /%5.1f" %
              (base, v, r["route"], r["coverage"], r["dpi"], r["ms"], r["out"],
               a[0], a[1], b[0], b[1]))
