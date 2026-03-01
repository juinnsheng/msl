/**
 * main.js — MSL Intel Platform
 * Global utilities: CSRF, progress polling, toast notifications
 */

// ── CSRF Token ────────────────────────────────────────────────────
function getCsrf() {
  return document.querySelector('meta[name="csrf-token"]')?.content ||
         document.cookie.split(';')
           .map(c => c.trim())
           .find(c => c.startsWith('csrf_token='))
           ?.split('=').slice(1).join('=') || '';
}

// Auto-attach CSRF header on all same-origin non-GET fetches
const _origFetch = window.fetch;
window.fetch = function(url, opts = {}) {
  try {
    const t = new URL(url, window.location.origin);
    if (t.origin === window.location.origin) {
      opts.headers = opts.headers || {};
      if (opts.method && opts.method.toUpperCase() !== 'GET') {
        opts.headers['X-CSRFToken'] = getCsrf();
      }
    }
  } catch(_) {}
  return _origFetch.call(this, url, opts);
};

// ── Prevent form resubmission on back ────────────────────────────
if (window.history?.replaceState) {
  window.history.replaceState(null, null, window.location.href);
}

// ── Toast notifications ───────────────────────────────────────────
window.showToast = function(msg, type = 'error') {
  const el = document.createElement('div');
  el.className = type === 'error' ? 'error-msg' : 'success-msg';
  el.style.cssText = 'position:fixed;bottom:1.5rem;left:50%;transform:translateX(-50%);z-index:9999;min-width:280px;max-width:480px;animation:fadeIn .2s ease';
  el.setAttribute('role', 'alert');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 5000);
};

// ── Security helpers ──────────────────────────────────────────────
window.escHtml = function(s) {
  if (!s) return '';
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#x27;');
};

window.tagsHtml = function(str, cls = 'pub-tag') {
  if (!str) return '';
  return str.split(';').filter(Boolean)
    .map(t => `<span class="${cls}">${escHtml(t.trim())}</span>`).join('');
};

// ── Progress bar helpers ──────────────────────────────────────────
/**
 * showProgress(containerId) — shows the progress bar inside #containerId
 * Returns a handle with .stop() and .update(pct, label)
 */
window.startProgress = function(containerId) {
  const wrap = document.getElementById(containerId);
  if (!wrap) return { stop: ()=>{}, update: ()=>{} };

  wrap.innerHTML = `
    <div class="progress-wrap">
      <div class="progress-label">
        <span id="${containerId}-step">Starting…</span>
        <span id="${containerId}-pct">0%</span>
      </div>
      <div class="progress-bar-track">
        <div class="progress-bar-fill" id="${containerId}-fill"></div>
      </div>
    </div>`;
  wrap.style.display = 'block';

  let _interval = null;
  let _stopped  = false;

  function update(pct, label) {
    if (_stopped) return;
    const fill  = document.getElementById(`${containerId}-fill`);
    const step  = document.getElementById(`${containerId}-step`);
    const pctEl = document.getElementById(`${containerId}-pct`);
    if (fill)  fill.style.width  = pct + '%';
    if (step)  step.textContent  = label || 'Processing…';
    if (pctEl) pctEl.textContent = pct + '%';
  }

  // Poll /api/status every 1.2 seconds
  _interval = setInterval(async () => {
    try {
      const r = await _origFetch('/api/status');
      if (!r.ok) return;
      const d = await r.json();
      if (d.step && d.step !== 'idle') {
        update(d.pct || 0, d.detail || d.step);
      }
    } catch(_) {}
  }, 1200);

  function stop(msg) {
    _stopped = true;
    clearInterval(_interval);
    if (msg) {
      wrap.innerHTML = '';
    } else {
      update(100, 'Complete');
      setTimeout(() => { wrap.innerHTML = ''; }, 600);
    }
  }

  return { stop, update };
};

// ── Warning banner helper ────────────────────────────────────────
window.showWarning = function(msg, containerId) {
  if (!msg || !containerId) return;
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = `<div class="warn-banner">⚠ ${escHtml(msg)}</div>`;
};
