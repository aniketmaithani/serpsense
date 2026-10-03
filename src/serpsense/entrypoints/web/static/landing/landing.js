// The landing page's one scroll-scrubbed sequence (issue #183). Scroll position is the playhead:
// one timeline, one label per chapter, driven by onScroll over the tall #track. Without this
// module, or with reduced motion, the page stays the plain story the HTML already is.
import { createScope, createTimeline, onScroll } from '../vendor/anime/anime.esm.min.js';
import { CHAPTER, firstChapters, intro } from './chapters-a.js';
import { lastChapters } from './chapters-b.js';
import { fitShapes } from './shapes.js';

const root = document.documentElement;
const track = document.getElementById('track');
const chapters = [...document.querySelectorAll('.chapter')];
const signal = document.getElementById('signal');
const OUT = 240; // the leaving chapter's copy fades out over 240 units,
const IN = 60; // ending 60 units before the boundary, where the arriving chapter's starts
let introduced = false;

// Each chapter's copy hands over to the next at the boundary: the old block is gone before the
// new one starts, so two chapters' copy never overlaps.
function crossfade(tl) {
  chapters.forEach((chapter, i) => {
    tl.label(chapter.id, i * CHAPTER);
    if (i === 0) return;
    const start = i * CHAPTER - IN;
    tl.add(chapters[i - 1], { opacity: [1, 0], translateY: [0, -16], duration: OUT }, start - OUT)
      .add(chapter, { opacity: [0, 1], translateY: [16, 0], duration: 300 }, start);
  });
}

// Only the copy on screen can be clicked, selected or reached with Tab: one chapter is `on` at a
// time, from its fade-in to the next one's, and landing.css hides the rest.
function showChapters(time) {
  const now = Math.floor((time + IN) / CHAPTER);
  chapters.forEach((chapter, i) => chapter.classList.toggle('on', i === Math.min(now, chapters.length - 1)));
}

const scope = createScope({
  mediaQueries: { reduce: '(prefers-reduced-motion: reduce)', mobile: '(max-width: 767px)' },
}).add((self) => {
  const { reduce, mobile } = self.matches;
  root.classList.toggle('motion', !reduce);
  if (reduce) return undefined;
  fitShapes(); // before anything moves, so the measured layout is the resting one
  const searchBar = document.getElementById('k-search').getAttribute('d');
  signal.setAttribute('d', searchBar);
  if (!introduced) intro();
  introduced = true;
  const tl = createTimeline({
    defaults: { ease: 'inOutQuad', duration: 400 },
    autoplay: onScroll({
      target: track,
      enter: 'top top',
      leave: 'bottom bottom',
      sync: 0.3,
      onUpdate: (scroll) => showChapters(scroll.linked.currentTime), // after each seek
    }),
  });
  showChapters(0);
  crossfade(tl);
  const precision = mobile ? 0.08 : 0.15; // morph points per unit of outline: fewer on a phone,
  const spread = mobile ? 0.2 : 0.3; // and a tighter burst of mentions
  firstChapters(tl, { precision, spread });
  const unhook = lastChapters(tl, track, precision);
  tl.add({ duration: 1 }, chapters.length * CHAPTER - 1); // the last chapter's full length
  signal.setAttribute('d', searchBar);
  return () => {
    unhook();
    root.classList.remove('motion');
  };
});

// Positions are measured in pixels, so a new window width rebuilds the sequence (debounced). A
// phone's address bar showing or hiding changes only the height, mid-scroll, and rebuilds nothing.
let width = innerWidth;
let resizing;
addEventListener('resize', () => {
  if (innerWidth === width) return;
  width = innerWidth;
  clearTimeout(resizing);
  resizing = setTimeout(() => scope.refresh(), 200);
});
