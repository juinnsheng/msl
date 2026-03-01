/**
 * clinicaltrials.js — Clinical Trials dashboard
 * CSP-compliant: no inline styles, no onclick attributes in HTML.
 */

var _ctData      = [];
var _chatHistory = [];

var STATUS_CLASS = {
  'RECRUITING':             'status--recruiting',
  'COMPLETED':              'status--completed',
  'ACTIVE_NOT_RECRUITING':  'status--active',
  'TERMINATED':             'status--terminated',
  'NOT_YET_RECRUITING':     'status--notyet',
};

document.addEventListener('DOMContentLoaded', function() {
  var btn  = document.getElementById('ct-search-btn');
  if (btn)  btn.addEventListener('click', ctSearch);
  var dl   = document.getElementById('ct-dl-btn');
  if (dl)   dl.addEventListener('click', ctDownload);
  var fab  = document.getElementById('chat-fab-btn');
  if (fab)  fab.addEventListener('click', toggleChat);
  var cls  = document.getElementById('ct-chat-close');
  if (cls)  cls.addEventListener('click', toggleChat);
  var send = document.getElementById('ct-chat-send-btn');
  if (send) send.addEventListener('click', ctChatSend);
  var inp  = document.getElementById('ct-chat-input');
  if (inp)  inp.addEventListener('keydown', function(e){ if (e.key === 'Enter') ctChatSend(); });
});

async function ctSearch() {
  var q = document.getElementById('ct-question').value.trim();
  if (!q) return showToast('Please enter a question.', 'error');

  ctBtnLoad(true);
  ['ct-context','ct-phase-summary'].forEach(hideEl);
  hideEl('ct-table-wrap');
  var dlBtn = document.getElementById('ct-dl-btn');
  if (dlBtn) dlBtn.disabled = true;
  document.getElementById('ct-warning').innerHTML = '';

  var prog = startProgress('ct-progress');

  try {
    var maxEl    = document.getElementById('ct-max');
    var statusEl = document.getElementById('ct-status');

    var resp = await fetch('/api/ct/search', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        question:      q,
        max_results:   maxEl    ? +maxEl.value        : 30,
        status_filter: statusEl ? statusEl.value       : '',
      })
    });
    var data = await resp.json();
    prog.stop();

    if (!resp.ok) { ctMsg(data.error || 'Search failed.', 'error'); return; }
    if (data.warning) showWarning(data.warning, 'ct-warning');

    _ctData = data.studies || [];
    var el;
    el = document.getElementById('ct-query-text'); if (el) el.textContent = data.ct_query || '';
    el = document.getElementById('ct-ctx-text');   if (el) el.textContent = data.clinical_context || '\u2014';
    el = document.getElementById('ct-total-text'); if (el) el.textContent = data.total + ' trials';
    showEl('ct-context');

    renderPhaseSummary(_ctData);
    renderCtTable(_ctData);
    if (dlBtn) dlBtn.disabled = false;
    showEl('chat-fab-btn');

  } catch(e) {
    prog.stop();
    ctMsg('Request failed \u2014 check your connection. (' + e.message + ')', 'error');
  } finally {
    ctBtnLoad(false);
  }
}

function renderPhaseSummary(studies) {
  var counts = {};
  studies.forEach(function(s) { var p = s.phase || 'N/A'; counts[p] = (counts[p]||0) + 1; });
  var el = document.getElementById('ct-phase-summary');
  if (!el) return;
  el.innerHTML = Object.entries(counts).sort(function(a,b){ return b[1]-a[1]; })
    .map(function(kv){ return '<span class="phase-pill">' + escHtml(kv[0]) + ': ' + kv[1] + '</span>'; })
    .join('');
  showEl('ct-phase-summary');
}

