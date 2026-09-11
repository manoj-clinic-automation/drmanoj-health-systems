#!/usr/bin/env python3
"""
patch_scanner_report.py  --  GutLog's own copy of scanner_widget.js, tuned for
printed lab reports instead of pharmacy bills.

Anchor-verified, idempotent (MARKER), compile-checked by node, .bak before write,
self-restoring, --check.

Base: S219 v2.3, md5 4ae2d29aa73943c6f772189b56be6307 (the newest of the three
clinic versions; confirmed live on /root/assetapp/scanner_widget.js).
This file is GutLog's copy only -- /root/gutlog/scanner_widget.js -- so the
clinic's seven live surfaces are untouched.
"""
import hashlib, os, re, shutil, subprocess, sys, time

MARKER  = "SCANNER_REPORT_V24"
BASEMD5 = "4ae2d29aa73943c6f772189b56be6307"
TARGET  = sys.argv[sys.argv.index("--file")+1] if "--file" in sys.argv else "scanner_widget.js"
CHECK   = "--check" in sys.argv

EDITS = []
def E(name, anchor, new):
    EDITS.append((name, anchor, new))

# ----------------------------------------------------------------- 1. knobs
E("knobs",
"""  var allowId = CFG.allowIdCard !== false;
  var allowBatch = CFG.allowBatch !== false;
""",
"""  var allowId = CFG.allowIdCard !== false;
  var allowBatch = CFG.allowBatch !== false;

  /* ============ v2.4 -- KNOBS FOR PRINTED REPORTS ======================= *
   * Every number below keeps its v2.3 value when the host does not set it,
   * so a caller that does not know about these knobs behaves exactly as it
   * did.  They exist because a pharmacy bill and a pathology report are not
   * the same photograph:
   *
   *   a bill    large print, half A4, lying on a desk with the desk visible
   *   a report  8pt print, full A4, held close so the page fills the frame
   *
   * At 1600px an A4 page is ~135dpi, and 8pt print is then ~15px tall --
   * about where an OCR starts guessing.  2600px is ~220dpi and it stops.
   */
  var CAP_MAX     = parseInt(CFG.captureMax, 10) || 1400;   // working copy on screen
  var WARP_MAX    = parseInt(CFG.warpMax, 10)    || 1600;   // the page that is saved
  var JPEG_Q      = parseFloat(CFG.jpegQuality)  || 0.85;
  var WHOLE_FIRST = CFG.wholePageFirst === true;            // keep it all; crop is the exception
""")

# ------------------------------------------------------- 2. capture ceiling
E("cap-loadimage",
"""      var s = Math.min(1, 1400 / Math.max(img.width, img.height));""",
"""      var s = Math.min(1, CAP_MAX / Math.max(img.width, img.height));""")

E("cap-warp",
"""    var s=Math.min(1,1600/Math.max(W,H)); W=Math.max(50,Math.round(W*s)); H=Math.max(50,Math.round(H*s));""",
"""    var s=Math.min(1,WARP_MAX/Math.max(W,H)); W=Math.max(50,Math.round(W*s)); H=Math.max(50,Math.round(H*s));""")

E("cap-shoot",
"""    loadImage(c.toDataURL("image/jpeg", 0.92)); closeCam();""",
"""    loadImage(c.toDataURL("image/jpeg", Math.max(0.92, JPEG_Q))); closeCam();""")

E("cap-jpeg",
"""    pages.push(canvas.toDataURL("image/jpeg", 0.85));""",
"""    pages.push(canvas.toDataURL("image/jpeg", JPEG_Q));""")

