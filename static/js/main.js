/**
 * main.js — MSL Intel Platform
 * Global utilities: CSRF, security helpers, toast notifications
 * NO inline scripts — all JS is external for strict CSP compliance
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
  setTimeout(() => el.remove(), 4500);
};

// ── Shared helpers ────────────────────────────────────────────────
window.escHtml = function(s) {
  if (!s) return '';
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#x27;');
};

window.tagsHtml = function(str, cls = 'pub-tag') {
  if (!str) return '';
  return str.split(';').filter(Boolean)
    .map(t => `<span class="${cls}">${escHtml(t.trim())}</span>`).join('');
};
