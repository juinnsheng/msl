/**
 * evidence.js — Top Evidence dashboard
 * CSP-compliant: no inline styles, no onclick attributes.
 * Uses classList for all show/hide state.
 */

document.addEventListener('DOMContentLoaded', function() {
  var btn = document.getElementById('ev-search-btn');
  if (btn) btn.addEventListener('click', evSearch);

  var ta = document.getElementById('ev-question');
  if (ta) ta.addEventListener('keydown', function(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') evSearch();
  });
});

async function evSearch() {
  var q = document.getElementById('ev-question').value.trim();
  if (!q) return showToast('Please enter a clinical question.', 'error');

  var btn  = document.getElementById('ev-search-btn');
  var btnT = document.getElementById('ev-btn-text');
  var spin = document.getElementById('ev-spinner');
  btn.disabled = true;
  btnT.textContent = 'Searching\u2026';
  spin.classList.remove('hidden');

  document.getElementById('ev-results').innerHTML = '';
  document.getElementById('ev-warning').innerHTML = '';
  hideEl('ev-context');

  var prog = startProgress('ev-progress');

  try {
    var enrich  = document.getElementById('ev-enrich');
    var llmRank = document.getElementById('ev-llm-rank');
    var topN    = document.getElementById('ev-top-n');
    var minYear = document.getElementById('ev-min-year');

    var resp = await fetch('/api/evidence/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question:         q,
        top_n:            topN    ? +topN.value    : 15,
        min_year:         minYear && minYear.value ? minYear.value : null,
        enrich_citations: enrich  ? enrich.checked  : false,
        use_llm_rank:     llmRank ? llmRank.checked : false,
      })
    });

    var data = await resp.json();
    prog.stop();

    if (!resp.ok) {
      evShowError(data.error || 'Search failed. Please try again.');
      return;
    }

    if (data.warning) showWarning(data.warning, 'ev-warning');
    if (!data.llm_used) {
      var wb = document.getElementById('ev-warning');
      if (wb) wb.innerHTML += '<div class="info-banner">&#8505; AI ranking disabled (no NVIDIA key) \u2014 results sorted by recency.</div>';
    }

    var el;
    el = document.getElementById('ctx-area');  if (el) el.textContent = data.therapeutic_area || '\u2014';
    el = document.getElementById('ctx-ctx');   if (el) el.textContent = data.clinical_context  || '\u2014';
    el = document.getElementById('ctx-total'); if (el) el.textContent = (data.total_pubmed||0).toLocaleString() + (data.candidates_fetched ? ' \u2022 ' + data.candidates_fetched + ' fetched' : '');
    el = document.getElementById('ctx-query'); if (el) el.textContent = (data.queries_used || [data.query_translation]).join(' | ');
    showEl('ev-context');

    renderEvidenceCards(data.articles || []);

  } catch(e) {
    prog.stop();
    evShowError('Request failed \u2014 check your connection and try again. (' + e.message + ')');
  } finally {
    btn.disabled = false;
    btnT.textContent = 'Search & Rank';
    spin.classList.add('hidden');
  }
}

function renderEvidenceCards(articles) {
  var container = document.getElementById('ev-results');
  if (!articles || articles.length === 0) {
    container.innerHTML = '<div class="empty-msg">No results found. Try broadening your query or removing the year filter.</div>';
    return;
  }
  var grid = document.createElement('div');
  grid.className = 'evidence-grid';
  articles.forEach(function(a) {
    var card = document.createElement('div');
    card.className = 'ev-card';
    var doiLink = a.doi
      ? '<a href="https://doi.org/' + escHtml(a.doi) + '" target="_blank" rel="noopener noreferrer" class="link-chip">DOI \u2197</a>'
      : '';
    var country = a.country
      ? '<span class="metric-badge metric--country">\uD83D\uDCCD ' + escHtml(a.country) + '</span>'
      : '';
    card.innerHTML =
      '<div class="ev-rank">#' + a.rank + (a.relevance_score != null ? '<span class="rel-score">' + Math.round(a.relevance_score * 100) + '% match</span>' : '') + '</div>' +
      '<div class="ev-body">' +
        '<h4 class="ev-title">' +
          '<a href="' + escHtml(a.pmid_url) + '" target="_blank" rel="noopener noreferrer">' + escHtml(a.title) + '</a>' +
        '</h4>' +
        '<div class="ev-meta">' +
          '<span class="ev-author">' + escHtml(a.authors) + '</span>' +
          '<span class="ev-sep">\u00b7</span>' +
          '<span class="ev-journal">' + escHtml(a.journal) + '</span>' +
          '<span class="ev-sep">\u00b7</span>' +
          '<span class="ev-year">' + escHtml(String(a.year)) + '</span>' +
        '</div>' +
        '<div class="ev-metrics">' +
          '<span class="metric-badge metric--cit">\u25c9 ' + (a.citations||0).toLocaleString() + ' citations</span>' +
          '<span class="metric-badge metric--inf">\u2605 ' + (a.inf_citations||0) + ' influential</span>' +
          '<span class="metric-badge metric--if">\u2197 ' + (a.impact_est||0) + ' est. IF</span>' +
          country +
        '</div>' +
        '<div class="ev-pubtypes">' + tagsHtml(a.pub_types, 'pub-tag') + '</div>' +
        '<p class="ev-abstract">' + escHtml(a.abstract) + ((a.abstract && a.abstract.length >= 600) ? '\u2026' : '') + '</p>' +
        '<div class="ev-links">' +
          '<a href="' + escHtml(a.pmid_url) + '" target="_blank" rel="noopener noreferrer" class="link-chip">PubMed \u2197</a>' +
          doiLink +
        '</div>' +
      '</div>';
    grid.appendChild(card);
  });
  container.innerHTML = '';
  container.appendChild(grid);
}

function evShowError(msg) {
  var el = document.getElementById('ev-results');
  if (el) el.innerHTML = '<div class="error-msg" role="alert">\u26a0 ' + escHtml(msg) + '</div>';
}
