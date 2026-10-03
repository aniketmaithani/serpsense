// Chapters 7-12 of the landing sequence: the scores, the alert, the draft, the replay, how it's
// built and the way in, plus the progress rail. As in chapters-a.js, every tween states its
// from and to values, so the whole sequence rewinds exactly.
import { morphTo, splitText, stagger } from '../vendor/anime/anime.esm.min.js';
import { CHAPTER, at } from './chapters-a.js';

const out = { opacity: [1, 0], duration: 250 };
// The draft appears word by word: opacity only, so nothing reflows while it plays.
const { words: draftWords } = splitText('.draft-text', { words: true });

function score(tl, precision) {
  tl.add('.cells i', { opacity: (el) => [el.dataset.tone === 'x' ? 1 : 0.15, 0], duration: 250 }, at(6, 0))
    .add('.cluster', out, at(6, 0))
    .add('#signal', { d: morphTo('#k-gauge', precision), duration: 500 }, at(6, 100))
    .add('.gauge', { opacity: [0, 1], translateY: [16, 0], duration: 300 }, at(6, 260))
    .add('.health', { innerHTML: [0, 41], modifier: Math.round, duration: 450 }, at(6, 320))
    // The crisis climbs linearly through 40 (medium) and 70 (high) at these offsets.
    .add('.crisis', { innerHTML: [0, 74], modifier: Math.round, ease: 'linear', duration: 600 }, at(6, 320))
    .add('.l1', { opacity: [0, 1], duration: 60 }, at(6, 320))
    .add('.l1', { opacity: [1, 0], duration: 60 }, at(6, 645))
    .add('.l2', { opacity: [0, 1], duration: 60 }, at(6, 645))
    .add('.l2', { opacity: [1, 0], duration: 60 }, at(6, 888))
    .add('.l3', { opacity: [0, 1], duration: 60 }, at(6, 888))
    .add('.bars b', { scaleX: [0, 1], delay: stagger(50), duration: 300 }, at(6, 380));
}

function alert(tl, precision) {
  tl.add('.gauge', out, at(7, 0))
    .add('.ac', { opacity: [0, 1], translateY: [16, 0], duration: 300 }, at(7, 260))
    .add('.ac-last', { translateY: ['-100%', '0%'], duration: 300 }, at(7, 420))
    .add('.ac-new', { opacity: [0, 1], translateX: [40, 0], duration: 300 }, at(7, 450))
    .add('#signal', { d: morphTo('#k-envelope', precision), duration: 500 }, at(7, 450))
    .add('.alertcard', { opacity: [0, 1], translateY: [-40, 0], duration: 400, ease: 'outBack' }, at(7, 620));
}

function draft(tl, precision) {
  tl.add(['.ac', '.alertcard'], out, at(8, 0))
    .add('#signal', { d: morphTo('#k-doc', precision), duration: 500 }, at(8, 100))
    .add('.docu', { opacity: [0, 1], duration: 250 }, at(8, 250))
    .add(draftWords, { opacity: [0, 1], delay: stagger(12), duration: 120 }, at(8, 300))
    .add('.cites span', { opacity: [0, 1], scale: [0.4, 1], delay: stagger(60), duration: 200 }, at(8, 650))
    .add('.docu .buttons span', { opacity: [0, 1], translateY: [8, 0], delay: stagger(60), duration: 200 }, at(8, 750));
}

function replay(tl, precision) {
  tl.add('.docu', out, at(9, 0))
    .add('#signal', { d: morphTo('#k-track', precision), duration: 450 }, at(9, 100))
    .add('.scrub', { opacity: [0, 1], duration: 250 }, at(9, 250))
    .add('.knob', {
      translateX: (el) => [0, el.parentElement.getBoundingClientRect().width],
      ease: 'linear',
      duration: 600,
    }, at(9, 300))
    .add('.ticks i', { opacity: [0.35, 1], delay: stagger(50), duration: 60, ease: 'linear' }, at(9, 300))
    // The crisis reaching one more surface every couple of scans.
    .add('.mini i', { opacity: [0, 1], delay: stagger(90), duration: 100 }, at(9, 380));
}

function builtRight(tl, precision) {
  tl.add('.scrub', out, at(10, 0))
    .add('#signal', { d: morphTo('#k-strip', precision), duration: 450 }, at(10, 100))
    .add('.strip li', { opacity: [0, 1], translateY: [20, 0], delay: stagger(45), duration: 250 }, at(10, 250));
}

function cta(tl, precision) {
  // The facts leave before the closing copy (and its button) arrives.
  tl.add('.strip li', { opacity: [1, 0], delay: stagger(15), duration: 180 }, at(11, -360))
    .add('#signal', { d: morphTo('#k-cta', precision), duration: 500 }, at(11, 100));
}

// One dot per chapter fills as its chapter plays; a click scrolls to that chapter.
function rail(tl, track) {
  const links = [...document.querySelectorAll('.rail a')];
  links.forEach((link, i) => {
    tl.add(link.querySelector('i'), { scaleY: [0, 1], ease: 'linear', duration: CHAPTER }, at(i, 0));
  });
  const onClick = (event) => {
    const label = tl.labels[event.currentTarget.dataset.chapter];
    if (label === undefined) return;
    event.preventDefault();
    const time = label && label + 350; // past the hand-over, where the chapter has arrived
    const travel = track.offsetHeight - innerHeight;
    scrollTo({ top: track.offsetTop + (travel * time) / tl.duration, behavior: 'smooth' });
  };
  links.forEach((link) => link.addEventListener('click', onClick));
  return () => links.forEach((link) => link.removeEventListener('click', onClick));
}

export function lastChapters(tl, track, precision) {
  score(tl, precision);
  alert(tl, precision);
  draft(tl, precision);
  replay(tl, precision);
  builtRight(tl, precision);
  cta(tl, precision);
  return rail(tl, track);
}
