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

const FITS = [
  ['k-search', () => pill(grow(frameOf('.query'), 60, 16, 480))],
  ['k-frame', () => roundRect(grow(frameOf('.cells'), 26, 26), 14)],
];

// Writes every key shape for the current layout.
export function fitShapes() {
  for (const [id, shape] of FITS) {
    document.getElementById(id).setAttribute('d', shape());
  }
}
