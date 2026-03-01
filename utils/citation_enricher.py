"""
citation_enricher.py
Enriches PubMed records with citation data from multiple sources.
Priority: Semantic Scholar → iCite NIH → Europe PMC → defaults
"""

import time
import requests
from datetime import datetime

SEMANTIC_SCHOLAR_BASE = "https://api.semanticscholar.org/graph/v1/paper/"
ICITE_BASE            = "https://icite.od.nih.gov/api/pubs"
EUROPE_PMC_BASE       = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def enrich_with_citations(articles: list, sleep_sec: float = 0.4) -> list:
    current_year = datetime.utcnow().year
    for art in articles:
        pmid = str(art.get("pmid", ""))
        doi  = art.get("doi", "")
        success = False

        # 1. Semantic Scholar
        if doi or pmid:
            try:
                url = (SEMANTIC_SCHOLAR_BASE + f"DOI:{doi}" if doi
                       else SEMANTIC_SCHOLAR_BASE + f"PubMed:{pmid}")
                url += "?fields=citationCount,influentialCitationCount,year,venue"
                r = requests.get(url, timeout=12)
                if r.status_code == 200:
                    d = r.json()
                    art["citation_count"]       = d.get("citationCount", 0)
                    art["influential_citations"] = d.get("influentialCitationCount", 0)
                    year = int(d.get("year") or art.get("year") or current_year)
                    art["year"] = art.get("year") or str(year)
                    art["venue"] = d.get("venue") or art.get("journal_full", "Not Reported")
                    art["impact_factor_est"] = round(art["citation_count"] / max(1, current_year - year), 2)
                    art["cited_by_count"] = art["citation_count"]
                    success = True
            except Exception:
                pass
            time.sleep(sleep_sec)

        # 2. iCite NIH
        if not success and pmid:
            try:
                r = requests.get(f"{ICITE_BASE}/{pmid}", timeout=12)
                if r.status_code == 200:
                    data = r.json()
                    entries = data.get("data", [])
                    if entries:
                        e = entries[0]
                        art["citation_count"]       = e.get("citation_count", 0)
                        art["influential_citations"] = 0
                        year = int(e.get("year") or art.get("year") or current_year)
                        art["year"] = art.get("year") or str(year)
                        art["impact_factor_est"] = round(art["citation_count"] / max(1, current_year - year), 2)
                        art["venue"] = e.get("journal") or art.get("journal_full", "Not Reported")
                        art["cited_by_count"] = art["citation_count"]
                        success = True
            except Exception:
                pass
            time.sleep(sleep_sec)

        # 3. Europe PMC
        if not success and pmid:
            try:
                r = requests.get(
                    EUROPE_PMC_BASE,
                    params={"query": f"EXT_ID:{pmid} AND src:MED", "format": "json"},
                    timeout=12
                )
                if r.status_code == 200:
                    results = r.json().get("resultList", {}).get("result", [])
                    if results:
                        res = results[0]
                        art["citation_count"]       = int(res.get("citedByCount", 0))
                        art["influential_citations"] = 0
                        year = int(res.get("pubYear") or art.get("year") or current_year)
                        art["year"] = art.get("year") or str(year)
                        art["impact_factor_est"] = round(art["citation_count"] / max(1, current_year - year), 2)
                        art["venue"] = res.get("journalTitle") or art.get("journal_full", "Not Reported")
                        art["cited_by_count"] = art["citation_count"]
                        success = True
            except Exception:
                pass
            time.sleep(sleep_sec)

        # 4. Defaults
        if not success:
            art.setdefault("citation_count", 0)
            art.setdefault("influential_citations", 0)
            art.setdefault("impact_factor_est", 0.0)
            art.setdefault("cited_by_count", 0)
            art.setdefault("venue", art.get("journal_full", "Not Reported"))

    return articles
