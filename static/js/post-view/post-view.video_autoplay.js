/**
 * post-view.video_autoplay.js
 * ─────────────────────────────────────────────────────────────────────────
 * Autoplay-on-scroll for the single post page's media carousel. Same
 * viewport + active-slide dual-IntersectionObserver approach as the feed's
 * static/js/feed/feed.video_autoplay.js, trimmed for post-view's simpler
 * case (at most one carousel on the page, no infinite-scroll re-injection
 * to watch for) and matched to post-view's own class names
 * (.postv-resources-carousel / .postv-resources-scroll / .postv-resource-slide)
 * rather than the feed's (.post-resources-carousel / .resources-scroll-container
 * / .post-resource), so post-view stays free of feed markup coupling.
 *
 * Usage:
 *   import { initPostViewVideoAutoplay, notifyPostViewSlideChange } from './post-view.video_autoplay.js';
 *   initPostViewVideoAutoplay();   // call once after the post resources are rendered
 *   notifyPostViewSlideChange(index); // call from carousel nav/dot/scroll handlers
 * ─────────────────────────────────────────────────────────────────────────
 */

let outerObserver = null;
let innerObserver = null;
let carouselEl = null;
let scrollEl = null;
const visibleIndices = new Set();
let carouselInView = false;

function getVideo(slideEl) {
  return slideEl?.querySelector('video') ?? null;
}

function safePlay(video) {
  if (!video || !video.paused) return;
  video.play().catch(err => {
    if (err.name !== 'AbortError') console.warn('[post-view video autoplay] play() error:', err);
  });
}

function safePause(video) {
  if (!video || video.paused) return;
  video.pause();
}

function evaluateSlide(slideEl, index) {
  const video = getVideo(slideEl);
  if (!video) return;
  if (carouselInView && visibleIndices.has(index)) {
    safePlay(video);
  } else {
    safePause(video);
  }
}

function evaluateAll() {
  if (!scrollEl) return;
  scrollEl.querySelectorAll('.postv-resource-slide').forEach((slideEl, index) => {
    evaluateSlide(slideEl, index);
  });
}

function pauseAll() {
  carouselEl?.querySelectorAll('video').forEach(safePause);
}

function teardown() {
  outerObserver?.disconnect();
  innerObserver?.disconnect();
  outerObserver = null;
  innerObserver = null;
  carouselEl = null;
  scrollEl = null;
  visibleIndices.clear();
  carouselInView = false;
}

/**
 * initPostViewVideoAutoplay()
 * Call once after the post's resources (and its carousel, if any) have been
 * rendered into the DOM. Safe to call again after a re-render — it tears
 * down any previous observers first.
 */
export function initPostViewVideoAutoplay() {
  teardown();

  carouselEl = document.querySelector('.postv-resources-carousel');
  if (!carouselEl) return;

  scrollEl = carouselEl.querySelector('.postv-resources-scroll');
  if (!scrollEl) return;

  const hasVideo = !!carouselEl.querySelector('video');
  if (!hasVideo) return;

  outerObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      carouselInView = entry.isIntersecting;
      if (carouselInView) {
        evaluateAll();
      } else {
        pauseAll();
      }
    });
  }, { threshold: 0.5 });
  outerObserver.observe(carouselEl);

  innerObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      const slideEl = entry.target;
      const index = Array.from(scrollEl.querySelectorAll('.postv-resource-slide')).indexOf(slideEl);
      if (index === -1) return;
      if (entry.isIntersecting) {
        visibleIndices.add(index);
      } else {
        visibleIndices.delete(index);
      }
      evaluateSlide(slideEl, index);
    });
  }, { root: scrollEl, threshold: 0.6 });

  scrollEl.querySelectorAll('.postv-resource-slide').forEach(slide => innerObserver.observe(slide));
}

/**
 * notifyPostViewSlideChange(newIndex)
 * Call from the carousel's nav-button/dot click handlers so playback starts
 * immediately rather than waiting on the IntersectionObserver's threshold.
 */
export function notifyPostViewSlideChange(newIndex) {
  if (!scrollEl) return;
  const slides = scrollEl.querySelectorAll('.postv-resource-slide');
  slides.forEach(slide => safePause(slide.querySelector('video')));
  visibleIndices.clear();
  visibleIndices.add(newIndex);
  const activeSlide = slides[newIndex];
  if (activeSlide) evaluateSlide(activeSlide, newIndex);
}