# -------------------------------------------- 3. is there any desk to see?
E("border-probe",
"""  window.__scannerAutoDetect = autoDetect;   // exposed so the selftest can drive it""",
"""  /* ---- v2.4: is there a desk in this photograph at all? ----------------
   * autoDetect fits the surface from a ring around the frame.  That is sound
   * for a bill on a desk and wrong for a report held close, where the page
   * fills the frame: the ring is then paper, the fit calls paper "surface",
   * and the brightest thing left is the printing -- so the outline lands
   * around a paragraph and the rest of the report is cropped away.  This is
   * exactly the auto-crop the owner reported.
   *
   * So before the detector is believed: is the border darker than the middle?
   * If it is not, the page fills the frame and there is nothing to crop.
   */
  function borderVisible(source){
    try {
      var W = source.width, H = source.height;
      var TW = Math.min(200, W), sc = TW / W, TH = Math.max(8, Math.round(H * sc));
      var t = document.createElement("canvas"); t.width = TW; t.height = TH;
      t.getContext("2d").drawImage(source, 0, 0, TW, TH);
      var D = t.getContext("2d").getImageData(0, 0, TW, TH).data;
      var band = Math.max(2, Math.round(Math.min(TW, TH) * 0.04));
      var ring = [], mid = [], x, y, p, v;
      for (y = 0; y < TH; y++) for (x = 0; x < TW; x++) {
        p = (y * TW + x) * 4; v = 0.3*D[p] + 0.59*D[p+1] + 0.11*D[p+2];
        if (x < band || y < band || x >= TW-band || y >= TH-band) ring.push(v);
        else if (x > TW*0.3 && x < TW*0.7 && y > TH*0.3 && y < TH*0.7) mid.push(v);
      }
      if (ring.length < 16 || mid.length < 16) return true;
      function med(a){ a.sort(function(u,v2){ return u-v2; }); return a[a.length>>1]; }
      // the middle of a page is mostly paper with print on it, so its median
      // sits a little below clean paper; 0.82 is below that and above any
      // desk, wood or otherwise, that a phone photographs alongside white.
      return med(ring) < 0.82 * med(mid);
    } catch (e) { return true; }
  }
  window.__scannerBorderVisible = borderVisible;

  window.__scannerAutoDetect = autoDetect;   // exposed so the selftest can drive it""")

# ------------------------------------------------- 4. what loadImage does with it
E("loadimage-decide",
"""      var found = autoDetect(cv);
      if (found) {
        corners = found;
        say("Outline placed automatically \\u2014 drag a corner if it needs nudging.");
      } else {
        var mx = cv.width * 0.08, my = cv.height * 0.08;
        corners = [[mx,my],[cv.width-mx,my],[cv.width-mx,cv.height-my],[mx,cv.height-my]];
        say("Could not find the edges \\u2014 drag the corners onto the document.");
      }""",
"""      var found = autoDetect(cv);
      // v2.4, and the whole of the auto-crop fix:
      //   * no visible desk -> the page fills the frame, so do not crop
      //   * a box covering almost the frame -> the same thing, said differently
      //   * a box that IS believed gets 1.5% of slack, because shaving the top
      //     line off a report costs more than a millimetre of desk in the scan
      if (found && !borderVisible(cv)) found = null;
      if (found) {
        var fa = (found[1][0]-found[0][0]) * (found[2][1]-found[1][1]);
        if (fa > cv.width * cv.height * 0.90) found = null;
      }
      if (found) {
        var pad = Math.round(Math.min(cv.width, cv.height) * 0.015);
        var L2 = Math.max(0, found[0][0]-pad), T2 = Math.max(0, found[0][1]-pad),
            R2 = Math.min(cv.width, found[1][0]+pad), B2 = Math.min(cv.height, found[2][1]+pad);
        corners = [[L2,T2],[R2,T2],[R2,B2],[L2,B2]];
        say("Outline placed automatically \\u2014 drag a corner if it needs nudging.");
      } else if (WHOLE_FIRST) {
        corners = [[0,0],[cv.width,0],[cv.width,cv.height],[0,cv.height]];
        say("Keeping the whole photo \\u2014 drag a corner only if you want to crop.");
      } else {
        var mx = cv.width * 0.08, my = cv.height * 0.08;
        corners = [[mx,my],[cv.width-mx,my],[cv.width-mx,cv.height-my],[mx,cv.height-my]];
        say("Could not find the edges \\u2014 drag the corners onto the document.");
      }""")

