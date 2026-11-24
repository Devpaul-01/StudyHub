/**
 * ============================================================================
 * LEADERBOARD EVENTS  —  leaderboard.events.js
 * Core async logic: loading, filtering, pagination, panel toggling.
 * Mirrors the notification.events.js pattern in the existing codebase.
 * ============================================================================
 */

import { leaderboardState }  from './leaderboard.state.js';
import { leaderboardAPI }    from './leaderboard.api.js';
import {
  createMyRankStrip, createMyRankStripSkeleton,
  createPodium, podiumSize, createEntryRow, createRisingRow,
  createNearbySection, createBreakdownPanel,
  createStatsBanner, createEmptyState, createErrorState,
  createSkeletonList, createLoadMoreBtn, createMyRankView,
} from './leaderboard.templates.js';


// ─────────────────────────────────────────────────────────────────────────────
// DOM SELECTORS  (never cached at module level — DOM may not exist yet)
// ─────────────────────────────────────────────────────────────────────────────

function $list()       { return document.getElementById('lb-list'); }
function $myStrip()    { return document.getElementById('lb-my-strip-wrapper'); }
function $statsBanner(){ return document.getElementById('lb-stats-banner'); }
function $breakdown()  { return document.getElementById('lb-breakdown-wrapper'); }
function $section()    { return document.getElementById('leaderboard'); }


// ─────────────────────────────────────────────────────────────────────────────
// 1. LOAD MY RANK STRIP  (always shown at top)
// ─────────────────────────────────────────────────────────────────────────────

export async function loadMyRankStrip() {
  const wrapper = $myStrip();
  if (!wrapper) return;

  wrapper.innerHTML = createMyRankStripSkeleton();
  const generation = leaderboardState.newStripGeneration();

  try {
    const res = await leaderboardAPI.getMyRank({
      period:     leaderboardState.activePeriod,
      department: leaderboardState.activeDepartment,
    });
    if (!leaderboardState.isCurrentStripGeneration(generation)) return;   // superseded

    if (res?.status === 'success') {
      leaderboardState.setMyRankData(res.data);
      wrapper.innerHTML = createMyRankStrip(res.data);
    } else {
      wrapper.innerHTML = '';
    }
  } catch (err) {
    console.error('lb: failed to load my rank strip', err);
    if (leaderboardState.isCurrentStripGeneration(generation)) wrapper.innerHTML = '';
  }
}


// ─────────────────────────────────────────────────────────────────────────────
// 2. LOAD STATS BANNER
// ─────────────────────────────────────────────────────────────────────────────

export async function loadStatsBanner() {
  const wrapper = $statsBanner();
  if (!wrapper) return;

  try {
    const res = await leaderboardAPI.getStats();
    if (res?.status === 'success') {
      leaderboardState.setStatsData(res.data);
      wrapper.innerHTML = createStatsBanner(res.data);
    }
  } catch (err) {
    console.error('lb: failed to load stats', err);
  }
}


// ─────────────────────────────────────────────────────────────────────────────
// 3. LOAD LEADERBOARD  (main list — all views)
// ─────────────────────────────────────────────────────────────────────────────

