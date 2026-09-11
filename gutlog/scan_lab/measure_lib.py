"""Ground-truth measurements shared by measure.py and test_phase_h.py."""
import json, os
import numpy as np
from scipy import ndimage
D = os.path.dirname(os.path.abspath(__file__))

def _load(base, out):
    meta = json.load(open(os.path.join(D, base + ".json")))
    mask = np.load(os.path.join(D, base + "_mask.npy"))
    m = json.load(open(os.path.join(D, out + ".json")))
    OW, OH = m["outW"], m["outH"]
    img = np.frombuffer(open(os.path.join(D, out + ".bin"), "rb").read(),
                        np.uint8).reshape(OH, OW, 4)[:, :, 0].astype(np.float32)
    return meta, mask, m, img

def coverage(base, out):
    """% of the report's ink that survived the crop."""
    meta, mask, m, img = _load(base, out)
    sc = m["capW"] / meta["w"]; cx, cy, cw, ch = m["crop"]
    r = np.zeros_like(mask)
    r[int(max(0, cy/sc)):int((cy+ch)/sc), int(max(0, cx/sc)):int((cx+cw)/sc)] = True
    return (mask & r).sum() / max(1, mask.sum()) * 100

def _grid(base, out):
    meta, mask, m, img = _load(base, out)
    OW, OH = m["outW"], m["outH"]; sc = m["capW"]/meta["w"]; cx, cy, cw, ch = m["crop"]
    qi = np.clip(np.round((np.arange(OW)*cw/OW + cx)/sc).astype(int), 0, meta["w"]-1)
    ri = np.clip(np.round((np.arange(OH)*ch/OH + cy)/sc).astype(int), 0, meta["h"]-1)
    return meta, m, img, mask[np.ix_(ri, qi)]

def separation(base, out):
    """(lit half, shadowed half) paper-minus-ink, page interior only."""
    meta, m, img, om = _grid(base, out)
    OH, OW = img.shape
    far = ~ndimage.binary_dilation(om, np.ones((9, 9), bool))
    px, py, pw, ph = meta["box"]; sc = m["capW"]/meta["w"]; cx, cy, cw, ch = m["crop"]
    def tox(X): return int(max(0, (X*sc - cx)*OW/cw))
    def toy(Y): return int(max(0, (Y*sc - cy)*OH/ch))
    box = np.zeros_like(om)
    box[toy(py+ph*0.08):toy(py+ph*0.92), tox(px+pw*0.08):tox(px+pw*0.92)] = True
    outv = []
    for sel in (box & (np.arange(OH)[:, None] < OH//2),
                box & (np.arange(OH)[:, None] >= OH//2)):
        ink, pap = img[sel & om], img[sel & far]
        outv.append(pap.mean() - ink.mean() if ink.size > 200 and pap.size > 200 else 0.0)
    return outv

def grain(base, out):
    """sd of a blank band well inside the page, where only paper lives."""
    meta, m, img, om = _grid(base, out)
    OH, OW = img.shape
    band = img[int(OH*0.62):int(OH*0.70), int(OW*0.55):int(OW*0.90)]
    return float(band.std())

def dpi(base, out):
    meta, mask, m, img = _load(base, out)
    sc = m["capW"]/meta["w"]
    return meta["box"][2] * (m["outW"]/m["crop"][2]) * sc / 8.27

def strokes(base, out, thr=160):
    """% of the report's ink that is still dark in the saved file."""
    meta, m, img, om = _grid(base, out)
    return float((img[om] < thr).mean() * 100) if om.sum() > 200 else 0.0
