// Drives the OLD (v2.3) and NEW (v2.4) widget over the same synthetic photos.
//   node run.js <old|new> <imagebase> <outprefix>
require('./shim.js');
const fs = require('fs'), path = require('path');
const which = process.argv[2], base = process.argv[3], outp = process.argv[4];
const dir = __dirname;

global.SCANNER_CONFIG = (which === 'new')
  ? { nameBase:'rep', backUrl:'/', allowIdCard:false, allowBatch:false,
      captureMax:2600, warpMax:2600, jpegQuality:0.92, wholePageFirst:true }
  : { nameBase:'rep', backUrl:'/', allowIdCard:false, allowBatch:false };

let src = fs.readFileSync(which === 'new'
  ? path.join(dir, '..', 'scanner_widget.js')
  : path.join(dir, 'scanner_widget_v23.js'), 'utf8');
if (which === 'old'){                      // v2.3 exposes neither -- expose them
  src = src.replace('  // ---------------------------------------------------------------- warp (verbatim v1.2.0)',
    '  window.__scannerEnhance = enhanceGray;\n  // ---- warp');
}
(0, eval)(src);

const img = global.__loadBin(path.join(dir, base));
const cap = (which === 'new') ? 2600 : 1400;

// ---- 1. what the widget puts on screen (the capture ceiling) --------------
const s = Math.min(1, cap / Math.max(img.width, img.height));
const cv = new global.__El('canvas');
cv.width = Math.round(img.width * s); cv.height = Math.round(img.height * s);
cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);

// ---- 2. auto-crop decision ------------------------------------------------
let found = window.__scannerAutoDetect(cv);
let route = 'detected';
if (which === 'new'){
  if (found && !window.__scannerBorderVisible(cv)) { found = null; route = 'fills-frame'; }
  if (found){
    const fa = (found[1][0]-found[0][0]) * (found[2][1]-found[1][1]);
    if (fa > cv.width*cv.height*0.90) { found = null; route = 'nothing-to-crop'; }
  }
  if (found){
    const pad = Math.round(Math.min(cv.width, cv.height)*0.015);
    found = [[Math.max(0,found[0][0]-pad), Math.max(0,found[0][1]-pad)],
             [Math.min(cv.width,found[1][0]+pad), Math.max(0,found[0][1]-pad)],
             [Math.min(cv.width,found[1][0]+pad), Math.min(cv.height,found[2][1]+pad)],
             [Math.max(0,found[0][0]-pad), Math.min(cv.height,found[2][1]+pad)]];
  } else if (route === 'detected') route = 'whole-page';
} else if (!found) route = 'inset-8pc';

let crop;
if (found) crop = [found[0][0], found[0][1], found[1][0]-found[0][0], found[2][1]-found[1][1]];
else if (which === 'new') crop = [0, 0, cv.width, cv.height];
else { const mx = cv.width*0.08, my = cv.height*0.08;
       crop = [mx, my, cv.width-2*mx, cv.height-2*my]; }

// ---- 3. the saved page: crop, resample to the warp ceiling, enhance -------
const warpMax = (which === 'new') ? 2600 : 1600;
let W = Math.round(crop[2]), H = Math.round(crop[3]);
const w2 = Math.min(1, warpMax / Math.max(W, H));
const OW = Math.max(50, Math.round(W*w2)), OH = Math.max(50, Math.round(H*w2));
const cut = new global.__El('canvas'); cut.width = OW; cut.height = OH;
{ // nearest-neighbour sample out of cv, exactly as warp() does
  const sd = cv.getContext('2d').getImageData(0,0,cv.width,cv.height).data;
  const D = new Uint8ClampedArray(OW*OH*4);
  for (let r=0;r<OH;r++) for (let q=0;q<OW;q++){
    const X = Math.round(crop[0] + q*crop[2]/OW), Y = Math.round(crop[1] + r*crop[3]/OH);
    const k=(r*OW+q)*4;
    if (X>=0&&Y>=0&&X<cv.width&&Y<cv.height){ const si=(Y*cv.width+X)*4;
      D[k]=sd[si]; D[k+1]=sd[si+1]; D[k+2]=sd[si+2]; } else { D[k]=D[k+1]=D[k+2]=255; }
    D[k+3]=255; }
  cut._data = D;
}
const t0 = Date.now();
window.__scannerEnhance(cut._data, OW, OH);
const ms = Date.now() - t0;

global.__saveBin(path.join(dir, outp + '.bin'), cut);
fs.writeFileSync(path.join(dir, outp + '.json'), JSON.stringify({
  which, route, crop: crop.map(Math.round), capW: cv.width, capH: cv.height,
  outW: OW, outH: OH, ms,
  scale: [cv.width/img.width, OW/crop[2]]
}, null, 1));
console.log(which.toUpperCase().padEnd(4), base.padEnd(10), 'route=' + route.padEnd(16),
  'crop=' + crop.map(Math.round).join(','), 'out=' + OW + 'x' + OH, ms + 'ms');
