# 🧬 MSL Intelligence Platform

> **AI-powered Medical Science Liaison research tool** — automated PubMed evidence retrieval, LLM-driven relevance ranking, clinical trial discovery, and structured data extraction. Built for pharmaceutical and biotech MSL teams.

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=flat&logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![NVIDIA NIM](https://img.shields.io/badge/NVIDIA-NIM%20LLMs-76B900?style=flat&logo=nvidia&logoColor=white)](https://www.nvidia.com/en-us/ai/)
[![PubMed](https://img.shields.io/badge/NCBI-PubMed%20API-326789?style=flat)](https://pubmed.ncbi.nlm.nih.gov)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Features](#-features)
- [Tech Stack](#-tech-stack)
- [Getting Started](#-getting-started)
- [Environment Variables](#-environment-variables)
- [Usage](#-usage)
- [API Reference](#-api-reference)
- [Architecture](#-architecture)
- [Free-Tier Notes](#-free-tier-notes)
- [Security](#-security)
- [Roadmap](#-roadmap)

---

## 🔬 Overview

The **MSL Intelligence Platform** automates the most time-consuming parts of medical literature research. A user submits a clinical question in natural language; the platform decomposes it into targeted PubMed sub-queries, fetches and deduplicates up to 120 candidate papers, scores them for relevance using an LLM, and returns a ranked, structured evidence table — all in under 60 seconds.

Designed for **Medical Science Liaisons, Medical Affairs teams, and clinical researchers** who need fast, credible, publication-ready evidence summaries.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🔍 **Smart Query Decomposition** | LLM breaks natural-language questions into PubMed-optimised sub-queries (population, outcomes, study design) |
| 📚 **Multi-Query PubMed Search** | Parallel NCBI E-utilities calls with PMID deduplication across up to 3 targeted queries |
| 🤖 **LLM Relevance Ranking** | NVIDIA NIM scores each abstract for clinical relevance; irrelevant papers filtered before display |
| 📊 **Bulk Review + Extraction** | Batch-extract structured evidence rows (PICO, endpoints, conclusions) from abstracts into Excel |
| 🏥 **Clinical Trials Discovery** | ClinicalTrials.gov integration with status filtering and Excel export |
| 💬 **AI Chat on Results** | Ask follow-up questions against loaded literature or trial records |
| 📈 **Citation Enrichment** | Optional citation count and impact factor estimation per paper |
| 📥 **Excel Export** | One-click `.xlsx` downloads for evidence tables and extracted data |
| 🔄 **Progress Polling** | `/api/status` endpoint for real-time progress bars without WebSockets |

---

## 🛠 Tech Stack

**Backend**
- [Flask](https://flask.palletsprojects.com/) — web framework
- [Flask-Login](https://flask-login.readthedocs.io/) — session authentication
- [Flask-Limiter](https://flask-limiter.readthedocs.io/) — rate limiting (Redis or in-process)
- [Flask-WTF](https://flask-wtf.readthedocs.io/) — CSRF protection
- [NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25499/) — PubMed search & fetch
- [NVIDIA NIM](https://www.nvidia.com/en-us/ai/) — LLM inference (query decomposition, ranking, extraction)
- [Pandas](https://pandas.pydata.org/) + [openpyxl](https://openpyxl.readthedocs.io/) — data handling & Excel generation

**Infrastructure**
- [Redis](https://redis.io/) (optional) — distributed rate limiting & session storage
- [Railway](https://railway.app/) — deployment target
- Filesystem session fallback for zero-config local development

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- (Optional) Redis instance for distributed rate limiting
- NCBI API key — [register free here](https://www.ncbi.nlm.nih.gov/account/)
- NVIDIA API key — [get NIM access here](https://www.nvidia.com/en-us/ai/)

### Installation

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/msl-intelligence-platform.git
cd msl-intelligence-platform

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy env template and fill in your keys
cp .env.example .env
```

### Run Locally

```bash
python app.py
# App runs at http://localhost:5000
```

---

## 🔑 Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | ✅ | Flask session secret (use `secrets.token_hex(32)`) |
| `ADMIN_USERNAME` | ✅ | Login username |
| `ADMIN_PASSWORD` | ✅ | Login password |
| `NCBI_API_KEY` | Recommended | NCBI E-utilities key (unlocks 10 req/s vs 3 req/s) |
| `NVIDIA_API_KEY` | Recommended | Enables LLM ranking, extraction, and chat |
| `RATELIMIT_STORAGE_URI` | Optional | Redis URI e.g. `redis://localhost:6379` |
| `WTF_CSRF_SECRET_KEY` | Optional | Separate CSRF key (auto-generated if omitted) |
| `ADMIN_UNLOCK_TOKEN` | Optional | Token for `/admin/unlock/<ip>` endpoint |
| `FLASK_ENV` | Optional | Set to `production` to enable HSTS + secure cookies |

> ⚠️ **Never commit `.env` to version control.** Add it to `.gitignore`.

---

## 💡 Usage

### 1. Evidence Search (`/evidence`)
Enter a clinical question (e.g. *"efficacy of pembrolizumab in PD-L1 high NSCLC"*). The platform:
1. Decomposes the question into 3 targeted PubMed queries
2. Fetches up to 120 candidate papers and deduplicates by PMID
3. Scores each abstract for relevance
4. Returns a ranked table (default 15, max 50 on free tier)

### 2. Bulk Review (`/review`)
Search → get up to 50 papers → click **Extract** to run LLM extraction on each abstract → download structured Excel with PICO elements, study design, key findings.

### 3. Clinical Trials (`/clinicaltrials`)
Natural-language search against ClinicalTrials.gov with status filtering (Recruiting, Completed, etc.) and Excel export.

### 4. AI Chat
After loading a search, ask follow-up questions: *"Which of these are RCTs?"*, *"Summarise the survival outcomes."*

---

## 📡 API Reference

All endpoints require session authentication. Send `X-CSRFToken` header on POST requests.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/status` | Poll current operation progress |
| `GET` | `/health` | Health check (no auth) |
| `POST` | `/api/evidence/search` | Run evidence search |
| `POST` | `/api/review/search` | Run bulk review search |
| `POST` | `/api/review/extract` | LLM extraction on loaded records |
| `GET` | `/api/review/extract_capacity` | How many records can be safely extracted |
| `GET` | `/api/review/download` | Download review Excel |
| `GET` | `/api/review/download_extracted` | Download extraction Excel |
| `POST` | `/api/ct/search` | Search ClinicalTrials.gov |
| `GET` | `/api/ct/download` | Download trials Excel |
| `POST` | `/api/chat` | Chat against loaded records |

### Example — Evidence Search Request

```json
POST /api/evidence/search
{
  "question": "dupilumab atopic dermatitis EASI score RCT",
  "top_n": 15,
  "min_year": 2019,
  "use_llm_rank": true,
  "enrich_citations": false
}
```

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────┐
│                  Browser / Client                │
└───────────────────┬─────────────────────────────┘
                    │ HTTPS + CSRF token
┌───────────────────▼─────────────────────────────┐
│              Flask App  (app.py)                 │
│  Auth │ Rate Limiter │ CSP Headers │ /api/status │
└──┬──────────────┬──────────────────┬────────────┘
   │              │                  │
   ▼              ▼                  ▼
pubmed_       llm_pipeline       citation_
fetcher.py    (NVIDIA NIM)       enricher.py
   │              │
   ▼              ▼
NCBI E-utils   NVIDIA NIM API
ClinicalTrials.gov
```

**Request flow (Evidence Search):**
1. LLM decomposes question → 3 PubMed sub-queries
2. Parallel PubMed `esearch` → PMID pool (deduped)
3. `efetch` → full records (up to 120)
4. LLM ranks abstracts → top N returned
5. Optional: citation enrichment via Semantic Scholar

---

## 🆓 Free-Tier Notes

The platform is optimised to run within NVIDIA NIM free-tier limits:

| Limit | Value | Reason |
|---|---|---|
| Default results | 30 | PubMed + LLM calls balanced |
| Max results | 50 | Hard cap with user warning |
| Extraction cap | 30 papers | ~40 RPM NVIDIA NIM limit |
| Inter-call gap | configurable | Prevents 429 errors |
| Session lifetime | 8 hours | Extended for research sessions |

> If `NVIDIA_API_KEY` is not set, the platform runs in **keyword-only mode** — PubMed search works, LLM ranking/extraction/chat are disabled with clear user messaging.

---

## 🔒 Security

- **PBKDF2-SHA256** password hashing (260,000 rounds)
- **CSRF protection** on all state-changing endpoints (Flask-WTF)
- **IP-based brute-force protection** — 5 failed attempts → 1-hour lockout
- **Content Security Policy** with per-request nonces
- **`Strict-Transport-Security`** in production
- **No VPN/datacenter blocking** — designed for corporate network users
- **Session hardening** — HttpOnly, SameSite=Lax, Secure in production
- **Scanner UA blocking** — sqlmap, nikto, masscan, dirbuster, nuclei rejected

---

## 🗺 Roadmap

- [ ] Multi-user support with role-based access
- [ ] PDF upload → abstract extraction
- [ ] Saved search history
- [ ] Slack / Teams notification on long extractions
- [ ] ORCID / institutional SSO
- [ ] Docker Compose setup

---

## 📄 License

MIT © 2025 — see [LICENSE](LICENSE) for details.

---

> Built for MSL teams who need answers fast. Contributions welcome — open an issue or PR.
