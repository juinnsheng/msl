/**
 * review.js — Bulk Literature Review
 * CSP-compliant: no inline styles, no onclick in HTML.
 * Uses classList + CSS .hidden/.visible for all state changes.
 */

var _allRows = [];

document.addEventListener('DOMContentLoaded', function() {
  var btn = document.getElementById('rv-search-btn');
  if (btn) btn.addEventListener('click', rvSearch);
  var ext = document.getElementById('rv-extract-btn');
  if (ext) ext.addEventListener('click', rvExtract);
  var dl  = document.getElementById('rv-dl-btn');
  if (dl)  dl.addEventListener('click', rvDownload);
  var dlx = document.getElementById('rv-dlext-btn');
  if (dlx) dlx.addEventListener('click', rvDownloadExtracted);
  var yf  = document.getElementById('rv-year-filter');
  if (yf)  yf.addEventListener('change', applyYearFilter);
});

async function rvSearch() {
  var q = document.getElementById('rv-question').value.trim();
  if (!q) return showToast('Please enter a question.', 'error');

  rvBtnLoad(true);
  hideEl('rv-table-wrap');
  hideEl('rv-stats');
  document.getElementById('rv-warning').innerHTML = '';
  ['rv-extract-btn','rv-dl-btn','rv-dlext-btn'].forEach(function(id) {
    var el = document.getElementById(id); if (el) el.disabled = true;
  });

  var prog = startProgress('rv-progress');

  try {
    var maxEl    = document.getElementById('rv-max');
    var yearEl   = document.getElementById('rv-min-year');
    var enrichEl = document.getElementById('rv-enrich');

    var resp = await fetch('/api/review/search', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        question:         q,
        max_results:      maxEl    ? +maxEl.value    : 30,
        min_year:         yearEl   && yearEl.value   ? yearEl.value : null,
        enrich_citations: enrichEl ? enrichEl.checked : false,
      })
    });
    var data = await resp.json();
    prog.stop();

    if (!resp.ok) { rvMsg(data.error || 'Search failed.', 'error'); return; }
    if (data.warning) showWarning(data.warning, 'rv-warning');

    _allRows = data.rows || [];
    var el;
    el = document.getElementById('rv-stat-total');   if (el) el.textContent = (data.total_pubmed||0).toLocaleString();
    el = document.getElementById('rv-stat-fetched'); if (el) el.textContent = (data.fetched||0).toLocaleString();
    showEl('rv-stats');
    renderTable(_allRows);
    ['rv-extract-btn','rv-dl-btn'].forEach(function(id) {
      var e = document.getElementById(id); if (e) e.disabled = false;
    });

  } catch(e) {
    prog.stop();
    rvMsg('Request failed \u2014 check your connection. (' + e.message + ')', 'error');
  } finally {
    rvBtnLoad(false);
  }
}

async function rvExtract() {
  if (!confirm('LLM extraction on up to 30 records (free-tier cap). This may take 2\u20134 minutes. Continue?')) return;
  rvExtLoad(true);
  var dlx = document.getElementById('rv-dlext-btn'); if (dlx) dlx.disabled = true;
  document.getElementById('rv-warning').innerHTML = '';

  var prog = startProgress('rv-ext-progress');

  try {
    var resp = await fetch('/api/review/extract', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({limit: 30})
    });
    var data = await resp.json();
    prog.stop();

    if (!resp.ok) { rvMsg(data.error || 'Extraction failed.', 'error'); return; }
    if (data.warning) showWarning(data.warning, 'rv-warning');
    rvMsg('\u2713 Extracted ' + data.count + ' records. Click \u201cDownload Extracted Excel\u201d to export.', 'success');
    if (dlx) dlx.disabled = false;

  } catch(e) {
    prog.stop();
    rvMsg('Extraction failed. Check NVIDIA API key. (' + e.message + ')', 'error');
  } finally {
    rvExtLoad(false);
  }
}

async function _dlFile(url, filename) {
  try {
    var resp = await fetch(url);
    if (!resp.ok) {
      var err = await resp.json().catch(function(){ return {error:'Download failed'}; });
      rvMsg(err.error || 'Download failed', 'error');
      return false;
    }
    var blob = await resp.blob();
    var a    = document.createElement('a');
    a.href     = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
    return true;
  } catch(e) { rvMsg('Download error: ' + e.message, 'error'); return false; }
}

