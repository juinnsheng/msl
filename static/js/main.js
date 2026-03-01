/**
 * main.js — MSL Intel Platform
 * 100% CSP-compliant: zero inline style attributes or style.* assignments.
 * All visual state driven by classList (CSS classes only).
 */

// ── CSRF Token ────────────────────────────────────────────────────
function getCsrf() {
  return document.querySelector('meta[name="csrf-token"]') &&
         document.querySelector('meta[name="csrf-token"]').content ||
         document.cookie.split(';')
           .map(function(c){ return c.trim(); })
           .filter(function(c){ return c.indexOf('csrf_token=') === 0; })
           .map(function(c){ return c.split('=').slice(1).join('='); })[0] || '';
}

// Auto-attach CSRF on same-origin non-GET fetches
var _origFetch = window.fetch;
window.fetch = function(url, opts) {
  opts = opts || {};
  try {
    var t = new URL(url, window.location.origin);
    if (t.origin === window.location.origin) {
      opts.headers = opts.headers || {};
      if (opts.method && opts.method.toUpperCase() !== 'GET') {
        opts.headers['X-CSRFToken'] = getCsrf();
      }
    }
  } catch(e) {}
  return _origFetch.call(this, url, opts);
};

if (window.history && window.history.replaceState) {
  window.history.replaceState(null, null, window.location.href);
}

// ── Toast (position via CSS class .toast, not inline style) ───────
window.showToast = function(msg, type) {
  var el = document.createElement('div');
  el.className = 'toast toast--' + (type === 'success' ? 'success' : 'error');
  el.setAttribute('role', 'alert');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(function(){ if (el.parentNode) el.parentNode.removeChild(el); }, 5000);
};

// ── HTML escape ───────────────────────────────────────────────────
window.escHtml = function(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#x27;');
};

// ── Tag badges ────────────────────────────────────────────────────
window.tagsHtml = function(str, cls) {
  cls = cls || 'pub-tag';
  if (!str) return '';
  return str.split(';').filter(Boolean).map(function(t) {
    return '<span class="' + cls + '">' + escHtml(t.trim()) + '</span>';
  }).join('');
};

// ── classList show/hide helpers ───────────────────────────────────
window.showEl = function(id) {
  var el = document.getElementById(id);
  if (el) el.classList.remove('hidden');
};
window.hideEl = function(id) {
  var el = document.getElementById(id);
  if (el) el.classList.add('hidden');
};

// ── Progress bar ──────────────────────────────────────────────────
// Width is driven by CSS attribute selectors → NO style.width calls.
// CSS rule: .progress-bar-fill::after { width: attr(data-pct %) } — see main.css
window.startProgress = function(containerId) {
  var wrap = document.getElementById(containerId);
  if (!wrap) return { stop: function(){}, update: function(){} };

  var stepId = containerId + '-step';
  var pctId  = containerId + '-pct';
  var fillId = containerId + '-fill';

  wrap.innerHTML =
    '<div class="progress-wrap">' +
    '<div class="progress-label">' +
    '<span id="' + stepId + '">Starting\u2026</span>' +
    '<span id="' + pctId  + '">0%</span>' +
    '</div>' +
    '<div class="progress-bar-track">' +
    '<div class="progress-bar-fill" id="' + fillId + '" data-pct="0"></div>' +
    '</div></div>';
  wrap.classList.remove('hidden');

  var _timer   = null;
  var _stopped = false;

  function update(pct, label) {
    if (_stopped) return;
    var p    = Math.round(Math.min(100, Math.max(0, pct || 0)));
    var fill = document.getElementById(fillId);
    var step = document.getElementById(stepId);
    var pEl  = document.getElementById(pctId);
    if (fill) fill.setAttribute('data-pct', String(p));
    if (step) step.textContent = label || 'Processing\u2026';
    if (pEl)  pEl.textContent  = p + '%';
  }

  _timer = setInterval(function() {
    _origFetch('/api/status').then(function(r) {
      return r.ok ? r.json() : null;
    }).then(function(d) {
      if (d && d.step && d.step !== 'idle') update(d.pct || 0, d.detail || d.step);
    }).catch(function(){});
  }, 1200);

  function stop() {
    _stopped = true;
    clearInterval(_timer);
    wrap.innerHTML = '';
    wrap.classList.add('hidden');
  }

  return { stop: stop, update: update };
};

// ── Warning banner ────────────────────────────────────────────────
window.showWarning = function(msg, containerId) {
  if (!msg || !containerId) return;
  var el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = '<div class="warn-banner" role="alert">\u26a0 ' + escHtml(msg) + '</div>';
};
