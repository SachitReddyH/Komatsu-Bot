/* ═══════════════════════════════════════════════════════════════════
   KOMATSU WATCHER BOT  ·  Dashboard App
   YANTRA LIVE
   ═══════════════════════════════════════════════════════════════════ */

'use strict';

/* ── Config ──────────────────────────────────────────────────────── */
const API = '';          // same origin – FastAPI serves this file
const POLL_INTERVAL = 30_000;   // 30 s auto-refresh

/* ── State ───────────────────────────────────────────────────────── */
let state = {
  listings: [],
  filtered: [],
  status: null,
  intervalMinutes: 60,
  nextRun: null,
  enquiryListingId: null,
};

/* ═══════════════════════════════════════════════════════════════════
   DATA FETCHING
   ═══════════════════════════════════════════════════════════════════ */

async function fetchStatus() {
  try {
    const r = await fetch(`${API}/api/status`);
    if (!r.ok) throw new Error(r.statusText);
    state.status = await r.json();
    renderStatus();
  } catch (e) {
    setStatusOffline();
    console.error('Status fetch failed:', e);
  }
}

async function fetchListings() {
  try {
    const r = await fetch(`${API}/api/listings?limit=200`);
    if (!r.ok) throw new Error(r.statusText);
    const data = await r.json();
    const prev = state.listings.length;
    state.listings = data.listings || [];
    filterListings();
    if (prev > 0 && state.listings.length > prev) {
      const n = state.listings.length - prev;
      toast('success', `${n} New Listing${n > 1 ? 's' : ''}!`,
        `${n} new equipment listing${n > 1 ? 's' : ''} found.`);
    }
  } catch (e) {
    console.error('Listings fetch failed:', e);
  }
}

async function fetchHistory() {
  try {
    const r = await fetch(`${API}/api/history?limit=50`);
    if (!r.ok) throw new Error(r.statusText);
    const data = await r.json();
    renderHistory(data.runs || []);
  } catch (e) {
    console.error('History fetch failed:', e);
  }
}

async function refresh() {
  await Promise.all([fetchStatus(), fetchListings()]);
}

/* ═══════════════════════════════════════════════════════════════════
   RENDER – STATUS + SIDEBAR
   ═══════════════════════════════════════════════════════════════════ */

function renderStatus() {
  const s = state.status;
  if (!s) return;

  /* Status pill */
  const dot  = document.getElementById('status-dot');
  const text = document.getElementById('status-text');

  if (s.check_in_progress) {
    dot.className = 'status-dot checking';
    text.textContent = 'Checking…';
  } else if (s.scheduler_running) {
    dot.className = 'status-dot live';
    text.textContent = 'Live';
  } else {
    dot.className = 'status-dot offline';
    text.textContent = 'Offline';
  }

  /* Last check */
  const lc = document.getElementById('last-check');
  lc.textContent = s.last_run
    ? 'Last check: ' + fmtTime(s.last_run)
    : 'No checks run yet';

  /* Stats strip */
  document.getElementById('stat-total').textContent  = s.total_listings ?? '—';
  document.getElementById('stat-models').textContent = (s.targets || []).length;
  document.getElementById('stat-checks').textContent = s.total_checks ?? '—';
  document.getElementById('stat-new').textContent    = s.new_24h ?? '—';

  /* Targets list */
  const tl = document.getElementById('targets-list');
  const targets = s.targets || [];
  if (targets.length === 0) {
    tl.innerHTML = `<div style="color:var(--txt-3);font-size:12px;line-height:1.6;">
      No targets yet.<br>
      <span style="color:var(--acc);cursor:pointer;text-decoration:underline" onclick="openAddTarget()">+ Add your first target</span>
    </div>`;
  } else {
    tl.innerHTML = targets.map((t, i) => `
      <div class="target-item">
        <div class="target-item-top">
          <div class="target-model">${esc(t.model)}</div>
          <button class="target-delete-btn" onclick="deleteTarget(${i})" title="Remove this target">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
        <div class="target-meta">${buildTargetMeta(t)}</div>
      </div>
    `).join('');
  }

  /* Activity */
  const runs = s.recent_runs || [];
  const al = document.getElementById('activity-list');
  if (runs.length === 0) {
    al.innerHTML = '<div style="color:var(--txt-3);font-size:12px;">No runs recorded yet.</div>';
  } else {
    al.innerHTML = runs.map(r => `
      <div class="activity-row">
        <span class="activity-time">${fmtTime(r.timestamp)}</span>
        <span class="activity-badge ${r.new_found > 0 ? 'activity-badge--new' : 'activity-badge--zero'}">
          ${r.new_found > 0 ? '+' + r.new_found : '0'}
        </span>
        <span class="activity-models">${esc(r.models || '')}</span>
      </div>
    `).join('');
  }

  /* Countdown */
  state.intervalMinutes = s.interval_minutes || 60;
  state.nextRun = s.next_run ? new Date(s.next_run) : null;
  document.getElementById('interval-label').textContent =
    `every ${state.intervalMinutes} min`;
  tickCountdown();
}

