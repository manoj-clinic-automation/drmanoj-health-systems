require('./shim.js');
const path = require('path');
global.SCANNER_CONFIG = { nameBase:'r', allowIdCard:false, allowBatch:false, wholePageFirst:true };
(0, eval)(require('fs').readFileSync(path.join(__dirname, '..', 'scanner_widget.js'), 'utf8'));
const out = {};
for (const b of ['fillframe', 'ondesk', 'harsh', 'closeup']) {
  const img = global.__loadBin(path.join(__dirname, b));
  const s = Math.min(1, 2600 / Math.max(img.width, img.height));
  const cv = new global.__El('canvas');
  cv.width = Math.round(img.width*s); cv.height = Math.round(img.height*s);
  cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
  out[b] = window.__scannerBorderVisible(cv);
}
console.log(JSON.stringify(out));
