// Shared helpers for /dashboard and /dashboard/inbound — icons, formatting,
// the KPI summary strip, and the call-volume sparkline. Kept framework-free
// (no build step, no CDN) to match how this app already serves static files.

const ICON = {
  phoneOut: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M15 8a4 4 0 0 1 4 4M15 4a8 8 0 0 1 8 8M22 16.9v2.6a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 3.8 2 2 0 0 1 4.1 1.6h2.6a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L7.6 9.6a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.5c.9.3 1.9.6 2.9.7a2 2 0 0 1 1.7 2Z"/></svg>',
  phoneIn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.9v2.6a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 3.8 2 2 0 0 1 4.1 1.6h2.6a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L7.6 9.6a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.5c.9.3 1.9.6 2.9.7a2 2 0 0 1 1.7 2Z"/><path d="M14 4h6v6M20 4l-7 7"/></svg>',
  building: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 21V5a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v16M14 21v-4a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v4M14 21h6a1 1 0 0 0 1-1V10l-3-3M9 7h.01M9 11h.01M9 15h.01"/></svg>',
  flame: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 17a2.5 2.5 0 0 0 2.5-2.5c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7.5 7.5 0 1 1-15 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5Z"/></svg>',
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  snow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20M17 5l-5 3-5-3M17 19l-5-3-5 3M2 12h20M4.5 7l3 5-3 5M19.5 7l-3 5 3 5"/></svg>',
  calendarCheck: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18M9 16l2 2 4-4"/></svg>',
  rupee: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h12M6 8h12M6 3c4 0 6 2 6 5s-2 5-6 5h-1l7 8"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>',
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5Z"/></svg>',
  mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4M8 22h8"/></svg>',
  mail: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m2 7 10 6 10-6"/></svg>',
  target: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></svg>',
  calendarClock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2v3M3 9h11M5 4h8a2 2 0 0 1 2 2v3.5M3 8v10a2 2 0 0 0 2 2h5.5"/><circle cx="17" cy="17" r="5"/><path d="M17 15.3V17l1.2 1"/></svg>',
  userCheck: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 21v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1"/><circle cx="8" cy="7" r="4"/><path d="m17 11 2 2 4-4"/></svg>',
  globe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
  phoneCallback: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.9v2.6a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 3.8 2 2 0 0 1 4.1 1.6h2.6a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L7.6 9.6a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.5c.9.3 1.9.6 2.9.7a2 2 0 0 1 1.7 2Z"/><path d="M18 6 3 21"/></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  inbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.4 5.3A2 2 0 0 1 7.2 4h9.6a2 2 0 0 1 1.8 1.3L21 12v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6l2.4-6.7Z"/></svg>',
  cpu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="1"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/></svg>',
};

function icon(name, cls) {
  return `<span class="${cls || ''}" aria-hidden="true">${ICON[name] || ''}</span>`;
}

const CLASS_ICON = { Hot: 'flame', Warm: 'sun', Cold: 'snow' };

function classificationTag(classification) {
  if (!classification) return '<span class="muted" style="color: var(--text-faint)">—</span>';
  return `<span class="tag ${classification}">${icon(CLASS_ICON[classification])}${classification}</span>`;
}

function initials(name) {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || name[0].toUpperCase();
}

const AVATAR_COLOR = { Hot: 'var(--hot)', Warm: 'var(--warm)', Cold: 'var(--cold)' };

function avatar(name, classification) {
  const bg = AVATAR_COLOR[classification] || 'var(--text-faint)';
  return `<span class="avatar" style="--avatar-bg:${bg}">${esc(initials(name))}</span>`;
}