function renderCtTable(studies) {
  var tbody = document.getElementById('ct-tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  studies.forEach(function(s, i) {
    var statusKey = (s.status || '').replace(/ /g,'_').toUpperCase();
    var sc  = STATUS_CLASS[statusKey] || '';
    var tr  = document.createElement('tr');
    var sum = s.summary ? '<p class="abs-snippet">' + escHtml(s.summary.slice(0,200)) + '\u2026</p>' : '';
    tr.innerHTML =
      '<td class="td-num">' + (i+1) + '</td>' +
      '<td><a href="' + escHtml(s.url) + '" target="_blank" rel="noopener noreferrer" class="nct-link">' + escHtml(s.nct_id) + '</a></td>' +
      '<td class="td-title"><a href="' + escHtml(s.url) + '" target="_blank" rel="noopener noreferrer">' + escHtml(s.title) + '</a>' + sum + '</td>' +
      '<td><span class="status-badge ' + sc + '">' + escHtml(s.status || '\u2014') + '</span></td>' +
      '<td><span class="phase-badge">' + escHtml(s.phase || '\u2014') + '</span></td>' +
      '<td>' + escHtml(s.study_type || '\u2014') + '</td>' +
      '<td class="td-cen">' + (s.enrollment ? Number(s.enrollment).toLocaleString() : '\u2014') + '</td>' +
      '<td>' + escHtml(s.start_date || '\u2014') + '</td>' +
      '<td>' + escHtml(s.completion_date || '\u2014') + '</td>' +
      '<td>' + escHtml(s.sponsor || '\u2014') + '</td>' +
      '<td>' + escHtml((s.interventions || '').slice(0,80)) + '</td>' +
      '<td>' + escHtml((s.primary_outcome || '').slice(0,100)) + '</td>' +
      '<td>' + escHtml((s.countries || '').slice(0,60)) + '</td>';
    tbody.appendChild(tr);
  });
  showEl('ct-table-wrap');
}

async function ctDownload() {
  var btn = document.getElementById('ct-dl-btn');
  btn.textContent = '\u23f3 Generating Excel\u2026'; btn.disabled = true;
  try {
    var resp = await fetch('/api/ct/download');
    if (!resp.ok) {
      var err = await resp.json().catch(function(){ return {error:'Download failed'}; });
      ctMsg(err.error || 'Download failed', 'error'); return;
    }
    var blob = await resp.blob();
    var a    = document.createElement('a');
    a.href     = URL.createObjectURL(blob);
    a.download = 'clinical_trials_' + new Date().toISOString().slice(0,10) + '.xlsx';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  } catch(e) { ctMsg('Download error: ' + e.message, 'error'); }
  finally { btn.textContent = '\u2b07 Download Excel'; btn.disabled = false; }
}

// ── Chat ──────────────────────────────────────────────────────────
function toggleChat() {
  var panel = document.getElementById('ct-chat-panel');
  if (panel) panel.classList.toggle('hidden');
}

async function ctChatSend() {
  var inp = document.getElementById('ct-chat-input');
  var q   = inp ? inp.value.trim() : '';
  if (!q) return;
  if (inp) inp.value = '';
  appendChat('user', q);
  appendChat('assistant', '\u2026thinking\u2026', true);

  try {
    var resp = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({question: q, history: _chatHistory})
    });
    var data = await resp.json();
    removeTyping();
    var ans = data.answer || data.error || 'No response.';
    appendChat('assistant', ans);
    _chatHistory.push({role:'user',content:q},{role:'assistant',content:ans});
    if (_chatHistory.length > 16) _chatHistory = _chatHistory.slice(-16);
  } catch(e) {
    removeTyping();
    appendChat('assistant', 'Error: request failed. Check NVIDIA API key.');
  }
}

function appendChat(role, text, typing) {
  var el = document.createElement('div');
  el.className = 'chat-msg chat-msg--' + role + (typing ? ' chat-typing' : '');
  el.textContent = text;
  var msgs = document.getElementById('ct-chat-messages');
  if (msgs) { msgs.appendChild(el); msgs.scrollTop = msgs.scrollHeight; }
}
function removeTyping() {
  document.querySelectorAll('.chat-typing').forEach(function(e){ e.parentNode && e.parentNode.removeChild(e); });
}

function ctBtnLoad(on) {
  var btn  = document.getElementById('ct-search-btn');
  var txt  = document.getElementById('ct-btn-text');
  var spin = document.getElementById('ct-spinner');
  if (btn)  btn.disabled    = on;
  if (txt)  txt.textContent = on ? 'Searching\u2026' : 'Search Trials';
  if (spin) spin.classList.toggle('hidden', !on);
}
function ctMsg(text, type) {
  var el = document.getElementById('ct-msg');
  if (!el) return;
  el.innerHTML = '<div class="' + (type === 'error' ? 'error-msg' : 'success-msg') + '" role="alert">' + escHtml(text) + '</div>';
  setTimeout(function(){ el.innerHTML = ''; }, 8000);
}
