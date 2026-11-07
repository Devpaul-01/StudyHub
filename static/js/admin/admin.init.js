/**
 * ============================================================================
 * ADMIN PANEL — admin.init.js
 *
 * Batch 5: this is the first UI for the four admin routes in
 * routes/admin.py (health, scheduler, ai-providers, reconciliation),
 * which already existed as a working, admin_required-gated JSON API with
 * no frontend calling any of them. Also wires up the admin/moderator
 * -only POST /leaderboard/snapshot (routes/student/leaderboard.py),
 * flagged as the fifth admin-facing route in the original audit.
 *
 * Uses the global `api` client from core/api.js, loaded by base.html —
 * same convention as search.init.js and profile-view.init.js.
 * ============================================================================
 */

function $(id) { return document.getElementById(id); }
function escapeHtml(str) {
  if (str == null) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Health ──────────────────────────────────────────────────────────────

async function loadHealth() {
  const el = $('admin-health-body');
  el.textContent = 'Loading…';
  try {
    const res = await api.get('/admin/health');
    if (res?.status !== 'success') {
      el.innerHTML = `<div class="admin-error">${escapeHtml(res?.message || 'Could not load')}</div>`;
      return;
    }
    const d = res.data;
    const statusClass = (ok) => ok ? 'admin-status-ok' : 'admin-status-error';
    el.innerHTML = `
      <div class="admin-status-row"><span>Database</span>
        <span class="${statusClass(d.database.connected)}">${d.database.connected ? 'Connected' : 'Down'}</span></div>
      <div class="admin-status-row"><span>Redis</span>
        <span class="${statusClass(d.redis.connected)}">${d.redis.connected ? `Connected (${d.redis.latency_ms}ms)` : 'Down'}</span></div>
      <div class="admin-status-row"><span>Email configured</span>
        <span class="${d.email_configured ? 'admin-status-ok' : 'admin-status-warn'}">${d.email_configured ? 'Yes' : 'No'}</span></div>
      <div class="admin-status-row"><span>Rate limiter storage</span><span>${escapeHtml(d.rate_limiter_storage)}</span></div>
      <div class="admin-status-row"><span>Environment</span><span>${escapeHtml(d.environment)}</span></div>
    `;
  } catch (error) {
    console.error('Admin health error:', error);
    el.innerHTML = `<div class="admin-error">Could not load health status.</div>`;
  }
}

// ── Scheduler ───────────────────────────────────────────────────────────

async function loadScheduler() {
  const el = $('admin-scheduler-body');
  el.textContent = 'Loading…';
  try {
    const res = await api.get('/admin/scheduler');
    if (res?.status !== 'success') {
      el.innerHTML = `<div class="admin-error">${escapeHtml(res?.message || 'Could not load')}</div>`;
      return;
    }
    const d = res.data;
    const jobRows = (d.jobs || []).map(j => `
      <div class="admin-status-row">
        <span>${escapeHtml(j.name || j.id)}</span>
        <span>${j.next_run_time ? new Date(j.next_run_time).toLocaleString() : '—'}</span>
      </div>`).join('');
    el.innerHTML = `
      <div class="admin-status-row"><span>Running</span>
        <span class="${d.running ? 'admin-status-ok' : 'admin-status-error'}">${d.running ? 'Yes' : 'No'}</span></div>
      ${jobRows || '<div class="admin-card-desc">No scheduled jobs.</div>'}
    `;
  } catch (error) {
    console.error('Admin scheduler error:', error);
    el.innerHTML = `<div class="admin-error">Could not load scheduler status.</div>`;
  }
}

// ── AI Providers ────────────────────────────────────────────────────────

async function loadProviders() {
  const el = $('admin-providers-body');
  el.textContent = 'Loading…';
  try {
    const res = await api.get('/admin/ai-providers');
    if (res?.status !== 'success') {
      el.innerHTML = `<div class="admin-error">${escapeHtml(res?.message || 'Could not load')}</div>`;
      return;
    }
    const d = res.data;
    const providerRows = (d.providers || []).map(p => `
      <div class="admin-status-row">
        <span>${escapeHtml(p.name)}</span>
        <span class="${p.key_failed || p.type_blacklisted ? 'admin-status-error' : 'admin-status-ok'}">
          ${p.type_blacklisted ? 'Blacklisted' : p.key_failed ? 'Key cooling' : 'OK'}
        </span>
      </div>`).join('');
    el.innerHTML = `
      <div class="admin-status-row"><span>Active / Total</span><span>${d.active_providers} / ${d.total_providers}</span></div>
      <div class="admin-status-row"><span>Current</span><span>${escapeHtml(d.current_provider || '—')}</span></div>
      <div class="admin-status-row"><span>State source</span><span>${escapeHtml(d.state_source)}</span></div>
      ${providerRows}
    `;
  } catch (error) {
    console.error('Admin providers error:', error);
    el.innerHTML = `<div class="admin-error">Could not load provider status.</div>`;
  }
}

// ── Reconciliation ──────────────────────────────────────────────────────

async function runReconciliation() {
  const btn = $('admin-reconciliation-btn');
  const resultEl = $('admin-reconciliation-result');
  btn.disabled = true;
  btn.textContent = 'Running…';
  resultEl.textContent = '';
  try {
    const res = await api.post('/admin/reconciliation/run');
    if (res?.status === 'success') {
      const d = res.data;
      resultEl.textContent =
        `Checked ${d.counters_checked} counters — ${d.drifts_found} drift(s) found, ` +
        `${d.corrected_count} corrected, ${d.alerted_only_count} alerted-only.`;
    } else {
      resultEl.textContent = res?.message || 'Reconciliation failed.';
    }
  } catch (error) {
    console.error('Reconciliation error:', error);
    resultEl.textContent = 'Reconciliation failed.';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run Now';
  }
}

// ── Leaderboard snapshot ────────────────────────────────────────────────

async function runSnapshot(type) {
  const resultEl = $('admin-snapshot-result');
  resultEl.textContent = 'Running…';
  try {
    const res = await api.post('/leaderboard/snapshot', { type });
    if (res?.status === 'success') {
      const d = res.data;
      resultEl.textContent = `Snapshot (${type}): ${d.created} created, ${d.skipped} skipped, ${d.total_ranked} ranked.`;
    } else {
      resultEl.textContent = res?.message || 'Snapshot failed.';
    }
  } catch (error) {
    console.error('Snapshot error:', error);
    resultEl.textContent = 'Snapshot failed.';
  }
}

// ── Moderation queue ────────────────────────────────────────────────────

async function loadReports() {
  const el = $('admin-mod-reports');
  el.textContent = 'Loading…';
  const status = $('admin-mod-status-filter')?.value || 'pending';
  const type   = $('admin-mod-type-filter')?.value || 'all';

  try {
    const res = await api.get(`/admin/moderation/reports?status=${status}&type=${type}`);
    if (res?.status !== 'success') {
      el.innerHTML = `<div class="admin-error">${escapeHtml(res?.message || 'Could not load reports')}</div>`;
      return;
    }
    const reports = res.data.reports || [];
    if (!reports.length) {
      el.innerHTML = `<div class="admin-card-desc">No reports match this filter.</div>`;
      return;
    }
    el.innerHTML = reports.map(reportCard).join('');
  } catch (error) {
    console.error('Load reports error:', error);
    el.innerHTML = `<div class="admin-error">Could not load reports.</div>`;
  }
}

function reportCard(r) {
  const preview = r.content_preview;
  const previewText = r.type === 'post'
    ? (preview ? `${escapeHtml(preview.title || '(untitled)')} — ${escapeHtml(preview.excerpt || '')}` : '(post not found)')
    : (preview ? escapeHtml(preview.body || '') : '(message not found)');

  const actionsHtml = r.status === 'pending' ? `
    <div class="admin-mod-actions">
      <select class="admin-mod-action-select">
        <option value="content_removed">Remove content</option>
        <option value="warning_issued">Issue warning</option>
        <option value="no_action">No action needed</option>
      </select>
      <input type="text" class="admin-mod-notes-input" placeholder="Notes (optional)">
      <button class="admin-action-btn" data-action="admin-resolve-report"
              data-report-id="${r.id}" data-report-type="${r.type}">Resolve</button>
    </div>
  ` : `<div class="admin-mod-meta">Resolved by ${escapeHtml(r.reviewer?.name || '—')} • ${r.action_taken ? escapeHtml(r.action_taken) : ''}</div>`;

  return `
    <div class="admin-mod-report" data-report-id="${r.id}" data-report-type="${r.type}">
      <div class="admin-mod-report-header">
        <span class="admin-mod-badge">${escapeHtml(r.type)} • ${escapeHtml(r.reason)}</span>
        <span class="admin-mod-meta">${r.reported_at ? new Date(r.reported_at).toLocaleString() : ''}</span>
      </div>
      <div class="admin-mod-preview">${previewText}</div>
      <div class="admin-mod-meta">
        Reported by ${escapeHtml(r.reporter?.name || 'Unknown')}
        ${r.description ? ` — "${escapeHtml(r.description)}"` : ''}
      </div>
      ${actionsHtml}
    </div>`;
}

async function resolveReport(reportId, reportType, card) {
  const select = card.querySelector('.admin-mod-action-select');
  const notesInput = card.querySelector('.admin-mod-notes-input');
  const btn = card.querySelector('[data-action="admin-resolve-report"]');

  btn.disabled = true;
  btn.textContent = 'Resolving…';
  try {
    const res = await api.post(`/admin/moderation/reports/${reportType}/${reportId}/resolve`, {
      action_taken: select.value,
      review_notes: notesInput.value.trim(),
    });
    if (res?.status === 'success') {
      loadReports(); // simplest correct refresh — re-fetches the current filter
    } else {
      alert(res?.message || 'Could not resolve report');
      btn.disabled = false;
      btn.textContent = 'Resolve';
    }
  } catch (error) {
    console.error('Resolve report error:', error);
    alert('Could not resolve report');
    btn.disabled = false;
    btn.textContent = 'Resolve';
  }
}

// ── Delegation ──────────────────────────────────────────────────────────

function setupDelegation() {
  document.addEventListener('click', (event) => {
    const target = event.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;

    if (action === 'admin-refresh-health')        loadHealth();
    else if (action === 'admin-refresh-scheduler') loadScheduler();
    else if (action === 'admin-refresh-providers') loadProviders();
    else if (action === 'admin-run-reconciliation') runReconciliation();
    else if (action === 'admin-run-snapshot')        runSnapshot(target.dataset.type);
    else if (action === 'admin-refresh-reports')     loadReports();
    else if (action === 'admin-resolve-report') {
      const card = target.closest('.admin-mod-report');
      if (card) resolveReport(target.dataset.reportId, target.dataset.reportType, card);
    }
  });

  $('admin-mod-status-filter')?.addEventListener('change', loadReports);
  $('admin-mod-type-filter')?.addEventListener('change', loadReports);
}

document.addEventListener('DOMContentLoaded', () => {
  setupDelegation();
  loadHealth();
  loadScheduler();
  loadProviders();
  loadReports();

  // AI provider health is the one card worth auto-refreshing — a
  // provider going down mid-session is the most time-sensitive thing
  // here; the others are checked on demand via their refresh buttons.
  setInterval(loadProviders, 30000);
});
