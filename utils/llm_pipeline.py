"""
llm_pipeline.py  —  MSL Intel Platform
Smart retrieval pipeline for Medical Science Liaisons.

Retrieval strategy (3-stage):
  Stage 1 — Query decomposition: LLM generates 3 targeted sub-queries
            (primary, population-specific, outcomes-specific) + extracts
            key concepts used later for scoring.
  Stage 2 — Abstract-aware relevance scoring: LLM reads the actual
            abstract snippet, not just title/MeSH. Scores 0.0-1.0.
  Stage 3 — Hard relevance cutoff: papers scoring < RELEVANCE_THRESHOLD
            are dropped entirely, not just ranked lower.
"""

import json
import re
import os
import time
from datetime import datetime
from typing import Optional
import requests
from openai import OpenAI

# ── Client singleton (initialised by app.py) ──────────────────────
_client: Optional[OpenAI] = None

MODELS = {
    "fast":    "meta/llama-3.3-70b-instruct",
    "quality": "meta/llama-3.3-70b-instruct",   # same model, kept for compat
    "balance": "meta/llama-3.3-70b-instruct",
}

# Papers with relevance score below this are REMOVED from results entirely
RELEVANCE_THRESHOLD = 0.40


def get_nvidia_key():
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY is not set")
    return api_key


def init_client(api_key: str):
    global _client
    _client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=api_key,
    )


# ── Core LLM call ─────────────────────────────────────────────────
def _llm(messages: list, model_key: str = "fast", temperature: float = 0.1,
         max_tokens: int = 1024) -> str:
    if _client is None:
        raise RuntimeError("LLM client not initialised. Set NVIDIA_API_KEY.")
    mid = MODELS.get(model_key, MODELS["fast"])
    return _client.chat.completions.create(
        model=mid,
        messages=messages,
        temperature=temperature,
        top_p=0.7,
        max_tokens=max_tokens,
    ).choices[0].message.content


# ── Robust JSON parser ────────────────────────────────────────────
def _parse_json(text: str):
    t = text.strip()
    if "```json" in t:
        t = t.split("```json")[1].split("```")[0]
    elif "```" in t:
        t = t.split("```")[1].split("```")[0]
    t = t.strip()
    match = re.search(r'(\{.*\}|\[.*\])', t, re.DOTALL)
    if match:
        t = match.group(1)
    return json.loads(t)


# ══════════════════════════════════════════════════════════════════
# STAGE 1 — Smart query decomposition
# ══════════════════════════════════════════════════════════════════
# Generates 3 targeted sub-queries instead of one broad query.
# Each sub-query focuses on a different retrieval angle:
#   primary_query   — core intervention + outcome (most precise)
#   population_query — population/indication focused
#   outcomes_query   — endpoint/mechanism focused
#
# Also extracts key_drugs, key_outcomes, study_types used later
# as EXPLICIT relevance anchors during scoring (Stage 2).

_DECOMPOSE_PROMPT = (
    "You are a Senior Medical Science Liaison with expertise in clinical evidence retrieval.\n"
    "Analyse the MSL enquiry below and decompose it into 3 targeted PubMed search queries.\n\n"
    "Rules for PubMed queries:\n"
    "- Use MeSH terms with [MeSH] tag where applicable\n"
    "- Use Boolean operators (AND, OR, NOT)\n"
    "- Add [tiab] for free-text title/abstract searches\n"
    "- primary_query: most specific — drug + outcome + population + study design filter\n"
    "- population_query: broaden to population/indication, keep drug specific\n"
    "- outcomes_query: focus on the specific endpoint/mechanism asked about\n\n"
    "Return ONLY a single line of valid JSON, no markdown:\n"
    '{"primary_query":"...","population_query":"...","outcomes_query":"...",'
    '"clinical_context":"2-sentence clinical framing of this enquiry",'
    '"therapeutic_area":"single phrase",'
    '"key_drugs":["drug1","drug2"],'
    '"key_outcomes":["outcome1","outcome2"],'
    '"key_population":"brief description",'
    '"study_types":["RCT","Meta-Analysis"]}\n\n'
    "MSL Enquiry: "
)

