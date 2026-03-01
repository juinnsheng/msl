/**
 * clinicaltrials.js — Clinical Trials dashboard
 * External file for CSP compliance
 */

let _ctData = [];
let _chatHistory = [];

const STATUS_CLASS = {
  'RECRUITING':'status--recruiting',
  'COMPLETED':'status--completed',
  'ACTIVE_NOT_RECRUITING':'status--active',
  'TERMINATED':'status--terminated',
  'NOT_YET_RECRUITING':'status--notyet',
};

async function ctSearch() {
  const q = document.getElementById('ct-question').value.trim();
  if (!q) return showToast('Please enter a question.', 'error');
  ctBtnLoad(true);
  ['ct-context','ct-phase-summary'].forEach(id => document.getElementById(id).classList.add('hidden'));
  document.getElementById('ct-table-wrap').style.display = 'none';
  document.getElementById('ct-dl-btn').disabled = true;

  try {
    const resp = await fetch('/api/ct/search', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        question:      q,
        max_results:   +document.getElementById('ct-max').value,
        status_filter: document.getElementById('ct-status').value,
      })
    });
    const data = await resp.json();
    if (!resp.ok) { ctMsg(data.error,'error'); return; }

    _ctData = data.studies;
    document.getElementById('ct-query-text').textContent = data.ct_query || '';
    document.getElementById('ct-ctx-text').textContent   = data.clinical_context || '—';
    document.getElementById('ct-total-text').textContent = data.total + ' trials';
    document.getElementById('ct-context').classList.remove('hidden');

    renderPhaseSummary(_ctData);
    renderCtTable(_ctData);
    document.getElementById('ct-dl-btn').disabled = false;
    document.getElementById('chat-fab-btn').style.display = 'flex';
  } catch(e) { ctMsg('Request failed. Please try again.','error'); }
  finally { ctBtnLoad(false); }
}

function renderPhaseSummary(studies) {
  const counts = {};
  studies.forEach(s => { const p = s.phase||'N/A'; counts[p]=(counts[p]||0)+1; });
  const el = document.getElementById('ct-phase-summary');
  el.innerHTML = Object.entries(counts).sort((a,b)=>b[1]-a[1])
    .map(([p,c]) => `<span class="phase-pill">${escHtml(p)}: ${c}</span>`).join('');
  el.classList.remove('hidden');
}

function renderCtTable(studies) {
  const tbody = document.getElementById('ct-tbody');
  tbody.innerHTML = '';
  studies.forEach((s, i) => {
    const sc  = STATUS_CLASS[s.status?.replace(/ /g,'_').toUpperCase()] || '';
    const tr  = document.createElement('tr');
    tr.innerHTML = `
      <td class="td-num">${i+1}</td>
      <td><a href="${escHtml(s.url)}" target="_blank" rel="noopener noreferrer" class="nct-link">${escHtml(s.nct_id)}</a></td>
      <td class="td-title">
        <a href="${escHtml(s.url)}" target="_blank" rel="noopener noreferrer">${escHtml(s.title)}</a>
        ${s.summary ? `<p class="abs-snippet">${escHtml(s.summary.slice(0,200))}…</p>` : ''}
      </td>
      <td><span class="status-badge ${sc}">${escHtml(s.status||'—')}</span></td>
      <td><span class="phase-badge">${escHtml(s.phase||'—')}</span></td>
      <td>${escHtml(s.study_type||'—')}</td>
      <td class="td-cen">${s.enrollment ? Number(s.enrollment).toLocaleString() : '—'}</td>
      <td>${escHtml(s.start_date||'—')}</td>
      <td>${escHtml(s.completion_date||'—')}</td>
      <td>${escHtml(s.sponsor||'—')}</td>
      <td>${escHtml((s.interventions||'').slice(0,80))}</td>
      <td>${escHtml((s.primary_outcome||'').slice(0,100))}</td>
      <td>${escHtml((s.countries||'').slice(0,60))}</td>`;
    tbody.appendChild(tr);
  });
  document.getElementById('ct-table-wrap').style.display = 'block';
}

async function ctDownload() {
  const btn = document.getElementById('ct-dl-btn');
  btn.textContent = '⏳ Generating Excel…'; btn.disabled = true;
  try {
    const resp = await fetch('/api/ct/download');
    if (!resp.ok) {
      const err = await resp.json().catch(()=>({error:'Download failed'}));
      ctMsg(err.error||'Download failed','error'); return;
    }
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'clinical_trials_'+new Date().toISOString().slice(0,10)+'.xlsx';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  } catch(e) { ctMsg('Download error: '+e.message,'error'); }
  finally { btn.textContent='⬇ Download Excel'; btn.disabled=false; }
}

// ── Chat ──────────────────────────────────────────────────────────
function toggleChat() {
  document.getElementById('ct-chat-panel').classList.toggle('hidden');
}

async function ctChatSend() {
  const inp = document.getElementById('ct-chat-input');
  const q   = inp.value.trim();
  if (!q) return;
  inp.value = '';
  appendChat('user', q);
  appendChat('assistant', '…thinking…', true);

  try {
    const resp = await fetch('/api/chat', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({question:q, history:_chatHistory})
    });
    const data = await resp.json();
    removeTyping();
    const ans = data.answer || data.error || 'No response.';
    appendChat('assistant', ans);
    _chatHistory.push({role:'user',content:q},{role:'assistant',content:ans});
    if (_chatHistory.length > 16) _chatHistory = _chatHistory.slice(-16);
  } catch(e) { removeTyping(); appendChat('assistant','Error: request failed'); }
}

function appendChat(role, text, typing=false) {
  const el = document.createElement('div');
  el.className = `chat-msg chat-msg--${role}${typing?' chat-typing':''}`;
  el.textContent = text;
  const msgs = document.getElementById('ct-chat-messages');
  msgs.appendChild(el);
  msgs.scrollTop = msgs.scrollHeight;
}
function removeTyping() {
  document.querySelectorAll('.chat-typing').forEach(e => e.remove());
}

function ctBtnLoad(on) {
  document.getElementById('ct-search-btn').disabled = on;
  document.getElementById('ct-btn-text').textContent = on ? 'Searching…' : 'Search Trials';
  document.getElementById('ct-spinner').classList.toggle('hidden', !on);
}
function ctMsg(text, type) {
  const el = document.getElementById('ct-msg');
  el.innerHTML = `<div class="${type==='error'?'error-msg':'success-msg'}" role="alert">${escHtml(text)}</div>`;
  setTimeout(() => el.innerHTML='', 6000);
}

document.addEventListener('DOMContentLoaded', function() {
  document.getElementById('ct-chat-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') ctChatSend();
  });
});