export async function loadLeaderboard(append = false) {
  // One "load more" at a time. A fresh (non-append) load is never dropped:
  // it starts a new generation that supersedes whatever is in flight, so a
  // period/department/view change made during a load still takes effect.
  if (append && leaderboardState.isLoading()) return;

  const list = $list();
  if (!list) return;

  const generation = append
    ? leaderboardState.listGeneration
    : leaderboardState.newListGeneration();
  leaderboardState.setLoading(true);

  // Show skeleton on first load
  if (!append) {
    list.innerHTML = createSkeletonList();
    leaderboardState.resetPagination();
  } else {
    // Append mode: disable the load-more btn
    const loadMoreBtn = list.querySelector('[data-action="lb-load-more"]');
    if (loadMoreBtn) {
      loadMoreBtn.dataset.label = loadMoreBtn.textContent.trim();
      loadMoreBtn.disabled = true;
      loadMoreBtn.textContent = 'Loading…';
    }
    list.querySelector('.lb-inline-error')?.remove();
  }

  try {
    const view   = leaderboardState.activeView;
    const period = leaderboardState.activePeriod;
    const dept   = leaderboardState.activeDepartment;
    const page   = leaderboardState.page;

    let html    = '';
    let hasMore = false;

    // ── Dispatch by view ──────────────────────────────────────────────────────

    if (view === 'global') {
      const res = await leaderboardAPI.getGlobal({ period, department: dept, page });
      if (res?.status !== 'success') throw new Error(res?.message || 'Unknown error');

      const { leaderboard: entries, pagination, your_position } = res.data;
      hasMore = pagination?.has_more ?? false;

      if (!append) {
        leaderboardState.setTotalUsers(pagination?.total || 0);

        if (entries.length === 0) {
          // No users match this period/department combo — show a clear
          // message instead of falling through to a misleading nearby block.
          html    = createEmptyState('global', dept);
          hasMore = false;
        } else {
          // Show podium only on first page
          const podiumHTML = page === 1 ? createPodium(entries) : '';
          const onPodium    = page === 1 ? podiumSize(entries) : 0;
          const rows        = entries.map((e, i) => createEntryRow(e, { inPodium: i < onPodium })).join('');
          html = podiumHTML + `<div class="lb-list">${rows}</div>`;

          // Show nearby section if user is not in top entries
          const userInList = entries.some(e => e.is_you);
          if (!userInList && your_position) {
            html += await _buildNearbyHTML(period, dept, your_position.rank);
          }
        }
      } else {
        html = entries.map((e) => createEntryRow(e)).join('');
      }
    }

    else if (view === 'connections') {
      const res = await leaderboardAPI.getConnections({ period });
      if (res?.status !== 'success') throw new Error(res?.message || 'Unknown error');

      const { leaderboard: entries } = res.data;
      const onPodium = podiumSize(entries);
      const rows = entries.map((e, i) => createEntryRow(e, { inPodium: i < onPodium })).join('');
      html = createPodium(entries) + `<div class="lb-list">${rows}</div>`;
      hasMore = false; // connections always returns full list
    }

    else if (view === 'rising') {
      const res = await leaderboardAPI.getRising({ limit: 20, department: dept });
      if (res?.status !== 'success') throw new Error(res?.message || 'Unknown error');

      const { rising_stars } = res.data;
      if (rising_stars.length === 0) {
        html = createEmptyState('rising', dept);
      } else {
        const rows = rising_stars.map(createRisingRow).join('');
        html = `<div class="lb-list lb-list-rising">${rows}</div>`;
      }
      hasMore = false;
    }

    else if (view === 'me') {
      const res = await leaderboardAPI.getMyRank({ period, department: dept });
      if (res?.status !== 'success') throw new Error(res?.message || 'Unknown error');

      leaderboardState.setMyRankData(res.data);
      html    = createMyRankView(res.data);
      hasMore = false;
    }

    // ── Render ────────────────────────────────────────────────────────────────

    // The user changed view/period/department while this was in flight; a newer
    // load owns the list now, so drop this response instead of painting it.
    if (!leaderboardState.isCurrentListGeneration(generation)) return;

    if (!append) {
      list.innerHTML = html || createEmptyState(view);
    } else {
      // Remove the old load-more button, then append new rows
      const existingMore = list.querySelector('.lb-load-more-wrap');
      if (existingMore) existingMore.remove();
      list.insertAdjacentHTML('beforeend', html);
    }

    leaderboardState.setHasMore(hasMore);
    leaderboardState.markLoaded();

    if (hasMore) {
      list.insertAdjacentHTML('beforeend', createLoadMoreBtn());
    }

  } catch (err) {
    console.error('lb: load error', err);
    if (!leaderboardState.isCurrentListGeneration(generation)) return;   // stale failure: ignore

    if (!append) {
      list.innerHTML = createErrorState(err.message || 'Failed to load leaderboard.');
    } else {
      // Give the page number back and make the button usable again, so the
      // user can retry instead of being stuck on a disabled "Loading…".
      leaderboardState.setPage(Math.max(1, leaderboardState.page - 1));
      const btn = list.querySelector('[data-action="lb-load-more"]');
      if (btn) {
        btn.disabled = false;
        btn.textContent = btn.dataset.label || 'Load more';
        btn.closest('.lb-load-more-wrap')?.insertAdjacentHTML(
          'beforebegin',
          '<div class="lb-inline-error" role="alert">Couldn\u2019t load more. Please try again.</div>'
        );
      }
    }
  } finally {
    if (leaderboardState.isCurrentListGeneration(generation)) {
      leaderboardState.setLoading(false);
    }
  }
}


// ─────────────────────────────────────────────────────────────────────────────
// 3b. NEARBY HELPER  (called inline during global load)
// ─────────────────────────────────────────────────────────────────────────────

async function _buildNearbyHTML(period, dept, yourRank) {
  try {
    const res = await leaderboardAPI.getNearby({ period, department: dept, range: 3 });
    if (res?.status === 'success') {
      return createNearbySection(res.data.nearby, yourRank);
    }
  } catch (_) { /* non-critical */ }
  return '';
}


// ─────────────────────────────────────────────────────────────────────────────
// 4. LOAD MORE  (pagination)
// ─────────────────────────────────────────────────────────────────────────────

export async function loadMoreLeaderboard() {
  if (!leaderboardState.canLoadMore()) return;
  leaderboardState.setPage(leaderboardState.page + 1);
  await loadLeaderboard(true);
}


// ─────────────────────────────────────────────────────────────────────────────
// 5. SWITCH VIEW  (global / connections / rising / me)
// ─────────────────────────────────────────────────────────────────────────────