def refine_query(question: str) -> dict:
    """
    Stage 1: Decompose the MSL enquiry into 3 targeted sub-queries.
    Returns dict with primary_query, population_query, outcomes_query,
    plus clinical context and key concepts for downstream scoring.
    """
    prompt = _DECOMPOSE_PROMPT + question
    resp = _llm(
        [{"role": "user", "content": prompt}],
        model_key="fast",
        max_tokens=600,
    )
    try:
        result = _parse_json(resp)
        # Ensure required keys exist with fallbacks
        if "primary_query" not in result:
            result["primary_query"] = question
        result.setdefault("population_query", question)
        result.setdefault("outcomes_query",   question)
        result.setdefault("clinical_context", "")
        result.setdefault("therapeutic_area", "")
        result.setdefault("key_drugs",    [])
        result.setdefault("key_outcomes", [])
        result.setdefault("key_population", "")
        result.setdefault("study_types",  [])
        # Keep pubmed_query as alias for primary for backwards compat
        result["pubmed_query"]  = result["primary_query"]
        result["general_query"] = question
        return result
    except Exception:
        return {
            "pubmed_query":     question,
            "primary_query":    question,
            "population_query": question,
            "outcomes_query":   question,
            "general_query":    question,
            "clinical_context": "",
            "therapeutic_area": "",
            "key_drugs":        [],
            "key_outcomes":     [],
            "key_population":   "",
            "study_types":      [],
        }


# ══════════════════════════════════════════════════════════════════
# STAGE 2 — Abstract-aware relevance scoring with hard cutoff
# ══════════════════════════════════════════════════════════════════
# Key improvements over old approach:
#   - LLM reads the actual abstract text, not just title+MeSH
#   - Anchors scoring against specific drugs, outcomes, population
#     extracted in Stage 1 (no more generic "is this relevant?")
#   - Scores labelled IRRELEVANT (< 0.40) are dropped, not ranked

_SCORE_SYSTEM = (
    "You are a strict MSL relevance assessor. Your job is to score whether each paper "
    "directly answers the MSL's specific clinical question.\n"
    "Scoring criteria (be strict — most papers should score below 0.5):\n"
    "  1.0 — Exactly matches: correct drug(s), correct outcome, correct population, RCT or meta-analysis\n"
    "  0.8 — Matches drug + outcome but different population or observational design\n"
    "  0.6 — Matches the outcome area but different drug or indirect evidence\n"
    "  0.4 — Tangentially related — shares some concepts but doesn't answer the question\n"
    "  0.2 — Same therapeutic area but wrong drug/outcome\n"
    "  0.0 — Completely irrelevant, editorial, letter, or protocol without results\n"
    "Return ONLY a JSON array of numbers, one per paper, e.g. [0.9,0.2,0.6,...]\n"
    "Be conservative. It is better to exclude a borderline paper than include an irrelevant one."
)

def _build_score_prompt(articles: list, question: str, refined: dict) -> str:
    """Build a rich scoring prompt using abstract text and key concept anchors."""
    key_drugs    = ", ".join(refined.get("key_drugs", []))    or "not specified"
    key_outcomes = ", ".join(refined.get("key_outcomes", [])) or "not specified"
    key_pop      = refined.get("key_population", "")          or "not specified"
    study_types  = ", ".join(refined.get("study_types", []))  or "any"

    header = (
        f"MSL Clinical Question: {question}\n"
        f"Target drug(s): {key_drugs}\n"
        f"Target outcome(s): {key_outcomes}\n"
        f"Target population: {key_pop}\n"
        f"Preferred study type(s): {study_types}\n\n"
        "Papers to score:\n"
    )
    lines = []
    for i, a in enumerate(articles):
        abstract_snippet = (a.get("abstract") or "")[:400]
        mesh = (a.get("mesh_major_str") or "")[:100]
        pub_types = (a.get("pub_types_str") or "")[:80]
        lines.append(
            f"[{i}] Title: {a.get('title','')[:150]}\n"
            f"    Year: {a.get('year','')} | Types: {pub_types} | MeSH: {mesh}\n"
            f"    Abstract: {abstract_snippet}\n"
        )
    return header + "\n".join(lines)