function setStatusOffline() {
  const dot  = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  dot.className  = 'status-dot offline';
  text.textContent = 'Offline';
}

function buildTargetMeta(t) {
  const parts = [];
  if (t.year_min || t.year_max) {
    const yr = [t.year_min || 'Any', t.year_max || 'Any'].join(' – ');
    parts.push('Year: ' + yr);
  }
  if (t.price_max) parts.push('Max $' + fmtNum(t.price_max));
  if (t.type)      parts.push(t.type);
  return parts.join('  ·  ') || 'No filters';
}

/* ═══════════════════════════════════════════════════════════════════
   RENDER – LISTINGS GRID
   ═══════════════════════════════════════════════════════════════════ */

function filterListings() {
  const q = (document.getElementById('search-input')?.value || '').toLowerCase();
  const sort = document.getElementById('sort-select')?.value || 'newest';

  let arr = [...state.listings];

  /* Search filter */
  if (q) {
    arr = arr.filter(item => {
      const d = item.data || {};
      return (d.title || '').toLowerCase().includes(q)
          || (d.location || '').toLowerCase().includes(q)
          || (d.seller_name || '').toLowerCase().includes(q);
    });
  }

  /* Sort */
  arr.sort((a, b) => {
    const da = a.data || {}, db = b.data || {};
    if (sort === 'price-asc')  return (da.price_int || 0) - (db.price_int || 0);
    if (sort === 'price-desc') return (db.price_int || 0) - (da.price_int || 0);
    // newest: by first_seen desc
    return (b.first_seen || '').localeCompare(a.first_seen || '');
  });

  state.filtered = arr;
  renderListings(arr);
}

function renderListings(listings) {
  const grid  = document.getElementById('listings-grid');
  const empty = document.getElementById('empty-state');
  const count = document.getElementById('listing-count');

  if (listings.length === 0 && state.listings.length === 0) {
    grid.innerHTML  = '';
    empty.style.display = 'flex';
    count.textContent = '0 listings';
    return;
  }

  empty.style.display = 'none';
  count.textContent = listings.length + ' listing' + (listings.length !== 1 ? 's' : '');

  if (listings.length === 0) {
    grid.innerHTML = '<div style="color:var(--txt-3);font-size:13px;padding:40px;grid-column:1/-1;text-align:center;">No listings match your search.</div>';
    return;
  }

  grid.innerHTML = listings.map((item, idx) => cardHTML(item, idx)).join('');
}

