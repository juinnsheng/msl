/**
 * evidence.js — Top Evidence dashboard
 * External file for CSP compliance (no unsafe-inline)
 */

async function evSearch() {
  const q = document.getElementById('ev-question').value.trim();
  if (!q) return showToast('Please enter a clinical question.', 'error');
  const btn  = document.getElementById('ev-search-btn');
  const btnT = document.getElementById('ev-btn-text');
  const spin = document.getElementById('ev-spinner');
  btn.disabled = true; btnT.textContent = 'Searching…'; spin.classList.remove('hidden');
  document.getElementById('ev-results').innerHTML = '<div class="loading-msg">Fetching PubMed records and ranking with AI…</div>';
  document.getElementById('ev-context').classList.add('hidden');

  try {
    const resp = await fetch('/api/evidence/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question:          q,
        top_n:             +document.getElementById('ev-top-n').value,
        min_year:          document.getElementById('ev-min-year').value || null,
        enrich_citations:  document.getElementById('ev-enrich').checked,
        use_llm_rank:      document.getElementById('ev-llm-rank').checked,
      })
    });
    const data = await resp.json();
    if (!resp.ok) { evShowError(data.error); return; }

    document.getElementById('ctx-area').textContent  = data.therapeutic_area || '—';
    document.getElementById('ctx-ctx').textContent   = data.clinical_context  || '—';
    document.getElementById('ctx-total').textContent = (data.total_pubmed||0).toLocaleString();
    document.getElementById('ctx-query').textContent = data.query_translation  || '';
    document.getElementById('ev-context').classList.remove('hidden');
    renderEvidenceCards(data.articles);
  } catch(e) {
    evShowError('Request failed. Please try again.');
  } finally {
    btn.disabled = false; btnT.textContent = 'Search & Rank'; spin.classList.add('hidden');
  }
}

function renderEvidenceCards(articles) {
  const container = document.getElementById('ev-results');
  if (!articles?.length) {
    container.innerHTML = '<div class="empty-msg">No results found.</div>'; return;
  }
  const grid = document.createElement('div');
  grid.className = 'evidence-grid';
  articles.forEach(a => {
    const card = document.createElement('div');
    card.className = 'ev-card';
    card.innerHTML = `
      <div class="ev-rank">#${a.rank}</div>
      <div class="ev-body">
        <h4 class="ev-title">
          <a href="${escHtml(a.pmid_url)}" target="_blank" rel="noopener noreferrer">${escHtml(a.title)}</a>
        </h4>
        <div class="ev-meta">
          <span class="ev-author">${escHtml(a.authors)}</span>
          <span class="ev-sep">·</span>
          <span class="ev-journal">${escHtml(a.journal)}</span>
          <span class="ev-sep">·</span>
          <span class="ev-year">${escHtml(String(a.year))}</span>
        </div>
        <div class="ev-metrics">
          <span class="metric-badge metric--cit">◉ ${(a.citations||0).toLocaleString()} citations</span>
          <span class="metric-badge metric--inf">★ ${a.inf_citations||0} influential</span>
          <span class="metric-badge metric--if">↗ ${a.impact_est||0} est. IF</span>
          ${a.country ? `<span class="metric-badge metric--country">📍 ${escHtml(a.country)}</span>` : ''}
        </div>
        <div class="ev-pubtypes">${tagsHtml(a.pub_types,'pub-tag')}</div>
        <p class="ev-abstract">${escHtml(a.abstract)}${(a.abstract?.length||0) >= 600 ? '…' : ''}</p>
        <div class="ev-links">
          <a href="${escHtml(a.pmid_url)}" target="_blank" rel="noopener noreferrer" class="link-chip">PubMed ↗</a>
          ${a.doi ? `<a href="https://doi.org/${escHtml(a.doi)}" target="_blank" rel="noopener noreferrer" class="link-chip">DOI ↗</a>` : ''}
        </div>
      </div>`;
    grid.appendChild(card);
  });
  container.innerHTML = '';
  container.appendChild(grid);
}

function evShowError(msg) {
  document.getElementById('ev-results').innerHTML =
    `<div class="error-msg" role="alert">⚠ ${escHtml(msg)}</div>`;
}

// Ctrl+Enter to submit
document.addEventListener('DOMContentLoaded', function() {
  document.getElementById('ev-question')?.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') evSearch();
  });
});
