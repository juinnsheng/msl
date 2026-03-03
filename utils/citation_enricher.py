"""
citation_enricher.py
Enriches PubMed records with citation data.
Priority: Semantic Scholar → iCite NIH → Europe PMC → defaults

PERFORMANCE: Uses ThreadPoolExecutor for parallel fetching.
Hard wall-clock limit of 45s to stay under Railway's 60s proxy timeout.
"""

import time
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed

SEMANTIC_SCHOLAR_BASE = "https://api.semanticscholar.org/graph/v1/paper/"
ICITE_BASE            = "https://icite.od.nih.gov/api/pubs"
EUROPE_PMC_BASE       = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# Per-HTTP-request timeout (seconds)
_HTTP_TIMEOUT = 8
# Total wall-clock budget for the entire enrich call (seconds)
_WALL_BUDGET  = 45
# Max parallel workers
_WORKERS      = 6


def _safe_json(r: requests.Response) -> dict | None:
    """Return parsed JSON or None — never raises, never returns raw XML/HTML."""
    if not r.ok:
        return None
    ct = r.headers.get("Content-Type", "")
    if "json" not in ct and "javascript" not in ct:
        # Server returned XML, HTML, or something unexpected — discard silently
        return None
    try:
        return r.json()
    except Exception:
        return None


def _enrich_one(art: dict, current_year: int) -> dict:
    """Fetch citation data for a single article. Returns the (possibly enriched) article."""
    pmid    = str(art.get("pmid", "")).strip()
    doi     = (art.get("doi") or "").strip()
    success = False

    # ── 1. Semantic Scholar ────────────────────────────────────────
    if doi or pmid:
        try:
            identifier = f"DOI:{doi}" if doi else f"PubMed:{pmid}"
            url = f"{SEMANTIC_SCHOLAR_BASE}{identifier}?fields=citationCount,influentialCitationCount,year,venue"
            r = requests.get(url, timeout=_HTTP_TIMEOUT)
            d = _safe_json(r)
            if d and "citationCount" in d:
                art["citation_count"]       = int(d.get("citationCount") or 0)
                art["influential_citations"] = int(d.get("influentialCitationCount") or 0)
                year = int(d.get("year") or art.get("year") or current_year)
                art["year"]             = art.get("year") or str(year)
                art["venue"]            = d.get("venue") or art.get("journal_full", "")
                art["impact_factor_est"] = round(
                    art["citation_count"] / max(1, current_year - year), 2)
                art["cited_by_count"]   = art["citation_count"]
                success = True
        except Exception:
            pass

    # ── 2. iCite NIH ──────────────────────────────────────────────
    # iCite single-record endpoint returns the record directly (not in "data" array)
    if not success and pmid:
        try:
            r = requests.get(f"{ICITE_BASE}/{pmid}", timeout=_HTTP_TIMEOUT)
            d = _safe_json(r)
            if d:
                # Single record endpoint: flat dict with "pmid", "citation_count" etc.
                # Batch endpoint: {"data": [...], "meta": {...}}
                if "citation_count" in d:
                    e = d
                elif "data" in d and d["data"]:
                    e = d["data"][0]
                else:
                    e = None
                if e:
                    art["citation_count"]       = int(e.get("citation_count") or 0)
                    art["influential_citations"] = 0
                    year = int(e.get("year") or art.get("year") or current_year)
                    art["year"]             = art.get("year") or str(year)
                    art["impact_factor_est"] = round(
                        art["citation_count"] / max(1, current_year - year), 2)
                    art["venue"]            = e.get("journal") or art.get("journal_full", "")
                    art["cited_by_count"]   = art["citation_count"]
                    success = True
        except Exception:
            pass

    # ── 3. Europe PMC ─────────────────────────────────────────────
    if not success and pmid:
        try:
            r = requests.get(
                EUROPE_PMC_BASE,
                params={
                    "query":  f"EXT_ID:{pmid} AND src:MED",
                    "format": "json",
                    "resultType": "core",
                    "pageSize": 1,
                },
                timeout=_HTTP_TIMEOUT,
            )
            d = _safe_json(r)
            if d:
                results = d.get("resultList", {}).get("result", [])
                if results:
                    res = results[0]
                    art["citation_count"]       = int(res.get("citedByCount") or 0)
                    art["influential_citations"] = 0
                    year = int(res.get("pubYear") or art.get("year") or current_year)
                    art["year"]             = art.get("year") or str(year)
                    art["impact_factor_est"] = round(
                        art["citation_count"] / max(1, current_year - year), 2)
                    art["venue"]            = res.get("journalTitle") or art.get("journal_full", "")
                    art["cited_by_count"]   = art["citation_count"]
                    success = True
        except Exception:
            pass

    # ── 4. Defaults ───────────────────────────────────────────────
    if not success:
        art.setdefault("citation_count",       0)
        art.setdefault("influential_citations", 0)
        art.setdefault("impact_factor_est",     0.0)
        art.setdefault("cited_by_count",        0)
        art.setdefault("venue", art.get("journal_full", ""))

    return art


def enrich_with_citations(articles: list, sleep_sec: float = 0.0) -> list:
    """
    Enrich articles in parallel.
    sleep_sec kept for API signature compatibility but ignored
    (parallelism makes sequential sleep unnecessary).
    Hard wall-clock budget: _WALL_BUDGET seconds total.
    """
    if not articles:
        return articles

    current_year = datetime.utcnow().year
    results      = {i: art for i, art in enumerate(articles)}  # preserve order

    deadline = time.monotonic() + _WALL_BUDGET

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        future_to_idx = {
            pool.submit(_enrich_one, art, current_year): i
            for i, art in enumerate(articles)
        }

        for future in as_completed(future_to_idx, timeout=_WALL_BUDGET):
            if time.monotonic() > deadline:
                # Cancel remaining — we're over budget
                for f in future_to_idx:
                    f.cancel()
                break
            idx = future_to_idx[future]
            try:
                results[idx] = future.result(timeout=1)
            except Exception:
                # Keep original article unchanged on any error
                pass

    return [results[i] for i in range(len(articles))]