function cardHTML(item, idx) {
  const d = item.data || {};
  const isNew = isNewToday(item.first_seen);
  const imgSrc = d.image_url || '';
  const price  = d.price || 'N/A';
  const isPoa  = !d.price_int;

  /* Delay each card slightly for a staggered entrance */
  const delay = Math.min(idx * 40, 400);

  const imgContent = imgSrc
    ? `<img class="card-img" src="${esc(imgSrc)}" alt="${esc(d.title)}" loading="lazy" onerror="this.parentNode.innerHTML=placeholderSVG('${esc(d.title || '')}')"/>`
    : placeholderHTML(d.title || '');

  return `
    <div class="card" style="animation-delay:${delay}ms">
      <div class="card-img-wrap">
        ${imgContent}
        <div class="card-badges">
          <div>${isNew ? '<span class="badge badge-new">New</span>' : ''}</div>
          ${d.year ? `<span class="badge badge-year">${d.year}</span>` : ''}
        </div>
      </div>

      <div class="card-body">
        <div>
          <div class="card-title">${esc(d.title || 'Unknown Model')}</div>
          <div class="card-category">${esc(d.category_type || '')}${d.category_subtype && d.category_subtype !== d.category_type ? ' · ' + esc(d.category_subtype) : ''}</div>
        </div>

        <div class="${isPoa ? 'card-price card-price--poa' : 'card-price'}">
          ${esc(price)}
        </div>

        <div class="card-meta">
          <div class="meta-row">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
            ${esc(d.seller_name || '—')}
          </div>
          ${d.seller_phone && d.seller_phone !== 'N/A' ? `
          <div class="meta-row">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 13.5 19.79 19.79 0 0 1 1.62 4.88 2 2 0 0 1 3.6 2.69h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L7.91 10a16 16 0 0 0 6 6l.92-.92a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 21.73 17z"/></svg>
            <a class="meta-phone" href="tel:${esc(d.seller_phone)}">${esc(d.seller_phone)}</a>
          </div>` : ''}
          <div class="meta-row">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
            ${esc(d.location || '—')}
          </div>
          <div class="meta-row" style="font-size:11px;color:var(--txt-3)">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            Found ${fmtRelative(item.first_seen)}
          </div>
        </div>
      </div>

      <div class="card-footer">
        <a class="card-btn card-btn--view" href="${esc(d.detail_url || d.komatsu_url || '#')}" target="_blank" rel="noopener">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
          View Listing
        </a>
        <button class="card-btn card-btn--enquiry" onclick="openEnquiry('${esc(d.id)}')">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
          Enquire
        </button>
      </div>
    </div>
  `;
}

function placeholderHTML(title) {
  return `
    <div class="card-img-placeholder">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><rect x="1" y="8" width="22" height="10" rx="2"/><polyline points="5 8 7 2 17 2 19 8"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/></svg>
      <span>${esc(title.replace(/^[\d\s]+KOMATSU\s*/i, '').substring(0, 20))}</span>
    </div>`;
}

// Called from onerror on broken img tags
window.placeholderSVG = function(title) {
  return placeholderHTML(title);
};

/* ═══════════════════════════════════════════════════════════════════
   HISTORY PANEL
   ═══════════════════════════════════════════════════════════════════ */

function renderHistory(runs) {
  const tbody = document.getElementById('history-body');
  if (!runs.length) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--txt-3);padding:20px;">No history yet.</td></tr>';
    return;
  }
  tbody.innerHTML = runs.map(r => `
    <tr>
      <td class="mono">${fmtTime(r.timestamp)}</td>
      <td>${esc(r.models || '—')}</td>
      <td class="mono">${r.total_found || 0}</td>
      <td>${r.new_found > 0
        ? `<span class="pill-new">+${r.new_found}</span>`
        : `<span class="pill-zero">0</span>`
      }</td>
    </tr>
  `).join('');
}

function toggleHistory() {
  const panel = document.getElementById('history-panel');
  const overlay = document.getElementById('history-overlay');
  const open = panel.classList.toggle('open');
  overlay.classList.toggle('open', open);
  if (open) fetchHistory();
}

/* ═══════════════════════════════════════════════════════════════════
   ENQUIRY MODAL
   ═══════════════════════════════════════════════════════════════════ */

