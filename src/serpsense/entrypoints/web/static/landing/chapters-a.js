// Chapters 1-6 of the landing sequence: the search bar, the surfaces, SerpApi collecting them,
// mentions normalised into a grid, labelled, and pulled into stories. Every tween states its
// from and to values, so scrolling back rewinds it exactly. Positions use the scene box's
// 1000 x 600 frame, the same one the SVG paths are drawn in.
import {
  animate,
  createDrawable,
  createSeededRandom,
  morphTo,
  scrambleText,
  splitText,
  stagger,
} from '../vendor/anime/anime.esm.min.js';

export const CHAPTER = 1000; // timeline units per chapter
const GRID = [8, 3];

const box = document.querySelector('.box');
export const at = (chapter, offset) => chapter * CHAPTER + offset;

// The pixel offset that moves an element's centre onto (x, y) of the box's frame.
function toPoint(el, x, y) {
  const b = box.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  return [
    b.left + (b.width * x) / 1000 - (r.left + r.width / 2),
    b.top + (b.height * y) / 600 - (r.top + r.height / 2),
  ];
}

// Plays once on load, before any scrolling: the Signal draws the search bar, VoltBox types
// itself in, the headline arrives word by word and the subline unscrambles.
export function intro() {
  const { words } = splitText('.headline', { words: true });
  animate(words, {
    opacity: [0, 1],
    translateY: ['0.5em', '0em'],
    delay: stagger(70),
    duration: 800,
    ease: 'outExpo',
  });
  // Created drawn, so a rebuild (which reverts this) leaves the Signal whole.
  animate(createDrawable('#signal', 0, 1), { draw: ['0 0', '0 1'], duration: 1100, ease: 'inOutCubic' });
  animate('.typed', { innerHTML: scrambleText({ override: '', cursor: true, duration: 700, delay: 400 }) });
  animate('.sub', { innerHTML: scrambleText({ duration: 900, delay: 700 }) });
}

function surfaces(tl, cards, precision) {
  tl.add('.cue', { opacity: [1, 0], duration: 200 }, at(0, 0))
    .add('.query', { opacity: [1, 0], scale: [1, 0.9], duration: 250 }, at(1, 0))
    .add('#signal', { d: morphTo('#k-ring', precision), duration: 600 }, at(1, 100))
    .add(cards, {
      opacity: [0, 1],
      scale: [0.4, 1],
      translateX: (el) => [toPoint(el, 500, 420)[0], 0], // out of the search bar
      translateY: (el) => [toPoint(el, 500, 420)[1], 0],
      delay: stagger(40),
      duration: 500,
    }, at(1, 220)); // once the search has faded
}

function collect(tl, precision) {
  tl.add('.core', { opacity: [0, 1], scale: [0.6, 1], duration: 300 }, at(2, 50))
    .add('#signal', { d: morphTo('#k-funnel', precision), duration: 500 }, at(2, 100))
    .add(createDrawable('.wires path'), { draw: ['0 0', '0 1'], delay: stagger(40), duration: 350 }, at(2, 200))
    .add('.card .engine', { opacity: [0, 1], translateY: ['0.5em', '0em'], delay: stagger(40), duration: 300 }, at(2, 300))
    .add('.card .count', { opacity: [0, 1], scale: [0.4, 1], delay: stagger(40), duration: 250 }, at(2, 450));
}

function normalise(tl, cards, cells, { spread, precision }) {
  const b = box.getBoundingClientRect();
  const random = createSeededRandom(11);
  const fromCore = cells.map((el) => toPoint(el, 500, 300));
  const cloud = cells.map(() => [
    random(-spread, spread, 3) * b.width,
    random(-spread, spread, 3) * b.height,
  ]);
  tl.add('.wires', { opacity: [1, 0], duration: 250 }, at(3, 0))
    .add(cards, { opacity: [1, 0], scale: [1, 0.85], delay: stagger(20), duration: 300 }, at(3, 0))
    .add(cells, {
      opacity: [0, 1],
      translateX: (_, i) => [fromCore[i][0], fromCore[i][0] + cloud[i][0]],
      translateY: (_, i) => [fromCore[i][1], fromCore[i][1] + cloud[i][1]],
      delay: stagger(8),
      duration: 350,
      ease: 'outQuad',
    }, at(3, 150))
    .add('#signal', { d: morphTo('#k-frame', precision), duration: 500 }, at(3, 300))
    .add('.core', { opacity: [1, 0], scale: [1, 0.6], duration: 250 }, at(3, 350))
    .add(cells, {
      translateX: (_, i) => [fromCore[i][0] + cloud[i][0], 0],
      translateY: (_, i) => [fromCore[i][1] + cloud[i][1], 0],
      delay: stagger(25, { grid: GRID, from: 'center' }),
      duration: 400,
      ease: 'inOutCubic',
    }, at(3, 550));
}

function enrich(tl) {
  tl.add('.cells b', { opacity: [0, 1], delay: stagger(30, { grid: GRID, from: 'first' }), duration: 300 }, at(4, 100))
    .add('.tag', { opacity: [0, 1], translateY: [10, 0], delay: stagger(90), duration: 250 }, at(4, 450));
}

function narratives(tl, cells) {
  const negative = cells.filter((el) => el.dataset.tone === 'x');
  const rest = cells.filter((el) => el.dataset.tone !== 'x');
  const anchors = [[380, 290], [620, 320]]; // the two stories' centres
  const spots = negative.map((el, i) => {
    const [x, y] = anchors[i % 2];
    const k = Math.floor(i / 2);
    return toPoint(el, x + (k % 2) * 52 - 26, y + Math.floor(k / 2) * 52 - 26);
  });
  tl.add('.tag', { opacity: [1, 0], duration: 200 }, at(5, 0))
    .add(rest, { opacity: [1, 0.15], duration: 300 }, at(5, 100))
    .add(negative, {
      translateX: (_, i) => [0, spots[i][0]],
      translateY: (_, i) => [0, spots[i][1]],
      delay: stagger(40),
      duration: 500,
      ease: 'inOutCubic',
    }, at(5, 150))
    .add('.cluster', { opacity: [0, 1], translateY: [12, 0], delay: stagger(120), duration: 300 }, at(5, 550));
}

export function firstChapters(tl, { precision, spread }) {
  const cards = [...document.querySelectorAll('.card')];
  const cells = [...document.querySelectorAll('.cells i')];
  surfaces(tl, cards, precision);
  collect(tl, precision);
  normalise(tl, cards, cells, { spread, precision });
  enrich(tl);
  narratives(tl, cells);
}