function esc(v) {
  if (v === null || v === undefined || v === '') return '';
  return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function fmt(v, fallback) {
  return (v === null || v === undefined || v === '')
    ? (fallback ?? '<span class="empty-val">Not captured yet</span>')
    : esc(v);
}

function meetingBadge(c) {
  if (c.discovery_call_scheduled && c.meeting_link) {
    return `<span class="badge yes">${icon('calendarCheck')}<a href="${c.meeting_link}" target="_blank" onclick="event.stopPropagation()">Booked</a></span>`;
  }
  if (c.discovery_call_scheduled) return `<span class="badge yes">${icon('calendarCheck')}Booked</span>`;
  return `<span class="badge no">—</span>`;
}

function durationLabel(seconds) {
  if (seconds == null) return '—';
  const s = Math.round(seconds);
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return m > 0 ? `${m}m ${rem}s` : `${rem}s`;
}

/** Aggregate KPI strip — computed client-side from the already-loaded call
 * list, no extra backend calls. `extraKpis` lets each page add one or two
 * direction-specific tiles after the shared ones. */
function renderKpiRow(calls, extraKpis) {
  const totalCalls = calls.length;
  const hot = calls.filter(c => c.classification === 'Hot').length;
  const booked = calls.filter(c => c.discovery_call_scheduled).length;
  const totalCostInr = calls.reduce((s, c) => s + (Number(c.total_cost_inr) || 0), 0);
  const latencies = calls.map(c => Number(c.avg_latency_s)).filter(v => !isNaN(v) && v != null);
  const avgLatency = latencies.length ? latencies.reduce((a, b) => a + b, 0) / latencies.length : null;

  const kpis = [
    { icon: 'phoneOut', accent: 'var(--gold)', label: 'Total calls', val: totalCalls.toLocaleString(), sub: 'in this view' },
    { icon: 'flame', accent: 'var(--hot)', label: 'Hot leads', val: hot.toLocaleString(), sub: totalCalls ? `${Math.round(hot / totalCalls * 100)}% of calls` : '—' },
    { icon: 'calendarCheck', accent: 'var(--good)', label: 'Meetings booked', val: booked.toLocaleString(), sub: totalCalls ? `${Math.round(booked / totalCalls * 100)}% conversion` : '—' },
    { icon: 'rupee', accent: 'var(--gold)', label: 'Total cost', val: '₹' + totalCostInr.toFixed(0), sub: booked ? `₹${(totalCostInr / booked).toFixed(0)} / meeting` : 'AI + telephony' },
    { icon: 'clock', accent: 'var(--cold)', label: 'Avg latency', val: avgLatency != null ? avgLatency.toFixed(2) + 's' : '—', sub: 'time to first audio' },
    ...(extraKpis || []),
  ];

  return kpis.map(k => `
    <div class="kpi" style="--kpi-accent:${k.accent}">
      ${icon(k.icon, 'kicon')}
      <div class="klabel">${k.label}</div>
      <div class="kval">${k.val}</div>
      <div class="ksub">${k.sub}</div>
    </div>
  `).join('');
}

/** Small inline sparkline of call volume for the last 14 days — a genuine
 * trend insight derived purely from each call's created_at, no new backend
 * data needed. */
function renderTrendCard(calls, label) {
  const days = 14;
  const buckets = new Array(days).fill(0);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  for (const c of calls) {
    if (!c.created_at) continue;
    const d = new Date(c.created_at);
    d.setHours(0, 0, 0, 0);
    const diff = Math.round((today - d) / 86400000);
    if (diff >= 0 && diff < days) buckets[days - 1 - diff]++;
  }
  const max = Math.max(1, ...buckets);
  const w = 600, h = 36, step = w / (days - 1);
  const points = buckets.map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * h).toFixed(1)}`).join(' ');
  const area = `0,${h} ${points} ${w},${h}`;
  const total = buckets.reduce((a, b) => a + b, 0);

  return `
    <div class="trend-card">
      <div class="tlabel">${icon('cpu')}${label} — last 14 days</div>
      <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <polygon points="${area}" fill="var(--gold)" opacity="0.12"></polygon>
        <polyline points="${points}" fill="none" stroke="var(--gold)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline>
      </svg>
      <div class="tcount">${total} calls</div>
    </div>
  `;
}

/* ── Models picker — per-direction realtime engine/model switcher ──────
 * Backed by GET/POST /api/models (core/model_config.py). Switching a model
 * makes the app restart itself (systemd Restart=on-failure), so the UI here
 * has to survive that: POST, then poll /health until the process has
 * actually cycled, then reload so every panel re-reads the new selection. */

let _modelCatalog = null;
let _modelSelection = null;
let _modelDirection = null;

async function initModelPicker(direction) {
  _modelDirection = direction;
  const badge = document.getElementById('model-badge');
  try {
    const res = await fetch('/api/models');
    if (res.status === 401) { window.location = '/login'; return; }
    const data = await res.json();
    _modelCatalog = data.catalog;
    _modelSelection = data.selection;
    if (badge) badge.textContent = _activeModelLabel();
  } catch (e) {
    if (badge) badge.textContent = 'Model unavailable';
  }
}

function _activeModelLabel() {
  const sel = (_modelSelection || {})[_modelDirection];
  if (!sel) return 'Unknown model';
  const m = (_modelCatalog || []).find(x => x.engine === sel.engine && x.model === sel.model);
  return m ? m.label : `${sel.engine}/${sel.model}`;
}

function openModelModal() {
  const overlay = document.getElementById('model-overlay');
  const modal = document.getElementById('model-modal');
  const sel = (_modelSelection || {})[_modelDirection] || {};

  const byProvider = {};
  (_modelCatalog || []).forEach(m => { (byProvider[m.provider] = byProvider[m.provider] || []).push(m); });

  const groups = Object.entries(byProvider).map(([provider, models]) => `
    <div class="model-group">
      <div class="model-group-label">${esc(provider)}</div>
      ${models.map(m => {
        const active = m.engine === sel.engine && m.model === sel.model;
        return `
        <button class="model-option ${active ? 'active' : ''}" onclick="chooseModel('${m.engine}', '${m.model}')" ${active ? 'disabled' : ''}>
          <div class="model-option-main">
            <span class="model-option-name">${esc(m.label)}</span>
            ${active ? `<span class="model-option-current">${icon('calendarCheck')}Active</span>` : ''}
          </div>
          <div class="model-option-note">${esc(m.note)}</div>
        </button>`;
      }).join('')}
    </div>
  `).join('');

  modal.innerHTML = `
    <div class="modal-head">
      <h2>${icon('cpu')}${_modelDirection === 'inbound' ? 'Inbound' : 'Outbound'} model</h2>
      <button class="modal-close" onclick="closeModelModal()" aria-label="Close">${ICON.close}</button>
    </div>
    <p class="modal-sub">Applies to the next call for this direction — calls already in progress are unaffected.</p>
    <div class="model-groups">${groups}</div>
    <div class="model-status" id="model-status"></div>
  `;
  overlay.classList.add('open');
  modal.classList.add('open');
}

function closeModelModal(e) {
  if (e && e.target !== e.currentTarget) return;
  document.getElementById('model-overlay').classList.remove('open');
  document.getElementById('model-modal').classList.remove('open');
}

async function chooseModel(engine, model) {
  const status = document.getElementById('model-status');
  const m = (_modelCatalog || []).find(x => x.engine === engine && x.model === model);
  const label = m ? m.label : `${engine}/${model}`;

  status.innerHTML = `<span class="spinner"></span> Switching to ${esc(label)}…`;
  document.querySelectorAll('.model-option').forEach(b => b.disabled = true);

  try {
    const res = await fetch(`/api/models/${_modelDirection}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ engine, model }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      status.textContent = `Failed: ${err.detail || res.statusText}`;
      document.querySelectorAll('.model-option').forEach(b => b.disabled = false);
      return;
    }
  } catch (e) {
    status.textContent = 'Failed to reach server.';
    document.querySelectorAll('.model-option').forEach(b => b.disabled = false);
    return;
  }

  // No process restart to wait for — saved to disk and picked up by the
  // very next call for this direction. Just refresh the badge/picker state.
  status.innerHTML = `${icon('calendarCheck')} Saved — ${esc(label)} applies to the next call.`;
  setTimeout(() => window.location.reload(), 900);
}