# ---------------------------------------------------- 5. reset = whole page
E("resetcorners",
"""    var mx=ov.width*0.08, my=ov.height*0.08;
    corners=[[mx,my],[ov.width-mx,my],[ov.width-mx,ov.height-my],[mx,ov.height-my]]; drawOverlay(); }""",
"""    if (WHOLE_FIRST){
      corners=[[0,0],[ov.width,0],[ov.width,ov.height],[0,ov.height]]; drawOverlay(); return; }
    var mx=ov.width*0.08, my=ov.height*0.08;
    corners=[[mx,my],[ov.width-mx,my],[ov.width-mx,ov.height-my],[mx,ov.height-my]]; drawOverlay(); }""")

# --------------------------------------------------------- 6. enhanceGray
OLD_ENH = """  function enhanceGray(D, W, H){
    var Wp=W+1, integ=new Float64Array(Wp*(H+1)), x, y, i, p, gy;
    for (y=0;y<H;y++){ var rowsum=0;
      for (x=0;x<W;x++){ p=(y*W+x)*4; gy=0.3*D[p]+0.59*D[p+1]+0.11*D[p+2];
        rowsum+=gy; integ[(y+1)*Wp+(x+1)]=integ[y*Wp+(x+1)]+rowsum; } }
    var s=Math.max(15, Math.floor((W<H?W:H)/8)), half=s>>1;
    var minv=1e9, maxv=-1e9, out=new Float32Array(W*H);
    for (y=0;y<H;y++){ var y1=y-half<0?0:y-half, y2=y+half>=H?H-1:y+half;
      for (x=0;x<W;x++){ var x1=x-half<0?0:x-half, x2=x+half>=W?W-1:x+half;
        var cnt=(x2-x1+1)*(y2-y1+1);
        var sum=integ[(y2+1)*Wp+(x2+1)]-integ[y1*Wp+(x2+1)]-integ[(y2+1)*Wp+x1]+integ[y1*Wp+x1];
        var mean=sum/cnt; p=(y*W+x)*4; gy=0.3*D[p]+0.59*D[p+1]+0.11*D[p+2];
        var norm=mean>0?(gy/mean)*200:gy; if(norm>255)norm=255;
        out[y*W+x]=norm; if(norm<minv)minv=norm; if(norm>maxv)maxv=norm; } }
    var rng=maxv-minv; if(rng<1)rng=1;
    for (i=0;i<W*H;i++){ var g2=Math.round((out[i]-minv)*255/rng); if(g2<0)g2=0; if(g2>255)g2=255;
      p=i*4; D[p]=D[p+1]=D[p+2]=g2; } }"""