function openEnquiry(listingId) {
  const item = state.listings.find(l => (l.data || {}).id === listingId);
  if (!item) return;
  const d = item.data || {};

  state.enquiryListingId = listingId;

  document.getElementById('modal-listing-title').textContent = d.title || '—';

  /* Info strip */
  document.getElementById('modal-listing-info').innerHTML = `
    <div class="modal-info-item">
      <span class="modal-info-label">Price</span>
      <span class="modal-info-value modal-info-value--price">${esc(d.price || 'N/A')}</span>
    </div>
    <div class="modal-info-item">
      <span class="modal-info-label">Dealer</span>
      <span class="modal-info-value">${esc(d.seller_name || '—')}</span>
    </div>
    <div class="modal-info-item">
      <span class="modal-info-label">Phone</span>
      <span class="modal-info-value">${esc(d.seller_phone || '—')}</span>
    </div>
    <div class="modal-info-item">
      <span class="modal-info-label">Location</span>
      <span class="modal-info-value">${esc(d.location || '—')}</span>
    </div>
  `;

  /* Pre-fill saved values */
  const saved = getSavedEnquiryDefaults();
  if (saved.phone) document.getElementById('enq-phone').value = saved.phone;
  if (saved.email) document.getElementById('enq-email').value = saved.email;

  /* Default message */
  document.getElementById('enq-message').value =
    `Hi,\n\nWe are interested in the ${d.title} listed at ${d.price}.\nPlease provide availability and arrange a call at your earliest convenience.\n\nThank you,\nYANTRA LIVE`;

  openModal();
}

function openModal() {
  document.getElementById('modal-overlay').classList.add('open');
  document.getElementById('enquiry-modal').classList.add('open');
  document.getElementById('enq-phone').focus();
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
  document.getElementById('enquiry-modal').classList.remove('open');
  state.enquiryListingId = null;
  setBtnLoading('btn-send-enquiry', false);
}

async function submitEnquiry() {
  const phone  = document.getElementById('enq-phone').value.trim();
  const email  = document.getElementById('enq-email').value.trim();
  const msg    = document.getElementById('enq-message').value.trim();
  const auto   = document.getElementById('enq-auto-submit').checked;

  if (!phone) { shake('enq-phone'); toast('error', 'Phone required', 'Please enter your phone number.'); return; }
  if (!email) { shake('enq-email'); toast('error', 'Email required', 'Please enter your email address.'); return; }

  /* Save defaults for next time */
  saveEnquiryDefaults({ phone, email });

  setBtnLoading('btn-send-enquiry', true, 'Opening browser…');

  try {
    const r = await fetch(`${API}/api/enquiry`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        listing_id: state.enquiryListingId,
        phone, email,
        message: msg,
        auto_submit: auto,
      }),
    });

    const data = await r.json();
    if (r.ok && data.success) {
      toast('success', 'Enquiry sent!', `Enquiry for ${data.listing || 'listing'} submitted.`);
      closeModal();
    } else {
      throw new Error(data.detail || 'Enquiry failed');
    }
  } catch (e) {
    toast('error', 'Enquiry failed', e.message || 'Check the console for details.');
    setBtnLoading('btn-send-enquiry', false);
  }
}

/* ═══════════════════════════════════════════════════════════════════
   TARGET MANAGEMENT
   ═══════════════════════════════════════════════════════════════════ */

function openAddTarget() {
  document.getElementById('addtarget-overlay').classList.add('open');
  document.getElementById('addtarget-modal').classList.add('open');
  setTimeout(() => document.getElementById('at-model').focus(), 80);
}