def batch_score_relevance(articles: list, question: str,
                          refined: dict = None,
                          batch_size: int = 15) -> list:
    """
    Stage 2: Score each article's relevance to the MSL question.
    Uses abstract text + key concept anchors for precise scoring.
    refined: output from refine_query() — if None, falls back to title-only
    """
    if refined is None:
        refined = {}

    scores = []
    for i in range(0, len(articles), batch_size):
        chunk = articles[i:i + batch_size]
        prompt = _build_score_prompt(chunk, question, refined)
        try:
            resp = _llm(
                [
                    {"role": "system", "content": _SCORE_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                model_key="fast",
                temperature=0.0,
                max_tokens=300,
            )
            chunk_scores = _parse_json(resp)
            if not isinstance(chunk_scores, list):
                chunk_scores = [0.5] * len(chunk)
            # Pad/truncate to chunk length
            chunk_scores = (list(chunk_scores) + [0.5] * len(chunk))[:len(chunk)]
            # Clamp to [0, 1]
            chunk_scores = [max(0.0, min(1.0, float(s))) for s in chunk_scores]
        except Exception:
            chunk_scores = [0.5] * len(chunk)
        scores.extend(chunk_scores)
        time.sleep(0.2)
    return scores


# ══════════════════════════════════════════════════════════════════
# STAGE 3 — Combined ranking with hard relevance cutoff
# ══════════════════════════════════════════════════════════════════

def rank_articles(articles: list, question: str, top_n: int = 50,
                  min_year: Optional[int] = None,
                  refined: dict = None) -> list:
    """
    Full 3-stage ranking:
      1. Filter by year (optional)
      2. Score relevance with abstract awareness
      3. Drop papers below RELEVANCE_THRESHOLD
      4. Combine relevance + citation impact + recency
      5. Return top_n

    If refined is provided (from refine_query()), scoring uses
    drug/outcome/population anchors for much higher precision.
    """
    current_year = datetime.utcnow().year

    # Year filter
    if min_year:
        filtered = [a for a in articles if int(a.get("year", 0) or 0) >= min_year]
        candidates = filtered if filtered else articles
    else:
        candidates = articles

    if not candidates:
        return []

    # Score with abstract awareness
    rel_scores = batch_score_relevance(candidates, question, refined=refined)

    # Combine scores
    combined = []
    for art, rel in zip(candidates, rel_scores):
        # Hard cutoff — irrelevant papers are dropped entirely
        if rel < RELEVANCE_THRESHOLD:
            continue

        citations     = int(art.get("citation_count", 0) or 0)
        year          = int(art.get("year") or current_year)
        years_age     = max(1, current_year - year)
        recency_bonus = 0.08 if years_age <= 2 else (0.04 if years_age <= 5 else 0.0)
        norm_cit      = citations / (citations + 50)

        # Weights: relevance is king (70%), citations (20%), recency (10%)
        score = 0.70 * rel + 0.20 * norm_cit + 0.10 * recency_bonus
        combined.append((score, rel, art))

    # Sort by combined score
    combined.sort(key=lambda x: x[0], reverse=True)

    # Tag each article with its relevance score for transparency
    result = []
    for score, rel, art in combined[:top_n]:
        art["_relevance_score"] = round(rel, 2)
        art["_combined_score"]  = round(score, 2)
        result.append(art)

    return result


# ══════════════════════════════════════════════════════════════════
# Evidence extraction — full MSL-grade, single paper (unchanged)
# ══════════════════════════════════════════════════════════════════

_EXTRACT_PASS1 = (
    "You are a Senior MSL extracting clinical trial data. "
    "Read the abstract carefully. Return ONLY valid compact JSON, no markdown.\n"
    "Extract EXACTLY these fields (use NR if genuinely not mentioned):\n"
    '{"Study Design":"","Study Phase":"NR","Study Type":"NR",'
    '"Sample Size":"NR","Follow-up Duration":"NR",'
    '"Randomisation":"NR","Blinding":"NR","Control Type":"NR",'
    '"Population / Indication":"NR","Inclusion Criteria":"NR","Exclusion Criteria":"NR",'
    '"Intervention":"NR","Dose / Regimen":"NR",'
    '"Comparator / Control":"NR","Background Therapy":"NR",'
    '"Countries / Sites":"NR","Funding Source":"NR",'
    '"Trial Registration":"NR"}\n'
    "Be specific — e.g. Intervention should include drug name + dose + route + frequency.\n"
    "Comparator should include active comparator name or 'placebo'.\n"
    "Paper:\n"
)

_EXTRACT_PASS2 = (
    "You are a Senior MSL extracting clinical outcomes. "
    "Read the abstract carefully. Return ONLY valid compact JSON, no markdown.\n"
    "Extract EXACTLY these fields (use NR if genuinely not mentioned):\n"
    '{"Primary Endpoint":"NR","Secondary Endpoints":"NR","Exploratory Endpoints":"NR",'
    '"Key Efficacy Outcomes":"NR",'
    '"Effect Size (Primary)":"NR","95% CI":"NR","P-value":"NR","NNT":"NR","NNH":"NR",'
    '"Relative Risk Reduction":"NR","Absolute Risk Reduction":"NR",'
    '"Statistical Method":"NR","ITT / Per-Protocol":"NR",'
    '"Subgroup Analyses":"NR",'
    '"Safety Population (N)":"NR","Any AE (%)":"NR","Serious AE (%)":"NR",'
    '"Discontinuation due to AE (%)":"NR","Key AEs of Interest":"NR","Deaths (%)":"NR",'
    '"Limitations":"NR","Guideline Relevance":"NR","MSL Key Message":"NR"}\n'
    "Be specific — Effect Size should include HR/OR/RR + direction + magnitude.\n"
    "Primary Endpoint must be the exact endpoint name as stated in the abstract.\n"
    "Paper:\n"
)

def _paper_context(paper: dict) -> str:
    sections = paper.get("abstract_sections", {})
    if sections and len(sections) > 1:
        ab = " | ".join(
            f"[{k.upper()}] {v[:300]}"
            for k, v in sections.items()
            if k != "full" and v
        )
    else:
        ab = paper.get("abstract", "")[:2000]

    return (
        f"Title: {paper.get('title', '')[:200]}\n"
        f"Authors: {paper.get('first_author', '')} et al.\n"
        f"Year: {paper.get('year', '')} | Journal: {paper.get('journal_full', '')[:80]}\n"
        f"Publication Types: {paper.get('pub_types_str', '')[:100]}\n"
        f"MeSH Major: {paper.get('mesh_major_str', '')[:150]}\n"
        f"Abstract: {ab}"
    )


def extract_evidence_row(paper: dict) -> dict:
    context = _paper_context(paper)

    try:
        resp1 = _llm(
            [{"role": "user", "content": _EXTRACT_PASS1 + context}],
            model_key="fast", temperature=0.0, max_tokens=700,
        )
        row = _parse_json(resp1)
    except Exception as e:
        row = {"Study Title": paper.get("title", ""), "extraction_error_pass1": str(e)}

    time.sleep(0.3)

    try:
        resp2 = _llm(
            [{"role": "user", "content": _EXTRACT_PASS2 + context}],
            model_key="fast", temperature=0.0, max_tokens=700,
        )
        row2 = _parse_json(resp2)
        row.update(row2)
    except Exception as e:
        row["extraction_error_pass2"] = str(e)

    row.update({
        "PMID":                  paper.get("pmid", ""),
        "Study Title":           paper.get("title", ""),
        "First Author":          paper.get("first_author", ""),
        "All Authors":           paper.get("all_authors", ""),
        "Year":                  paper.get("year", ""),
        "Journal":               paper.get("journal_full", ""),
        "Abstract":              paper.get("abstract", ""),
        "MeSH Major":            paper.get("mesh_major_str", ""),
        "MeSH All":              paper.get("mesh_str", ""),
        "Keywords":              paper.get("keywords_str", ""),
        "Publication Types":     paper.get("pub_types_str", ""),
        "Chemicals":             paper.get("chemicals_str", ""),
        "COI":                   paper.get("conflict_of_interest", ""),
        "Grants":                paper.get("grants_str", ""),
        "Citation Count":        paper.get("citation_count", 0),
        "Influential Citations": paper.get("influential_citations", 0),
        "Impact Factor Est":     paper.get("impact_factor_est", 0.0),
        "DOI":                   paper.get("doi", ""),
        "PubMed URL":            paper.get("url_pubmed", ""),
        "Open Access PDF":       paper.get("open_access_pdf", ""),
    })
    return row


# ── MSL Chatbot ───────────────────────────────────────────────────
_CHATBOT_SYS_HEADER = (
    "You are an expert MSL assistant. Answer clinical questions using ONLY the evidence below.\n"
    "Cite papers as [N]. Never fabricate data. Be concise (<=250 words unless asked).\n"
    "Evidence:\n"
)

def build_context_str(records: list, max_r: int = 20) -> str:
    lines = []
    for i, r in enumerate(records[:max_r], 1):
        lines.append(
            f"[{i}] {r.get('title','')[:100]} | "
            f"{r.get('first_author','')} | {r.get('journal_full','')} {r.get('year','')} | "
            f"cites:{r.get('citation_count',0)} | "
            f"{r.get('abstract','')[:300]}..."
        )
    return "\n".join(lines)


def chatbot_answer(question: str, records: list, history: list) -> str:
    context = build_context_str(records)
    sys_msg = {"role": "system", "content": _CHATBOT_SYS_HEADER + context}
    msgs    = [sys_msg] + history[-8:] + [{"role": "user", "content": question}]
    return _llm(msgs, model_key="fast", temperature=0.3, max_tokens=800)


# ── ClinicalTrials.gov v2 search ──────────────────────────────────
CT_API = "https://clinicaltrials.gov/api/v2/studies"

def search_clinical_trials(query: str, max_results: int = 100) -> list:
    params = {
        "query.term": query,
        "pageSize":   min(max_results, 100),
        "format":     "json",
        "fields": (
            "NCTId,BriefTitle,OfficialTitle,OverallStatus,Phase,"
            "StudyType,StartDate,CompletionDate,EnrollmentCount,"
            "Condition,InterventionName,PrimaryOutcomeMeasure,"
            "BriefSummary,LeadSponsorName,LocationCountry"
        ),
    }
    studies    = []
    next_token = None

    while len(studies) < max_results:
        if next_token:
            params["pageToken"] = next_token

        r = requests.get(CT_API, params=params, timeout=30)
        if not r.ok:
            raise RuntimeError(
                f"ClinicalTrials.gov returned HTTP {r.status_code}. "
                f"The service may be temporarily unavailable."
            )
        try:
            data = r.json()
        except ValueError:
            raise RuntimeError(
                "ClinicalTrials.gov returned an unexpected response (not JSON). "
                "The service may be temporarily unavailable."
            )

        for s in data.get("studies", []):
            proto         = s.get("protocolSection", {})
            ident         = proto.get("identificationModule", {})
            status        = proto.get("statusModule", {})
            design        = proto.get("designModule", {})
            desc          = proto.get("descriptionModule", {})
            sponsor       = proto.get("sponsorCollaboratorsModule", {})
            outcomes      = proto.get("outcomesModule", {})
            interventions = proto.get("armsInterventionsModule", {})
            conditions    = proto.get("conditionsModule", {})
            contacts      = proto.get("contactsLocationsModule", {})

            ivn_names = [i.get("interventionName", "") for i in interventions.get("interventions", [])]
            prim_out  = [o.get("measure", "")          for o in outcomes.get("primaryOutcomes", [])]
            conds     = conditions.get("conditions", [])
            countries = list({
                loc.get("locationCountry", "")
                for loc in contacts.get("locations", [])
                if loc.get("locationCountry")
            })

            studies.append({
                "nct_id":          ident.get("nctId", ""),
                "title":           ident.get("briefTitle", ""),
                "official_title":  ident.get("officialTitle", ""),
                "status":          status.get("overallStatus", ""),
                "phase":           ", ".join(design.get("phases", [])),
                "study_type":      design.get("studyType", ""),
                "start_date":      status.get("startDateStruct", {}).get("date", ""),
                "completion_date": status.get("primaryCompletionDateStruct", {}).get("date", ""),
                "enrollment":      design.get("enrollmentInfo", {}).get("count", ""),
                "conditions":      "; ".join(conds),
                "interventions":   "; ".join(ivn_names),
                "primary_outcome": "; ".join(prim_out),
                "summary":         desc.get("briefSummary", "")[:500],
                "sponsor":         sponsor.get("leadSponsor", {}).get("name", ""),
                "countries":       "; ".join(countries),
                "url":             f"https://clinicaltrials.gov/study/{ident.get('nctId', '')}",
            })

        next_token = data.get("nextPageToken")
        if not next_token or len(studies) >= max_results:
            break

    return studies[:max_results]