NEW_ENH = """  function enhanceGray(D, W, H){
    /* v2.4.  Three things were wrong on a report and right enough on a bill:
     *
     *  1. the window was min-side/8.  Fitted to a ~1000px photo of a bill that
     *     is 125px; on a 2600px page it is 325px, and a shadow gradient lives
     *     comfortably inside 325px -- so the shadow survived the flattening.
     *  2. blank paper was divided by its own local mean, which multiplies the
     *     paper grain up into speckle.  Half a lab report is white space, and
     *     it came back dirty.
     *  3. the stretch used min and max.  One staple, one punch hole, one black
     *     logo set the low end for the whole page and the print went pale.
     *
     * And the illumination map is now computed on a small grid.  At 2600px a
     * full-resolution integral image is 75MB, which is how a phone browser
     * dies mid-scan; the shadow is smooth, so it does not need the page's
     * resolution to be described.
     */
    var N=W*H, i, x, y, p, gy;
    var g=new Float32Array(N);
    for (i=0;i<N;i++){ p=i*4; g[i]=0.3*D[p]+0.59*D[p+1]+0.11*D[p+2]; }

    // --- the paper, mapped small ------------------------------------------
    // The shadow across a page is smooth, so the map of it does not need the
    // page's resolution -- and at 2600px a full-resolution integral image is
    // 75MB, which is how a phone browser dies mid-scan.  Block means at ~1/ds
    // scale carry the same field for a 64th of the memory.
    var ds=Math.max(1, Math.round((W<H?W:H)/400));
    var ws=Math.ceil(W/ds), hs=Math.ceil(H/ds), NS=ws*hs;
    var bs=new Float64Array(NS), bn=new Float64Array(NS), k;
    for (y=0;y<H;y++){ var by=((y/ds)|0)*ws;
      for (x=0;x<W;x++){ k=by+((x/ds)|0); bs[k]+=g[y*W+x]; bn[k]++; } }
    for (i=0;i<NS;i++){ if (bn[i]) bs[i]/=bn[i]; }

    // The background is the local MAXIMUM of those block means, not their
    // average.  A local average is dragged down by whatever print is inside
    // the window, so dividing by it leaves a pale halo round every block of
    // text -- visible on a report as a grey box round each column.  The
    // brightest block in the neighbourhood is the paper itself, which is
    // exactly what the print should be measured against.
    var win=Math.max(24, Math.round((W<H?W:H)/16)), hw=Math.max(1, Math.round(win/(2*ds)));
    var t1=new Float32Array(NS), bgm=new Float32Array(NS), j, m2;
    for (y=0;y<hs;y++){ for (x=0;x<ws;x++){
        var x1=x-hw<0?0:x-hw, x2=x+hw>=ws?ws-1:x+hw; m2=0;
        for (j=x1;j<=x2;j++){ if (bs[y*ws+j]>m2) m2=bs[y*ws+j]; }
        t1[y*ws+x]=m2; } }
    for (x=0;x<ws;x++){ for (y=0;y<hs;y++){
        var y1=y-hw<0?0:y-hw, y2=y+hw>=hs?hs-1:y+hw; m2=0;
        for (j=y1;j<=y2;j++){ if (t1[j*ws+x]>m2) m2=t1[j*ws+x]; }
        bgm[y*ws+x]=m2; } }
    // a max map has plateaux and steps; one box blur turns it back into the
    // smooth surface a photograph's lighting actually is.
    var Wp=ws+1, I1=new Float64Array(Wp*(hs+1)), hb=Math.max(1, hw>>1);
    for (y=0;y<hs;y++){ var r1=0;
      for (x=0;x<ws;x++){ r1+=bgm[y*ws+x]; I1[(y+1)*Wp+(x+1)]=I1[y*Wp+(x+1)]+r1; } }
    var bg=new Float32Array(NS);
    for (y=0;y<hs;y++){ var by1=y-hb<0?0:y-hb, by2=y+hb>=hs?hs-1:y+hb;
      for (x=0;x<ws;x++){ var bx1=x-hb<0?0:x-hb, bx2=x+hb>=ws?ws-1:x+hb;
        var cnt=(bx2-bx1+1)*(by2-by1+1);
        bg[y*ws+x]=(I1[(by2+1)*Wp+(bx2+1)]-I1[by1*Wp+(bx2+1)]
                   -I1[(by2+1)*Wp+bx1]+I1[by1*Wp+bx1])/cnt; } }
    function samp(M, fx, fy){                       // bilinear: no block edges
      var ax=fx<0?0:(fx>ws-1?ws-1:fx), ay=fy<0?0:(fy>hs-1?hs-1:fy);
      var x0=ax|0, y0=ay|0, xb=x0+1>ws-1?ws-1:x0+1, yb=y0+1>hs-1?hs-1:y0+1;
      var tx=ax-x0, ty=ay-y0;
      var a0=M[y0*ws+x0]+(M[y0*ws+xb]-M[y0*ws+x0])*tx;
      var a1=M[yb*ws+x0]+(M[yb*ws+xb]-M[yb*ws+x0])*tx;
      return a0+(a1-a0)*ty; }

    var out=new Float32Array(N), hist=new Float64Array(256);
    for (y=0;y<H;y++){ var fy=(y+0.5)/ds-0.5;
      for (x=0;x<W;x++){ var fx=(x+0.5)/ds-0.5;
        var b2=samp(bg,fx,fy); if (b2<4) b2=4;
        i=y*W+x;
        var norm=(g[i]/b2)*248;
        if (norm<0) norm=0; else if (norm>255) norm=255;
        out[i]=norm; hist[norm|0]++; } }

    // Black point from a low percentile, not the minimum -- one staple or one
    // punch hole used to set it for the whole page and the print went pale.
    // White point from the 75th percentile, because three quarters of a page
    // of print IS paper: everything at or above it clips to white, which is
    // what takes the grain off a shadowed corner instead of amplifying it
    // along with the letters. Guarded for the rare page that is mostly dark.
    var lo=0, hi=232, acc=0, q=255;
    for (i=0;i<256;i++){ acc+=hist[i]; if (acc>=N*0.004){ lo=i; break; } }
    acc=0;
    for (i=0;i<256;i++){ acc+=hist[i]; if (acc>=N*0.75){ q=i; break; } }
    // Paper has just been put at 248 by construction, so 244 is the white
    // point wherever the photograph came from -- and clipping there is what
    // finally takes the grain off a shadowed corner instead of amplifying it
    // along with the letters. The percentile is kept only as the guard for a
    // page that is mostly dark, where there is no paper to speak of.
    if (q<160){ acc=0; for (i=0;i<256;i++){ acc+=hist[i]; if (acc>=N*0.98){ hi=i; break; } } }
    if (hi-lo<32){ lo=0; hi=255; }
    var rng=hi-lo;
    for (i=0;i<N;i++){ var g2=(out[i]-lo)*255/rng; if(g2<0)g2=0; else if(g2>255)g2=255;
      p=i*4; D[p]=D[p+1]=D[p+2]=g2|0; } }
  window.__scannerEnhance = enhanceGray;   // exposed so the selftest can measure it"""