function closeAddTarget() {
  document.getElementById('addtarget-overlay').classList.remove('open');
  document.getElementById('addtarget-modal').classList.remove('open');
  ['at-model', 'at-type', 'at-year-min', 'at-year-max', 'at-price-min', 'at-price-max'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
}

async function saveTarget() {
  const model = (document.getElementById('at-model').value || '').trim().toUpperCase();
  if (!model) {
    shake('at-model');
    toast('error', 'Model required', 'Enter the model name (e.g. HD785).');
    return;
  }

  const payload = {
    model,
    type:      (document.getElementById('at-type').value || '').trim(),
    year_min:  parseInt(document.getElementById('at-year-min').value) || null,
    year_max:  parseInt(document.getElementById('at-year-max').value) || null,
    price_min: parseInt(document.getElementById('at-price-min').value) || null,
    price_max: parseInt(document.getElementById('at-price-max').value) || null,
  };

  setBtnLoading('btn-save-target', true, 'Saving…');
  try {
    const r = await fetch(`${API}/api/targets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await r.json();
    if (r.ok) {
      toast('info', 'Target added', `Searching for ${data.added.model} listings…`);
      closeAddTarget();
      // The backend seeds listings in a background thread.
      // Poll a few times so results appear in the UI as soon as they're ready.
      await refresh();
      setTimeout(refresh, 2000);
      setTimeout(() => {
        refresh();
        toast('success', `${data.added.model} ready`, 'Dashboard updated. Watcher will alert for any new listings.');
      }, 5000);
    } else {
      throw new Error(data.detail || 'Failed to add target');
    }
  } catch (e) {
    toast('error', 'Failed', e.message);
  } finally {
    setBtnLoading('btn-save-target', false);
  }
}

async function deleteTarget(index) {
  const targets = (state.status || {}).targets || [];
  const t = targets[index];
  if (!t) return;
  if (!confirm(`Stop watching "${t.model}"?\n\nThis removes it from the watcher. Existing listings stay in the database.`)) return;

  try {
    const r = await fetch(`${API}/api/targets/${index}`, { method: 'DELETE' });
    const data = await r.json();
    if (r.ok) {
      const n = data.listings_deleted || 0;
      const modelName = data.removed?.model || t.model;
      toast('success', 'Target removed',
        `Stopped watching ${modelName}. ${n > 0 ? `${n} listing${n !== 1 ? 's' : ''} removed from dashboard.` : 'No listings were stored for this model.'}`
      );
      await refresh();
    } else {
      throw new Error(data.detail || 'Failed to remove target');
    }
  } catch (e) {
    toast('error', 'Failed', e.message);
  }
}

/* ═══════════════════════════════════════════════════════════════════
   RUN CHECK
   ═══════════════════════════════════════════════════════════════════ */

async function runCheck() {
  const btn = document.getElementById('btn-check');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Checking…';

  try {
    const r = await fetch(`${API}/api/check`, { method: 'POST' });
    const data = await r.json();

    if (data.started) {
      toast('info', 'Check started', 'Agent 1 is scanning for new listings…');
      /* Poll more aggressively for 30 s */
      let polls = 0;
      const interval = setInterval(async () => {
        await refresh();
        polls++;
        if (polls >= 6) clearInterval(interval);
      }, 5000);
    } else {
      toast('warn', 'Already running', 'A check is already in progress.');
    }
  } catch (e) {
    toast('error', 'Check failed', e.message);
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Check Now`;
    }, 3000);
  }
}

/* ═══════════════════════════════════════════════════════════════════
   COUNTDOWN TIMER
   ═══════════════════════════════════════════════════════════════════ */

