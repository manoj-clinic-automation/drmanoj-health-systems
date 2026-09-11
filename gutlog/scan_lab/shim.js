// Minimum DOM the scanner widget needs, with a real 2D canvas behind it, so the
// image maths can be driven and measured offline. No browser, no jsdom.
const fs = require('fs');

function Ctx(el){ this.el = el; this.fillStyle = '#fff'; }
Ctx.prototype._ensure = function(){
  const n = this.el.width * this.el.height * 4;
  if (!this.el._data || this.el._data.length !== n)
    this.el._data = new Uint8ClampedArray(n).fill(255);
  return this.el._data;
};
Ctx.prototype.drawImage = function(src, dx, dy, dw, dh){
  const D = this._ensure(), W = this.el.width, H = this.el.height;
  const sw = src.width, sh = src.height, S = src._data;
  dx = dx|0; dy = dy|0;
  dw = (dw === undefined) ? sw : Math.round(dw);
  dh = (dh === undefined) ? sh : Math.round(dh);
  if (!S) throw new Error('drawImage: source has no pixels');
  for (let y = 0; y < dh; y++){
    const ty = dy + y; if (ty < 0 || ty >= H) continue;
    const sy = Math.min(sh - 1, Math.floor(y * sh / dh));
    for (let x = 0; x < dw; x++){
      const tx = dx + x; if (tx < 0 || tx >= W) continue;
      const sx = Math.min(sw - 1, Math.floor(x * sw / dw));
      const si = (sy * sw + sx) * 4, ti = (ty * W + tx) * 4;
      D[ti] = S[si]; D[ti+1] = S[si+1]; D[ti+2] = S[si+2]; D[ti+3] = 255;
    }
  }
};
Ctx.prototype.getImageData = function(x, y, w, h){
  const D = this._ensure(), W = this.el.width;
  const out = new Uint8ClampedArray(w * h * 4);
  for (let j = 0; j < h; j++)
    for (let i = 0; i < w * 4; i++) out[j*w*4 + i] = D[(y+j)*W*4 + x*4 + i];
  return { data: out, width: w, height: h };
};
Ctx.prototype.createImageData = function(w, h){
  return { data: new Uint8ClampedArray(w*h*4), width: w, height: h }; };
Ctx.prototype.putImageData = function(img, x, y){
  const D = this._ensure(), W = this.el.width;
  for (let j = 0; j < img.height; j++)
    for (let i = 0; i < img.width * 4; i++) D[(y+j)*W*4 + x*4 + i] = img.data[j*img.width*4 + i];
};
Ctx.prototype.fillRect = function(){}; Ctx.prototype.clearRect = function(){};
Ctx.prototype.beginPath = function(){}; Ctx.prototype.moveTo = function(){};
Ctx.prototype.lineTo = function(){}; Ctx.prototype.stroke = function(){};
Ctx.prototype.fill = function(){}; Ctx.prototype.arc = function(){};
Ctx.prototype.closePath = function(){}; Ctx.prototype.rect = function(){};
Ctx.prototype.save = function(){}; Ctx.prototype.restore = function(){};
Ctx.prototype.setLineDash = function(){}; Ctx.prototype.translate = function(){};

let depth = 0;
function El(tag){
  this.tagName = (tag||'div').toUpperCase(); this.style = {}; this.children = [];
  this.width = 300; this.height = 150; this._parent = null;
  this.textContent = ''; this.className = ''; this.innerHTML = '';
  this.checked = true; this.value = ''; this.disabled = false; this.type = '';
}
Object.defineProperty(El.prototype, 'parentNode', {
  get: function(){
    if (!this._parent && depth < 6){ depth++; this._parent = new El('div'); depth--; }
    return this._parent; }
});
El.prototype.getContext = function(){ return this._ctx || (this._ctx = new Ctx(this)); };
El.prototype.addEventListener = function(){}; El.prototype.removeEventListener = function(){};
El.prototype.appendChild = function(c){ this.children.push(c); c._parent = this; return c; };
El.prototype.insertBefore = function(c){ this.children.push(c); c._parent = this; return c; };
El.prototype.replaceChild = function(n, o){ n._parent = this; return o; };
El.prototype.setAttribute = function(k, v){ this['_a_'+k] = v; };
El.prototype.getAttribute = function(k){ return this['_a_'+k]; };
El.prototype.getBoundingClientRect = function(){ return {width:this.width, height:this.height, left:0, top:0}; };
El.prototype.scrollIntoView = function(){};
El.prototype.querySelectorAll = function(){ return []; };
Object.defineProperty(El.prototype, 'nextSibling', { get: function(){ return null; } });

const byId = {};
global.document = {
  head: new El('head'), body: new El('body'),
  getElementById: function(id){ return byId[id] || (byId[id] = new El('div')); },
  createElement: function(t){ return new El(t); },
  querySelectorAll: function(){ return []; },
  getElementsByName: function(){ return []; },
  addEventListener: function(){}
};
global.window = global;
global.navigator = {};
global.localStorage = { getItem: function(){ return null; }, setItem: function(){},
                        removeItem: function(){} };
global.addEventListener = function(){};
global.URL = { createObjectURL: function(){ return ''; } };
global.Image = function(){};
global.__byId = byId;
global.__El = El;

// a canvas-like holding a raw RGBA file
global.__loadBin = function(base){
  const meta = JSON.parse(fs.readFileSync(base + '.json', 'utf8'));
  const el = new El('canvas');
  el.width = meta.w; el.height = meta.h;
  el._data = new Uint8ClampedArray(fs.readFileSync(base + '.bin'));
  el.meta = meta;
  return el;
};
global.__saveBin = function(path, el){ fs.writeFileSync(path, Buffer.from(el._data.buffer, el._data.byteOffset, el._data.length)); };