E("enhance", OLD_ENH, NEW_ENH)

# --------------------------------------------- 7. whole page as the big button
E("whole-first",
"""  // ---------------------------------------------------------------- elements
  function $(id){ return document.getElementById(id); }""",
"""  // ---------------------------------------------------------------- elements
  function $(id){ return document.getElementById(id); }
  /* v2.4: for reports the whole page is the answer and the crop is the
   * exception, so the two buttons swap places and labels. Nothing is added or
   * removed -- the same two handlers are bound below either way. */
  (function reportButtons(){
    if (!WHOLE_FIRST) return;
    var ap=document.getElementById("addpage"), aw=document.getElementById("addwhole");
    if (!ap || !aw) return;
    var pa=ap.parentNode, pw=aw.parentNode, nx=aw.nextSibling;
    pa.replaceChild(aw, ap); pw.insertBefore(ap, nx);
    aw.textContent="\\u2714 Add this page"; aw.className="btn"; aw.style.flex="2 1 60%";
    ap.textContent="\\u2702 Crop to the outline"; ap.className="btn small"; ap.style.flex="";
    var hint=document.createElement("p");
    hint.className="muted"; hint.style.margin="6px 0 0";
    hint.textContent="A whole page is kept as photographed. Crop only if something else is in the shot.";
    aw.parentNode.parentNode.insertBefore(hint, aw.parentNode.nextSibling);
  })();""")

# ------------------------------------------------------------------ apply
def main():
    src = open(TARGET, encoding="utf-8").read()
    if MARKER in src:
        print("already patched (%s)" % MARKER); return 0
    md5 = hashlib.md5(src.encode()).hexdigest()
    if md5 != BASEMD5:
        print("REFUSING: base md5 %s, expected %s" % (md5, BASEMD5)); return 2
    for name, anchor, _ in EDITS:
        n = src.count(anchor)
        if n != 1:
            print("REFUSING: anchor '%s' found %d times" % (name, n)); return 2
    print("all %d anchors verified" % len(EDITS))
    if CHECK: return 0
    out = src
    for name, anchor, new in EDITS:
        out = out.replace(anchor, new, 1)
    out = out.replace("(function () {", "(function () {  /* " + MARKER + " */", 1)
    tmp = TARGET + ".tmp.js"
    open(tmp, "w", encoding="utf-8").write(out)
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    if r.returncode:
        print("REFUSING: syntax error\n" + r.stderr); os.remove(tmp); return 2
    bak = "%s.bak-v24-%s" % (TARGET, time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(TARGET, bak)
    shutil.move(tmp, TARGET)
    print("patched. backup %s  new md5 %s" % (bak, hashlib.md5(out.encode()).hexdigest()))
    return 0

sys.exit(main())
