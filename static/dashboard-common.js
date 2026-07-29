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
  // Diagonal call-direction arrows — colored red (inbound) / green (outbound)
  // via the wrapping element's CSS color, same convention as every other
  // icon here. Points INTO the corner for inbound, OUT of it for outbound.
  arrowIn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 9v9h9"/></svg>',
  arrowOut: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M6 18 18 6M9 6h9v9"/></svg>',
  menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M3 12h18M3 18h18"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>',
  barChart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18M8 17V10M13 17V6M18 17v-4"/></svg>',
  pieChart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21.2 15.3A10 10 0 1 1 12 2v10z"/></svg>',
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 7H4a1 1 0 0 0-1 1v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8a1 1 0 0 0-1-1Z"/><path d="M20 7V5a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v2"/><circle cx="16" cy="13" r="1.5"/></svg>',
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

function dateLabel(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return '—';
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

const CLASSIFICATION_RANK = { Hot: 3, Warm: 2, Cold: 1 };

/** Groups a flat call list into one entry per contact (same phone number —
 * see core/pending_calls.normalize_phone), most-recent call first within
 * each group and across the returned list. Pass the FULL, unfiltered call
 * list (both directions) so each contact's in/out counts are the true
 * cross-direction totals, not just whatever a single direction-filtered
 * dashboard happens to see — the caller filters the returned contacts down
 * to its own direction afterward (see dashboard.html/dashboard_inbound.html).
 *
 * `latest` (the most recent call) is still exposed for things that are
 * genuinely about "their current situation" — but classification and
 * booking status are NOT that: they're achievements that must not regress
 * just because a later call was a shorter, less-substantive conversation.
 * Confirmed real bug: a contact booked+Hot on an earlier call, then had a
 * brief unrelated call later that only reached Warm with no booking — the
 * dashboard showed Warm and "no meeting", silently erasing what the
 * earlier call achieved. `bestClassification`/`everBooked`/
 * `bookedMeetingLink` are computed across the WHOLE group specifically to
 * fix that; `displayName`/`displayCompany` similarly use the latest call
 * that actually HAS a value, not blindly the latest call overall (a repeat
 * caller who didn't repeat their name shouldn't make an already-known name
 * disappear). */
function groupCallsByContact(allCalls) {
  const groups = new Map();
  for (const c of allCalls) {
    const key = c.normalized_phone || c.phone_number || `id:${c.id}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(c);
  }
  const contacts = [];
  for (const [key, group] of groups) {
    group.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));

    let bestClassification = null, bestRank = 0;
    let everBooked = false, bookedMeetingLink = null, bookedAt = null;
    let displayName = null, displayCompany = null, displayBudget = null, displayTimeline = null, displayDM = null;
    let displayInterestArea = null, displayEmail = null;
    let totalCostInr = 0;
    for (const c of group) {
      const rank = CLASSIFICATION_RANK[c.classification] || 0;
      if (rank > bestRank) { bestRank = rank; bestClassification = c.classification; }
      if (c.discovery_call_scheduled) {
        everBooked = true;
        const t = c.updated_at || c.created_at;
        if (!bookedAt || new Date(t) > new Date(bookedAt)) { bookedMeetingLink = c.meeting_link; bookedAt = t; }
      }
      // Latest call that actually HAS a value, not blindly the latest call
      // overall — a returning caller's brief follow-up call not re-stating
      // their budget/timeline/name shouldn't blank out what an earlier call
      // already captured.
      if (!displayName && c.name) displayName = c.name;
      if (!displayCompany && c.company) displayCompany = c.company;
      if (!displayBudget && c.budget) displayBudget = c.budget;
      if (!displayTimeline && c.timeline) displayTimeline = c.timeline;
      if (!displayDM && c.decision_maker_status) displayDM = c.decision_maker_status;
      if (!displayInterestArea && c.interest_area) displayInterestArea = c.interest_area;
      if (!displayEmail && c.email_id) displayEmail = c.email_id;
      totalCostInr += Number(c.total_cost_inr) || 0;
    }

    contacts.push({
      key,
      latest: group[0],
      calls: group,
      inboundCount: group.filter(c => c.direction === 'inbound').length,
      outboundCount: group.filter(c => c.direction === 'outbound').length,
      displayName: displayName || group[0].name,
      displayCompany: displayCompany || group[0].company,
      displayBudget: displayBudget || group[0].budget,
      displayTimeline: displayTimeline || group[0].timeline,
      displayDecisionMaker: displayDM || group[0].decision_maker_status,
      displayInterestArea: displayInterestArea || group[0].interest_area,
      displayEmail: displayEmail || group[0].email_id,
      bestClassification,
      everBooked,
      bookedMeetingLink,
      totalCostInr,
    });
  }
  contacts.sort((a, b) => new Date(b.latest.created_at || 0) - new Date(a.latest.created_at || 0));
  return contacts;
}

/** Red-inbound / green-outbound call-count pair, shown in place of a flat
 * "N calls" total so both directions are visible at a glance. */
function callCountBadge(contact) {
  return `<span class="call-counts">
    <span class="ccount in" title="${contact.inboundCount} inbound call${contact.inboundCount === 1 ? '' : 's'}">${icon('arrowIn')}${contact.inboundCount}</span>
    <span class="ccount out" title="${contact.outboundCount} outbound call${contact.outboundCount === 1 ? '' : 's'}">${icon('arrowOut')}${contact.outboundCount}</span>
  </span>`;
}

/** Full per-call detail — everything from "Call overview" through
 * "Transcript" for ONE call. Shared by both dashboards' detail panels
 * (identical section-for-section except which icon/label fronts the
 * interest section — outbound calls already know why they're calling,
 * inbound calls are finding out live) so the two pages can't drift out of
 * sync on what a call's detail actually shows. `opts.interestIcon`/
 * `opts.interestLabel` carry that one difference. */
function renderCallDetailSections(c, opts) {
  // AI cost shown in INR, derived from the two top-level totals (never an
  // independent $->₹ conversion) so this figure plus Telephony cost always
  // sums exactly to Total cost below — no rounding-drift, no second
  // exchange-rate figure to keep in sync.
  const aiCostInr = (c.total_cost_inr != null && c.exotel_cost_inr != null)
    ? '₹' + (Number(c.total_cost_inr) - Number(c.exotel_cost_inr)).toFixed(2) : '—';
  const exotelCost = c.exotel_cost_inr != null ? '₹' + Number(c.exotel_cost_inr).toFixed(2) : '—';
  const totalCostV = c.total_cost_inr != null ? '₹' + Number(c.total_cost_inr).toFixed(2) : '—';
  const latency = c.avg_latency_s != null ? Number(c.avg_latency_s).toFixed(2) + 's' : '—';
  const durationS = c.lead_state?.call_metrics?.call_duration_s;
  // Derived from total_cost_inr / duration rather than the stored
  // blended_cost_per_min_inr field — that field only exists on calls
  // processed after it was added; this way every historical call still
  // shows a per-minute figure, not a blank dash.
  const perMinLabel = (c.total_cost_inr != null && durationS > 0)
    ? '₹' + (Number(c.total_cost_inr) / (durationS / 60)).toFixed(2) + ' /min' : '—';
  const duration = durationLabel(durationS);
  const turns = (c.transcript || []).length;

  const priced = c.engine
    ? `<p class="priced-against">Priced against <code>${esc(c.engine)}</code> / <code>${esc(c.model || '—')}</code></p>`
    : '';

  const recordingHtml = c.recording_url
    ? `<audio controls src="${c.recording_url}"></audio>`
    : `<p class="no-recording">No recording available for this call.</p>`;

  const transcriptHtml = (c.transcript || []).length
    ? `<div class="transcript">${c.transcript.map(m => `<div class="msg ${m.role}"><span class="role">${m.role}</span>${esc(m.content)}</div>`).join('')}</div>`
    : `<p class="no-recording">No transcript captured.</p>`;

  const u = c.usage || {};
  const n = (v) => v ?? 0;
  const totalInput = n(u.text_input) + n(u.audio_input);
  const totalOutput = n(u.text_output) + n(u.audio_output);
  const tokenUsageHtml = c.usage ? `
    <div class="tiles">
      <div class="tile">${icon('cpu', 'ticon')}<div class="tlabel">Total input tokens</div><div class="tval">${totalInput.toLocaleString()}</div></div>
      <div class="tile">${icon('cpu', 'ticon')}<div class="tlabel">Total output tokens</div><div class="tval">${totalOutput.toLocaleString()}</div></div>
    </div>
    <table class="utable">
      <thead><tr><th>Breakdown</th><th>Total</th><th>Cached</th></tr></thead>
      <tbody>
        <tr><td>Text input</td><td>${n(u.text_input).toLocaleString()}</td><td>${n(u.text_input_cached).toLocaleString()}</td></tr>
        <tr><td>Audio input</td><td>${n(u.audio_input).toLocaleString()}</td><td>${n(u.audio_input_cached).toLocaleString()}</td></tr>
        <tr><td>Text output</td><td>${n(u.text_output).toLocaleString()}</td><td>—</td></tr>
        <tr><td>Audio output</td><td>${n(u.audio_output).toLocaleString()}</td><td>—</td></tr>
        <tr class="total"><td>Total</td><td>${(totalInput + totalOutput).toLocaleString()}</td><td></td></tr>
      </tbody>
    </table>
  ` : `<p class="no-recording">No token usage recorded for this call.</p>`;

  return `
    <div class="section">
      <h3>${icon(c.direction === 'inbound' ? 'phoneIn' : 'phoneOut')}Call overview</h3>
      <div class="tiles">
        <div class="tile" style="--tile-accent: var(--cold)">${icon('clock', 'ticon')}<div class="tlabel">Duration</div><div class="tval">${duration}</div></div>
        <div class="tile" style="--tile-accent: var(--good)">${icon('chat', 'ticon')}<div class="tlabel">Turns</div><div class="tval">${turns}</div></div>
      </div>
      <dl class="kv" style="margin-top:0.7rem">
        <dt>${icon('globe')}Language</dt><dd>${fmt(c.preferred_language)}</dd>
      </dl>
    </div>
    <div class="section">
      <h3>${icon(opts.interestIcon)}${opts.interestLabel}</h3>
      <dl class="kv">
        <dt>Interest area</dt><dd>${fmt(c.interest_area)}</dd>
        <dt>${icon('mail')}Email</dt><dd>${fmt(c.email_id)}</dd>
      </dl>
    </div>
    <div class="section">
      <h3>${icon('userCheck')}Qualification</h3>
      <dl class="kv">
        <dt>Budget</dt><dd>${fmt(c.budget)}</dd>
        <dt>Timeline</dt><dd>${fmt(c.timeline)}</dd>
        <dt>Decision maker</dt><dd>${fmt(c.decision_maker_status)}</dd>
      </dl>
    </div>
    ${c.callback_time ? `
    <div class="section">
      <h3>${icon('phoneCallback')}Callback requested</h3>
      <dl class="kv"><dt>Time</dt><dd>${esc(c.callback_time)}</dd></dl>
    </div>` : ''}
    <div class="section">
      <h3>${icon('rupee')}Cost &amp; latency</h3>
      ${priced}
      <div class="tiles">
        <div class="tile"><div class="tlabel">AI cost</div><div class="tval">${aiCostInr}</div><div class="tsub">Gemini / OpenAI tokens</div></div>
        <div class="tile"><div class="tlabel">Telephony cost</div><div class="tval">${exotelCost}</div><div class="tsub">Exotel, per-minute</div></div>
        <div class="tile"><div class="tlabel">Total cost</div><div class="tval">${totalCostV}</div><div class="tsub">Blended, INR</div></div>
        <div class="tile"><div class="tlabel">Cost / min</div><div class="tval">${perMinLabel}</div><div class="tsub">AI + telephony, blended</div></div>
        <div class="tile"><div class="tlabel">Avg latency / turn</div><div class="tval">${latency}</div><div class="tsub">Time to first audio</div></div>
      </div>
    </div>
    <div class="section">
      <h3>${icon('cpu')}Token usage</h3>
      ${tokenUsageHtml}
    </div>
    <div class="section">
      <h3>${icon('mic')}Recording</h3>
      ${recordingHtml}
    </div>
    <div class="section">
      <h3>${icon('chat')}Transcript</h3>
      ${transcriptHtml}
    </div>
  `;
}

// ── Multi-call contact detail panel — shared state + renderer ──────────────
// A contact (see groupCallsByContact) can have several calls; the panel
// shows a compact row per call plus one expanded call's full detail
// (renderCallDetailSections above), accordion-style, most recent expanded
// by default rather than dumping every call's full transcript/tokens at once.
let _detailContact = null;
let _detailOpts = null;
let _expandedCallIdx = 0;

function openContactDetail(contact, opts) {
  _detailContact = contact;
  _detailOpts = opts;
  _expandedCallIdx = 0;
  // Header represents the CONTACT overall, so it uses the aggregated
  // best-ever classification/name (see groupCallsByContact) — not
  // contact.latest, which can be a shorter, less-substantive call that
  // shouldn't override what an earlier call already established.
  document.getElementById('d-avatar-wrap').innerHTML = avatar(contact.displayName, contact.bestClassification).replace('class="avatar"', 'class="avatar davatar"');
  document.getElementById('d-name').textContent = contact.displayName || opts.unknownLabel || 'Unknown';
  document.getElementById('d-sub').innerHTML = `${esc(contact.latest.phone_number) || ''}${callCountBadge(contact)}`;
  _renderContactDetailBody();
  document.getElementById('detail').classList.add('open');
  document.getElementById('overlay').classList.add('open');
}

function _renderContactDetailBody() {
  const contact = _detailContact;
  const opts = _detailOpts;
  const rows = contact.calls.map((c, idx) => {
    const expanded = idx === _expandedCallIdx;
    return `
      <div class="call-row ${expanded ? 'expanded' : ''}" onclick="toggleCallDetail(${idx})">
        <span class="crow-dir" style="color: var(${c.direction === 'inbound' ? '--hot' : '--good'})">${icon(c.direction === 'inbound' ? 'arrowIn' : 'arrowOut')}</span>
        <span class="crow-date">${dateLabel(c.created_at)}</span>
        ${classificationTag(c.classification)}
        ${meetingBadge(c)}
        <span class="crow-cost mono">${c.total_cost_inr != null ? '₹' + Number(c.total_cost_inr).toFixed(2) : '—'}</span>
      </div>
      ${expanded ? `<div class="call-detail-expanded">${renderCallDetailSections(c, opts)}</div>` : ''}
    `;
  }).join('');

  document.getElementById('d-body').innerHTML = `
    <div class="section calls-list-section">
      <h3>${icon('chat')}Calls with this contact (${contact.calls.length})</h3>
      <div class="calls-list">${rows}</div>
    </div>
  `;
}

function toggleCallDetail(idx) {
  _expandedCallIdx = (_expandedCallIdx === idx) ? -1 : idx;
  _renderContactDetailBody();
}

/** Shared stat-card component — used by the master dashboard's Overview/
 * Today sections AND (via renderKpiRow below) every other dashboard's KPI
 * strip, so a "stat card" only has one visual definition anywhere in the
 * app, at one size (see dashboard-light.css's .stat-tile). */
function statTile(iconName, accentVar, label, val, sub) {
  return `<div class="stat-tile" style="--stat-accent: var(${accentVar})">
    ${icon(iconName, 'sicon')}
    <div class="slabel">${esc(label)}</div>
    <div class="sval">${val}</div>
    <div class="ssub">${sub || ''}</div>
  </div>`;
}

/** Aggregate KPI strip — computed client-side from the already-loaded call
 * list, no extra backend calls. `extraKpis` lets each page add one or two
 * direction-specific tiles after the shared ones. Renders via statTile()
 * (see above) so this looks identical to every other stat card in the app. */
function renderKpiRow(calls, extraKpis) {
  const totalCalls = calls.length;
  const hot = calls.filter(c => c.classification === 'Hot').length;
  const booked = calls.filter(c => c.discovery_call_scheduled).length;
  const totalCostInr = calls.reduce((s, c) => s + (Number(c.total_cost_inr) || 0), 0);
  const latencies = calls.map(c => Number(c.avg_latency_s)).filter(v => !isNaN(v) && v != null);
  const avgLatency = latencies.length ? latencies.reduce((a, b) => a + b, 0) / latencies.length : null;

  const kpis = [
    { icon: 'phoneOut', accent: '--brand', label: 'Total calls', val: totalCalls.toLocaleString(), sub: 'in this view' },
    { icon: 'flame', accent: '--hot', label: 'Hot leads', val: hot.toLocaleString(), sub: totalCalls ? `${Math.round(hot / totalCalls * 100)}% of calls` : '—' },
    { icon: 'calendarCheck', accent: '--out', label: 'Meetings booked', val: booked.toLocaleString(), sub: totalCalls ? `${Math.round(booked / totalCalls * 100)}% conversion` : '—' },
    { icon: 'rupee', accent: '--warm', label: 'Total cost', val: '₹' + totalCostInr.toFixed(0), sub: booked ? `₹${(totalCostInr / booked).toFixed(0)} / meeting` : 'AI + telephony' },
    { icon: 'clock', accent: '--cold', label: 'Avg latency', val: avgLatency != null ? avgLatency.toFixed(2) + 's' : '—', sub: 'time to first audio' },
    ...(extraKpis || []),
  ];

  // Returns just the tiles — the caller's container element carries the
  // .stat-grid class itself (see dashboard.html/dashboard_inbound.html's
  // #kpis div), same convention dashboard_master.html already uses.
  return kpis.map(k => statTile(k.icon, k.accent, k.label, k.val, k.sub)).join('');
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
  const w = 600, h = 30, step = w / (days - 1);
  const points = buckets.map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * h).toFixed(1)}`).join(' ');
  const area = `0,${h} ${points} ${w},${h}`;
  const total = buckets.reduce((a, b) => a + b, 0);

  return `
    <div class="trend-card">
      <div class="tlabel">${icon('cpu')}${label} — last 14 days</div>
      <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <polygon points="${area}" fill="var(--brand)" opacity="0.12"></polygon>
        <polyline points="${points}" fill="none" stroke="var(--brand)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline>
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

/** Full weekday+date+time formatter for a meeting's preferred_slot ISO
 * string — shared by the Meetings page and the Leads insight panel so both
 * format the same kind of value identically. */
function meetingDateLabel(iso) {
  if (!iso) return '<span class="muted">—</span>';
  const d = new Date(iso);
  if (isNaN(d)) return '<span class="muted">—</span>';
  return d.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function levelBadge(level) {
  if (!level) return '<span class="level-badge unknown">Unknown</span>';
  return `<span class="level-badge ${esc(level)}">${esc(level)}</span>`;
}

/** Leads page insight panel body (see static/dashboard_leads.html) — combines
 * a contact's own deterministic facts (echoed back from core/lead_insights.
 * compute_lead_facts, same numbers already on the Leads row, just shown in
 * full here) with the AI-generated read from POST /api/leads/insights.
 * insight is null while that request is still in flight — callers render
 * once with insight=null for an immediate loading state, then again once it
 * resolves, rather than blocking the panel open on the AI call. */
function renderLeadInsightBody(contact, insight) {
  const facts = (insight && insight.facts) || {};
  const lastCall = contact.calls[contact.calls.length - 1];

  const aiSection = !insight ? `
    <div class="section">
      <h3>${icon('barChart')}AI analysis</h3>
      <div class="insight-loading"><span class="spinner"></span>Analyzing calls…</div>
    </div>
  ` : insight.generated ? `
    <div class="section">
      <h3>${icon('barChart')}AI analysis</h3>
      <p class="insight-summary">${fmt(insight.summary, 'No summary available.')}</p>
      <div class="badge-row">
        <div><div class="mini-label">Interest level</div>${levelBadge(insight.interest_level)}</div>
        <div><div class="mini-label">Conversion potential</div>${levelBadge(insight.conversion_potential)}</div>
      </div>
      ${insight.conversion_reason ? `<p class="insight-reason">${esc(insight.conversion_reason)}</p>` : ''}
      ${insight.intent ? `<dl class="kv" style="margin-top:0.8rem"><dt>${icon('target')}Intent</dt><dd>${esc(insight.intent)}</dd></dl>` : ''}
    </div>
    <div class="section">
      <h3>${icon('chat')}Things they've asked about</h3>
      ${(insight.enquiries || []).length
        ? `<div class="insight-list">${insight.enquiries.map(e => `<div class="insight-list-item">${esc(e)}</div>`).join('')}</div>`
        : '<div class="insight-unavailable">Nothing specific captured yet.</div>'}
    </div>
    ${(insight.notes || []).length ? `
    <div class="section">
      <h3>${icon('userCheck')}Other notes</h3>
      <div class="insight-list">${insight.notes.map(n => `<div class="insight-list-item">${esc(n)}</div>`).join('')}</div>
    </div>` : ''}
  ` : `
    <div class="section">
      <h3>${icon('barChart')}AI analysis</h3>
      <div class="insight-unavailable">AI analysis unavailable right now — showing captured facts only.</div>
    </div>
  `;

  return `
    <div class="section">
      <h3>${icon('userCheck')}Lead overview</h3>
      <div class="tiles">
        <div class="tile"><div class="tlabel">Total calls</div><div class="tval">${facts.total_calls ?? contact.calls.length}</div><div class="tsub">${facts.inbound_calls ?? contact.inboundCount} in &middot; ${facts.outbound_calls ?? contact.outboundCount} out</div></div>
        <div class="tile"><div class="tlabel">Total cost</div><div class="tval">${facts.total_cost_inr != null ? '₹' + Number(facts.total_cost_inr).toFixed(2) : '—'}</div><div class="tsub">AI + telephony, all calls</div></div>
      </div>
      <dl class="kv" style="margin-top:0.7rem">
        <dt>${icon('mail')}Email</dt><dd>${fmt(contact.displayEmail)}</dd>
        <dt>Interest area</dt><dd>${fmt(facts.interest_area || contact.displayInterestArea)}</dd>
        <dt>First contact</dt><dd>${dateLabel(facts.first_contact_at || (lastCall && lastCall.created_at))}</dd>
        <dt>Last contact</dt><dd>${dateLabel(facts.last_contact_at || contact.latest.created_at)}</dd>
      </dl>
    </div>
    <div class="section">
      <h3>${icon('userCheck')}Qualification</h3>
      <dl class="kv">
        <dt>Budget</dt><dd>${fmt(facts.budget || contact.displayBudget)}</dd>
        <dt>Timeline</dt><dd>${fmt(facts.timeline || contact.displayTimeline)}</dd>
        <dt>Decision maker</dt><dd>${fmt(facts.decision_maker_status || contact.displayDecisionMaker)}</dd>
      </dl>
    </div>
    <div class="section">
      <h3>${icon('calendarClock')}Meeting &amp; scheduling</h3>
      <dl class="kv">
        <dt>Status</dt><dd>${meetingBadge({ discovery_call_scheduled: facts.ever_booked_meeting ?? contact.everBooked, meeting_link: facts.meeting_link || contact.bookedMeetingLink })}</dd>
        <dt>Meeting date</dt><dd>${meetingDateLabel(facts.meeting_date)}</dd>
        <dt>Reschedules</dt><dd>${facts.reschedule_count ?? 0}</dd>
      </dl>
    </div>
    ${aiSection}
  `;
}

/** Collapsible sidebar — shared across all four dashboard pages. Persists
 * collapsed/expanded state in localStorage so it doesn't reset every page
 * navigation (each page is a separate document load, no client-side
 * router). Call once per page after the sidebar markup is in the DOM. */
function initSidebar() {
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('sidebar-toggle');
  if (!sidebar || !toggle) return;
  toggle.innerHTML = icon('menu');
  if (localStorage.getItem('mira-sidebar-collapsed') === '1') {
    sidebar.classList.add('collapsed');
  }
  toggle.addEventListener('click', () => {
    sidebar.classList.toggle('collapsed');
    localStorage.setItem('mira-sidebar-collapsed', sidebar.classList.contains('collapsed') ? '1' : '0');
  });
}
