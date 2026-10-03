// The Signal's shapes, drawn around what they frame. Each build measures the framed element in
// the scene box's 1000 x 600 frame and writes the key path the Signal morphs to, so no text
// crosses an outline at any window size. Every closed shape starts at its top left and runs
// clockwise, which keeps the morphs between them from twisting.
const box = document.querySelector('.box');
const round = (value) => Math.round(value * 10) / 10;

// An element's layout box in frame units, before any animation has moved it.
export function frameOf(selector) {
  const b = box.getBoundingClientRect();
  const r = document.querySelector(selector).getBoundingClientRect();
  const sx = 1000 / b.width;
  const sy = 600 / b.height;
  return { x: (r.left - b.left) * sx, y: (r.top - b.top) * sy, w: r.width * sx, h: r.height * sy };
}

// A box grown by `px` across and `py` down, at least `minWidth` wide, centred where it was.
export function grow({ x, y, w, h }, px, py, minWidth = 0) {
  const width = Math.max(w + 2 * px, minWidth);
  return { x: x + w / 2 - width / 2, y: y - py, w: width, h: h + 2 * py };
}

export function roundRect({ x, y, w, h }, radius) {
  const r = Math.min(radius, w / 2, h / 2);
  const [left, top, right, bottom] = [x, y, x + w, y + h].map(round);
  const arc = `A${round(r)} ${round(r)} 0 0 1`;
  return `M${round(left + r)} ${top}H${round(right - r)}${arc} ${right} ${round(top + r)}`
    + `V${round(bottom - r)}${arc} ${round(right - r)} ${bottom}H${round(left + r)}`
    + `${arc} ${left} ${round(bottom - r)}V${round(top + r)}${arc} ${round(left + r)} ${top}Z`;
}

export const pill = (rect) => roundRect(rect, rect.h / 2);

// The gauge: an arc over the scores, its feet halfway down the gap above the weight bars.
function gauge() {
  const scores = frameOf('.readout');
  const bars = frameOf('.bars');
  const base = (scores.y + scores.h + bars.y) / 2;
  const centre = scores.x + scores.w / 2;
  const clear = Math.hypot(scores.w / 2 + 40, base - scores.y + 26); // over the scores' corners
  const r = round(Math.min(Math.max(clear, 210), base - 8));
  return `M${round(centre - r)} ${round(base)}A${r} ${r} 0 0 1 ${round(centre + r)} ${round(base)}`;
}

// The envelope: the alert inside with room to spare, its flap well above the first line.
function envelope() {
  const card = frameOf('.alertcard');
  const flap = Math.min(Math.max(card.h * 0.22, 34), 60);
  const { x, y, w, h } = grow(card, 30, 0);
  const top = round(y - flap - 26);
  const [left, right, bottom] = [x, x + w, y + h + 28].map(round);
  return `M${left} ${top}L${round(x + w / 2)} ${round(top + flap)}L${right} ${top}V${bottom}H${left}Z`;
}

// The document: the draft inside, a corner folded over at the top right.
function doc() {
  const { x, y, w, h } = grow(frameOf('.docu'), 30, 26);
  const [left, top, right, bottom] = [x, y, x + w, y + h].map(round);
  return `M${left} ${top}H${round(right - 36)}L${right} ${round(top + 36)}V${bottom}H${left}Z`;
}

// The replay track: a thin bar under the knob's whole run.
function track() {
  const scrub = frameOf('.scrub');
  const knob = frameOf('.knob');
  return pill({ x: scrub.x, y: knob.y + knob.h / 2 - 6, w: scrub.w, h: 12 });
}

const FITS = [
  ['k-search', () => pill(grow(frameOf('.query'), 60, 16, 480))],
  ['k-frame', () => roundRect(grow(frameOf('.cells'), 26, 26), 14)],
  ['k-gauge', gauge],
  ['k-envelope', envelope],
  ['k-doc', doc],
  ['k-track', track],
  ['k-strip', () => roundRect(grow(frameOf('.box > .strip'), 26, 22), 20)],
  ['k-cta', () => pill(grow(frameOf('#cta .button'), 34, 16))],
];

// Writes every key shape for the current layout.
export function fitShapes() {
  for (const [id, shape] of FITS) {
    document.getElementById(id).setAttribute('d', shape());
  }
}
