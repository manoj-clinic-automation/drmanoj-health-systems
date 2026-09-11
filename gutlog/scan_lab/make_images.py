#!/usr/bin/env python3
"""Synthetic lab-report photographs, with ground truth, for the scanner test.
Writes raw RGBA + a .json of dims and the text mask, so node needs no decoder."""
import json, math, os, random
from PIL import Image, ImageDraw, ImageFont
import numpy as np

OUT = os.path.dirname(os.path.abspath(__file__))
random.seed(7)

def font(sz):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

ROWS = [("Haemoglobin","13.4","g/dL","13.0-17.0"),("Total Leucocyte Count","7 200","/cmm","4000-11000"),
        ("Platelet Count","1.42","lakh/cmm","1.5-4.1"),("ESR (Westergren)","28","mm/hr","0-15"),
        ("Sodium","134","mmol/L","136-145"),("Potassium","4.2","mmol/L","3.5-5.1"),
        ("Chloride","99","mmol/L","98-107"),("Urea","24","mg/dL","15-40"),
        ("Creatinine","0.9","mg/dL","0.7-1.3"),("Calcium","9.1","mg/dL","8.6-10.2"),
        ("Bilirubin Total","0.8","mg/dL","0.2-1.2"),("SGPT (ALT)","31","U/L","0-45"),
        ("SGOT (AST)","27","U/L","0-40"),("Alkaline Phosphatase","88","U/L","40-129"),
        ("Total Protein","7.1","g/dL","6.4-8.3"),("Albumin","4.3","g/dL","3.5-5.2")]

def page(w=1240, h=1754):
    """A clean A4-ish report at ~150dpi, black on white. Returns (img, textmask)."""
    im = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(im)
    fb, fs, ft = font(34), font(19), font(22)
    d.text((70, 60), "NK PATHOLOGY LABORATORY", font=fb, fill=0)
    d.text((70, 104), "Haematology & Biochemistry  |  Report", font=fs, fill=0)
    d.line((70, 140, w-70, 140), fill=0, width=3)
    d.text((70, 158), "Patient : XXXX XXXX 4417        Age/Sex : 5X / M", font=fs, fill=0)
    d.text((70, 184), "Collected : 11-09-2026 08:10      Reported : 11-09-2026 12:40", font=fs, fill=0)
    y = 236
    d.text((70, y), "TEST", font=ft, fill=0); d.text((640, y), "RESULT", font=ft, fill=0)
    d.text((820, y), "UNIT", font=ft, fill=0); d.text((980, y), "REFERENCE", font=ft, fill=0)
    y += 34; d.line((70, y, w-70, y), fill=0, width=2); y += 16
    for name, val, unit, ref in ROWS:
        d.text((70, y), name, font=fs, fill=0)
        d.text((640, y), val, font=fs, fill=0)
        d.text((820, y), unit, font=fs, fill=0)
        d.text((980, y), ref, font=fs, fill=0)
        y += 40
    y += 30
    d.line((70, y, w-70, y), fill=0, width=2)
    d.text((70, y+18), "Impression : mild hyponatraemia, platelets at the low end.", font=fs, fill=0)
    d.text((70, h-120), "Verified by : MD Pathology", font=fs, fill=0)
    d.rectangle((w-260, h-170, w-70, h-90), outline=0, width=2)   # a stamp box
    mask = (np.array(im) < 128)
    return im, mask

def photograph(page_img, mask, fill, tilt_deg, shadow, vignette, desk=128, W=2200):
    """Put the page into a frame: `fill` = fraction of the frame width it covers."""
    H = int(W * 4 / 3)
    pw = int(W * fill)
    ph = int(pw * page_img.height / page_img.width)
    if fill <= 1.0 and ph > H * fill:
        ph = int(H * fill); pw = int(ph * page_img.width / page_img.height)
    p = page_img.resize((pw, ph), Image.LANCZOS)
    m = Image.fromarray((mask * 255).astype("uint8")).resize((pw, ph), Image.LANCZOS)
    frame = Image.new("L", (W, H), desk)
    fm = Image.new("L", (W, H), 0)
    ox, oy = (W - pw) // 2, (H - ph) // 2
    if tilt_deg:
        p = p.rotate(tilt_deg, resample=Image.BICUBIC, expand=True, fillcolor=desk)
        m = m.rotate(tilt_deg, resample=Image.BICUBIC, expand=True, fillcolor=0)
        ox, oy = (W - p.width) // 2, (H - p.height) // 2
    frame.paste(p, (ox, oy)); fm.paste(m, (ox, oy))
    a = np.asarray(frame).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    g = np.ones((H, W), np.float32)
    if shadow:                       # a hand/head shadow falling across the page
        g *= 1.0 - shadow * np.clip((xx / W) * 0.7 + (yy / H) * 0.6, 0, 1.3)
    if vignette:                     # flash falloff toward the corners
        r = ((xx - W/2)**2 + (yy - H/2)**2) / ((W/2)**2 + (H/2)**2)
        g *= 1.0 - vignette * r
    a = a * g
    a += np.random.normal(0, 2.2, a.shape)          # sensor grain
    a = np.clip(a, 0, 255).astype(np.uint8)
    return a, (np.asarray(fm) > 128), (ox, oy, p.width if tilt_deg else pw, p.height if tilt_deg else ph)

def dump(name, arr, mask, box):
    H, W = arr.shape
    rgba = np.dstack([arr, arr, arr, np.full_like(arr, 255)]).tobytes()
    open(os.path.join(OUT, name + ".bin"), "wb").write(rgba)
    np.save(os.path.join(OUT, name + "_mask.npy"), mask)
    json.dump({"w": int(W), "h": int(H), "box": [int(v) for v in box]},
              open(os.path.join(OUT, name + ".json"), "w"))
    Image.fromarray(arr).save(os.path.join(OUT, name + ".png"))
    print("%-14s %dx%d  page box %s  text px %d" % (name, W, H, box, int(mask.sum())))

pg, mk = page()
dump("fillframe", *photograph(pg, mk, fill=0.99, tilt_deg=0,   shadow=0.30, vignette=0.18))
dump("ondesk",    *photograph(pg, mk, fill=0.74, tilt_deg=5.0, shadow=0.26, vignette=0.15))
dump("harsh",     *photograph(pg, mk, fill=0.95, tilt_deg=1.5, shadow=0.48, vignette=0.30))
# held close, the way a phone is actually held over a report: no desk at all
dump("closeup",   *photograph(pg, mk, fill=1.18, tilt_deg=0,   shadow=0.34, vignette=0.20))