export async function switchView(view) {
  if (leaderboardState.activeView === view && !leaderboardState.isLoading()) return;

  // Update active tab styling
  document.querySelectorAll('.lb-view-tab').forEach(btn => {
    btn.classList.toggle('lb-view-tab-active', btn.dataset.view === view);
  });

  // Some filters are irrelevant for certain views
  const deptFilter = document.getElementById('lb-dept-filter');
  if (deptFilter) {
    deptFilter.style.visibility = (view === 'connections' || view === 'me') ? 'hidden' : 'visible';
  }
  const periodTabs = document.getElementById('lb-period-tabs');
  if (periodTabs) {
    periodTabs.style.visibility = view === 'rising' ? 'hidden' : 'visible';
  }

  leaderboardState.setView(view);
  leaderboardState.resetPagination();
  leaderboardState.setBreakdownVisible(false);
  _closeBreakdown();

  await loadLeaderboard();

  // Refresh the rank strip with the new period if not the 'me' view
  // (in 'me' view the strip overlaps with the full view)
  const stripWrapper = $myStrip();
  if (stripWrapper) {
    stripWrapper.style.display = view === 'me' ? 'none' : '';
  }
}


// ─────────────────────────────────────────────────────────────────────────────
// 6. SET PERIOD
// ─────────────────────────────────────────────────────────────────────────────

export async function setPeriod(period) {
  if (leaderboardState.activePeriod === period) return;

  document.querySelectorAll('.lb-period-tab').forEach(btn => {
    btn.classList.toggle('lb-period-tab-active', btn.dataset.period === period);
  });

  leaderboardState.setPeriod(period);
  leaderboardState.resetPagination();
  if (leaderboardState.breakdownVisible) {        // its numbers belong to the old period
    leaderboardState.setBreakdownVisible(false);
    _closeBreakdown();
  }

  await Promise.all([
    loadLeaderboard(),
    loadMyRankStrip(),
  ]);
}


// ─────────────────────────────────────────────────────────────────────────────
// 7. SET DEPARTMENT
// ─────────────────────────────────────────────────────────────────────────────

export async function setDepartment(dept) {
  if (leaderboardState.activeDepartment === dept) return;

  leaderboardState.setDepartment(dept);
  leaderboardState.resetPagination();

  await Promise.all([
    loadLeaderboard(),
    loadMyRankStrip(),
  ]);
}


// ─────────────────────────────────────────────────────────────────────────────
// 8. SCORE BREAKDOWN PANEL
// ─────────────────────────────────────────────────────────────────────────────

export async function toggleBreakdown() {
  const wrapper = $breakdown();
  if (!wrapper) return;

  const isOpen = !leaderboardState.breakdownVisible;
  leaderboardState.setBreakdownVisible(isOpen);

  if (!isOpen) {
    _closeBreakdown();
    return;
  }

  // Open: load data and render
  wrapper.innerHTML = '<div class="lb-bk-loading">Loading breakdown…</div>';
  wrapper.classList.remove('lb-breakdown-hidden');

  try {
    const res = await leaderboardAPI.getBreakdown({ period: leaderboardState.activePeriod });
    if (res?.status === 'success') {
      wrapper.innerHTML = createBreakdownPanel(res.data);
    } else {
      wrapper.innerHTML = createErrorState('Could not load breakdown.');
    }
  } catch (err) {
    console.error('lb: breakdown error', err);
    wrapper.innerHTML = createErrorState('Could not load breakdown.');
  }
}

function _closeBreakdown() {
  const wrapper = $breakdown();
  if (wrapper) {
    wrapper.classList.add('lb-breakdown-hidden');
    setTimeout(() => { wrapper.innerHTML = ''; }, 300);
  }
}


// ─────────────────────────────────────────────────────────────────────────────
// 9. RETRY  (called from error state button)
// ─────────────────────────────────────────────────────────────────────────────

export async function retryLoad() {
  leaderboardState.resetPagination();
  await loadLeaderboard();
}


// ─────────────────────────────────────────────────────────────────────────────
// 9b. DEPARTMENT FILTER OPTIONS  (populate dynamically from backend)
// ─────────────────────────────────────────────────────────────────────────────

export async function loadDeptFilterOptions() {
  const select = document.getElementById('lb-dept-filter');
  if (!select) return;

  try {
    const res = await leaderboardAPI.getFilters();
    if (res?.status === 'success' && Array.isArray(res.data?.departments)) {
      const current  = select.value;
      const existing = new Set(
        Array.from(select.options).map(o => o.value).filter(Boolean)
      );

      res.data.departments.forEach(dept => {
        if (!dept || existing.has(dept)) return;
        const opt = document.createElement('option');
        opt.value = dept;
        opt.textContent = dept;
        select.appendChild(opt);
        existing.add(dept);
      });

      // Preserve whatever was selected before repopulating
      select.value = current;
    }
  } catch (err) {
    console.error('lb: failed to load department filters', err);
  }
}


// ─────────────────────────────────────────────────────────────────────────────
// 10. FULL INIT LOAD  (called once when section becomes active)
// ─────────────────────────────────────────────────────────────────────────────

export async function initialLoad() {
  await Promise.all([
    loadStatsBanner(),
    loadMyRankStrip(),
    loadLeaderboard(),
    loadDeptFilterOptions(),
  ]);
}