async function rvDownload() {
  var btn = document.getElementById('rv-dl-btn');
  btn.textContent = '\u23f3 Generating\u2026'; btn.disabled = true;
  await _dlFile('/api/review/download', 'msl_literature_' + new Date().toISOString().slice(0,10) + '.xlsx');
  btn.textContent = '\u2b07 Download Raw Excel'; btn.disabled = false;
}

async function rvDownloadExtracted() {
  var btn = document.getElementById('rv-dlext-btn');
  btn.textContent = '\u23f3 Generating\u2026'; btn.disabled = true;
  await _dlFile('/api/review/download_extracted', 'msl_extracted_' + new Date().toISOString().slice(0,10) + '.xlsx');
  btn.textContent = '\u2b07 Download Extracted Excel'; btn.disabled = false;
}

function renderTable(rows) {
  var tbody = document.getElementById('rv-tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  rows.forEach(function(r, i) {
    var tr  = document.createElement('tr');
    var doi = r.doi
      ? '<a href="https://doi.org/' + escHtml(r.doi) + '" target="_blank" rel="noopener noreferrer" class="link-chip">DOI</a>'
      : '';
    tr.innerHTML =
      '<td class="td-num">' + (i+1) + '</td>' +
      '<td class="td-title">' +
        '<a href="' + escHtml(r.pmid_url) + '" target="_blank" rel="noopener noreferrer">' + escHtml(r.title) + '</a>' +
        (r.abstract_short ? '<p class="abs-snippet">' + escHtml(r.abstract_short) + '</p>' : '') +
      '</td>' +
      '<td>' + escHtml(r.authors) + '</td>' +
      '<td class="td-cen">' + escHtml(String(r.year)) + '</td>' +
      '<td class="td-journal">' + escHtml(r.journal) + '</td>' +
      '<td class="td-cen"><span class="cit-badge">' + (r.citations||0).toLocaleString() + '</span></td>' +
      '<td>' + tagsHtml(r.pub_types, 'pub-tag-sm') + '</td>' +
      '<td>' +
        '<a href="' + escHtml(r.pmid_url) + '" target="_blank" rel="noopener noreferrer" class="link-chip">PM</a>' +
        doi +
      '</td>';
    tbody.appendChild(tr);
  });
  var el = document.getElementById('rv-stat-showing');
  if (el) el.textContent = rows.length.toLocaleString();
  showEl('rv-table-wrap');
}

function applyYearFilter() {
  var el = document.getElementById('rv-year-filter');
  var y  = el ? +el.value : 0;
  if (!y) { renderTable(_allRows); return; }
  renderTable(_allRows.filter(function(r){ return +(r.year||0) >= y; }));
}

var _sortDir = {};
function sortTable(col) {
  _sortDir[col] = !_sortDir[col];
  var asc = _sortDir[col];
  _allRows.sort(function(a,b) {
    var va = col === 'citations' ? +(a[col]||0) : String(a[col]||'').toLowerCase();
    var vb = col === 'citations' ? +(b[col]||0) : String(b[col]||'').toLowerCase();
    return va < vb ? (asc?-1:1) : va > vb ? (asc?1:-1) : 0;
  });
  renderTable(_allRows);
}

function rvBtnLoad(on) {
  var btn  = document.getElementById('rv-search-btn');
  var txt  = document.getElementById('rv-btn-text');
  var spin = document.getElementById('rv-spinner');
  if (btn)  btn.disabled       = on;
  if (txt)  txt.textContent    = on ? 'Searching\u2026' : 'Search PubMed';
  if (spin) spin.classList.toggle('hidden', !on);
}
function rvExtLoad(on) {
  var btn  = document.getElementById('rv-extract-btn');
  var txt  = document.getElementById('rv-ext-text');
  var spin = document.getElementById('rv-ext-spinner');
  if (btn)  btn.disabled    = on;
  if (txt)  txt.textContent = on ? 'Extracting\u2026' : 'LLM Extract (up to 30)';
  if (spin) spin.classList.toggle('hidden', !on);
}
function rvMsg(text, type) {
  var el = document.getElementById('rv-msg');
  if (!el) return;
  el.innerHTML = '<div class="' + (type === 'error' ? 'error-msg' : 'success-msg') + '" role="alert">' + escHtml(text) + '</div>';
  setTimeout(function(){ el.innerHTML = ''; }, 8000);
}

// ── Sortable column headers (replaces onclick="sortTable()" in HTML) ─
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('th.sortable').forEach(function(th) {
    th.addEventListener('click', function() {
      sortTable(th.getAttribute('data-col'));
    });
  });
});