function tickCountdown() {
  const el = document.getElementById('countdown');
  const fill = document.getElementById('progress-fill');

  if (!state.nextRun) {
    el.textContent = '—';
    fill.style.width = '0%';
    return;
  }

  const now   = Date.now();
  const next  = state.nextRun.getTime();
  const total = state.intervalMinutes * 60 * 1000;
  const diff  = next - now;

  if (diff <= 0) {
    el.textContent = '00:00';
    fill.style.width = '100%';
    return;
  }

  const mm = Math.floor(diff / 60000);
  const ss = Math.floor((diff % 60000) / 1000);
  el.textContent = `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;

  const pct = Math.max(0, Math.min(100, ((total - diff) / total) * 100));
  fill.style.width = pct + '%';
}

/* ═══════════════════════════════════════════════════════════════════
   TOAST NOTIFICATIONS
   ═══════════════════════════════════════════════════════════════════ */

const TOAST_ICONS = { success: '✅', error: '❌', info: 'ℹ️', warn: '⚠️' };

function toast(type, title, msg, duration = 4000) {
  const container = document.getElementById('toast-container');
  const div = document.createElement('div');
  div.className = `toast toast--${type}`;
  div.innerHTML = `
    <div class="toast-icon">${TOAST_ICONS[type] || '•'}</div>
    <div class="toast-body">
      <div class="toast-title">${esc(title)}</div>
      ${msg ? `<div class="toast-msg">${esc(msg)}</div>` : ''}
    </div>
  `;
  container.appendChild(div);
  div.addEventListener('click', () => div.remove());
  setTimeout(() => {
    div.style.transition = 'opacity 0.3s, transform 0.3s';
    div.style.opacity = '0';
    div.style.transform = 'translateX(100%)';
    setTimeout(() => div.remove(), 300);
  }, duration);
}

/* ═══════════════════════════════════════════════════════════════════
   HELPERS
   ═══════════════════════════════════════════════════════════════════ */

function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmtTime(isoStr) {
  if (!isoStr) return '—';
  try {
    const d = new Date(isoStr);
    return d.toLocaleString('en-AU', {
      day: '2-digit', month: 'short',
      hour: '2-digit', minute: '2-digit', hour12: false,
    });
  } catch { return isoStr.substring(0, 16); }
}

function fmtRelative(isoStr) {
  if (!isoStr) return '—';
  try {
    const diff = Date.now() - new Date(isoStr).getTime();
    const m = Math.floor(diff / 60000);
    if (m < 1)  return 'just now';
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return Math.floor(h / 24) + 'd ago';
  } catch { return '—'; }
}

function fmtNum(n) {
  return Number(n).toLocaleString('en-AU');
}

function isNewToday(isoStr) {
  if (!isoStr) return false;
  const d = new Date(isoStr);
  const now = new Date();
  return d.toDateString() === now.toDateString();
}

function setBtnLoading(id, loading, label = '') {
  const btn = document.getElementById(id);
  if (!btn) return;
  btn.disabled = loading;
  if (loading) {
    btn.dataset.orig = btn.innerHTML;
    btn.innerHTML = `<span class="spinner"></span> ${esc(label)}`;
  } else if (btn.dataset.orig) {
    btn.innerHTML = btn.dataset.orig;
  }
}

function shake(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.animation = 'none';
  requestAnimationFrame(() => {
    el.style.animation = 'shake 0.3s ease';
  });
}

function getSavedEnquiryDefaults() {
  try { return JSON.parse(localStorage.getItem('enq_defaults') || '{}'); }
  catch { return {}; }
}

function saveEnquiryDefaults(data) {
  try { localStorage.setItem('enq_defaults', JSON.stringify(data)); }
  catch {}
}

/* Shake keyframe (injected once) */
(function() {
  const style = document.createElement('style');
  style.textContent = `@keyframes shake {
    0%,100%{transform:translateX(0)}
    20%,60%{transform:translateX(-6px)}
    40%,80%{transform:translateX(6px)}
  }`;
  document.head.appendChild(style);
})();

/* ═══════════════════════════════════════════════════════════════════
   BOOT
   ═══════════════════════════════════════════════════════════════════ */

async function boot() {
  await refresh();

  /* Countdown tick every second */
  setInterval(tickCountdown, 1000);

  /* Auto-refresh every 30 s */
  setInterval(refresh, POLL_INTERVAL);
}

document.addEventListener('DOMContentLoaded', boot);
