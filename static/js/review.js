/**
 * review.js — Bulk Literature Review dashboard
 * With progress polling, free-tier cap warnings, and better error handling
 */

let _allRows = [];

async function rvSearch() {
  const q = document.getElementById('rv-question').value.trim();
  if (!q) return showToast('Please enter a question.', 'error');

  rvBtnLoad(true, 'Fetching records…');
  document.getElementById('rv-table-wrap').style.display = 'none';
  document.getElementById('rv-stats').classList.add('hidden');
  document.getElementById('rv-warning').innerHTML = '';
  ['rv-extract-btn','rv-dl-btn','rv-dlext-btn'].forEach(id => {
    document.getElementById(id).disabled = true;
  });

  const prog = startProgress('rv-progress');

  try {
    const resp = await fetch('/api/review/search', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        question:         q,
        max_results:      +document.getElementById('rv-max').value,
        min_year:         document.getElementById('rv-min-year').value || null,
        enrich_citations: document.getElementById('rv-enrich').checked,
      })
    });
    const data = await resp.json();
    prog.stop();

    if (!resp.ok) { rvMsg(data.error || 'Search failed.','error'); return; }

    if (data.warning) showWarning(data.warning, 'rv-warning');

    _allRows = data.rows;
    document.getElementById('rv-stat-total').textContent   = (data.total_pubmed||0).toLocaleString();
    document.getElementById('rv-stat-fetched').textContent = (data.fetched||0).toLocaleString();
    document.getElementById('rv-stats').classList.remove('hidden');
    renderTable(_allRows);
    document.getElementById('rv-extract-btn').disabled = false;
    document.getElementById('rv-dl-btn').disabled = false;

  } catch(e) {
    prog.stop('error');
    rvMsg('Request failed — check your connection and try again.', 'error');
  } finally {
    rvBtnLoad(false, 'Search PubMed');
  }
}

async function rvExtract() {
  if (!confirm(`LLM extraction on up to 30 records (free-tier cap). This may take 2–4 minutes. Continue?`)) return;
  rvExtLoad(true);
  document.getElementById('rv-dlext-btn').disabled = true;
  document.getElementById('rv-warning').innerHTML = '';

  const prog = startProgress('rv-ext-progress');

  try {
    const resp = await fetch('/api/review/extract', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({limit: 30})
    });
    const data = await resp.json();
    prog.stop();

    if (!resp.ok) { rvMsg(data.error || 'Extraction failed.','error'); return; }
    if (data.warning) showWarning(data.warning, 'rv-warning');

    rvMsg(`✓ Extracted ${data.count} records. Click "Download Extracted Excel" to export.`, 'success');
    document.getElementById('rv-dlext-btn').disabled = false;

  } catch(e) {
    prog.stop('error');
    rvMsg('Extraction failed — check your NVIDIA API key and try again.','error');
  } finally {
    rvExtLoad(false);
  }
}

async function _dlFile(url, filename) {
  try {
    const resp = await fetch(url);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({error:'Download failed'}));
      rvMsg(err.error || 'Download failed — try searching again','error');
      return false;
    }
    const blob = await resp.blob();
    const a    = document.createElement('a');
    a.href     = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
    return true;
  } catch(e) { rvMsg('Download error: ' + e.message, 'error'); return false; }
}

async function rvDownload() {
  const btn = document.getElementById('rv-dl-btn');
  btn.textContent = '⏳ Generating…'; btn.disabled = true;
  await _dlFile('/api/review/download','msl_literature_'+new Date().toISOString().slice(0,10)+'.xlsx');
  btn.textContent = '⬇ Download Raw Excel'; btn.disabled = false;
}

async function rvDownloadExtracted() {
  const btn = document.getElementById('rv-dlext-btn');
  btn.textContent = '⏳ Generating…'; btn.disabled = true;
  await _dlFile('/api/review/download_extracted','msl_extracted_'+new Date().toISOString().slice(0,10)+'.xlsx');
  btn.textContent = '⬇ Download Extracted Excel'; btn.disabled = false;
}

function renderTable(rows) {
  const tbody = document.getElementById('rv-tbody');
  tbody.innerHTML = '';
  rows.forEach((r, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="td-num">${i+1}</td>
      <td class="td-title">
        <a href="${escHtml(r.pmid_url)}" target="_blank" rel="noopener noreferrer">${escHtml(r.title)}</a>
        ${r.abstract_short ? `<p class="abs-snippet">${escHtml(r.abstract_short)}</p>` : ''}
      </td>
      <td>${escHtml(r.authors)}</td>
      <td class="td-cen">${escHtml(String(r.year))}</td>
      <td class="td-journal">${escHtml(r.journal)}</td>
      <td class="td-cen"><span class="cit-badge">${(r.citations||0).toLocaleString()}</span></td>
      <td>${tagsHtml(r.pub_types,'pub-tag-sm')}</td>
      <td>
        <a href="${escHtml(r.pmid_url)}" target="_blank" rel="noopener noreferrer" class="link-chip">PM</a>
        ${r.doi ? `<a href="https://doi.org/${escHtml(r.doi)}" target="_blank" rel="noopener noreferrer" class="link-chip">DOI</a>` : ''}
      </td>`;
    tbody.appendChild(tr);
  });
  document.getElementById('rv-stat-showing').textContent = rows.length.toLocaleString();
  document.getElementById('rv-table-wrap').style.display = 'block';
}

function applyYearFilter() {
  const y = +document.getElementById('rv-year-filter').value;
  if (!y) { renderTable(_allRows); return; }
  renderTable(_allRows.filter(r => +(r.year||0) >= y));
}

let _sortDir = {};
function sortTable(col) {
  _sortDir[col] = !_sortDir[col];
  const asc = _sortDir[col];
  _allRows.sort((a,b) => {
    const va = col==='citations' ? +(a[col]||0) : String(a[col]||'').toLowerCase();
    const vb = col==='citations' ? +(b[col]||0) : String(b[col]||'').toLowerCase();
    return va < vb ? (asc?-1:1) : va > vb ? (asc?1:-1) : 0;
  });
  renderTable(_allRows);
}

function rvBtnLoad(on, offText) {
  document.getElementById('rv-search-btn').disabled = on;
  document.getElementById('rv-btn-text').textContent = on ? 'Searching…' : offText;
  document.getElementById('rv-spinner').classList.toggle('hidden', !on);
}
function rvExtLoad(on) {
  document.getElementById('rv-extract-btn').disabled = on;
  document.getElementById('rv-ext-text').textContent = on ? 'Extracting…' : 'LLM Extract (up to 30)';
  document.getElementById('rv-ext-spinner').classList.toggle('hidden', !on);
}
function rvMsg(text, type) {
  const el = document.getElementById('rv-msg');
  el.innerHTML = `<div class="${type==='error'?'error-msg':'success-msg'}" role="alert">${escHtml(text)}</div>`;
  setTimeout(() => el.innerHTML = '', 8000);
}
