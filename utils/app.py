"""
app.py  —  MSL Intelligence Platform  (Free-Tier Optimised Build)
Changes from v1:
- Removed datacenter/VPN IP blocking (was blocking legitimate corporate users)
- Relaxed rate limits for small-team usage
- Capped queries to 30 results (50 max with user warning) for free-tier API compliance
- Added /api/status endpoint for progress polling
- Graceful fallback when NVIDIA_API_KEY not set
- Clear user-facing error messages for API failures
"""

import os, io, re, json, time, hashlib, secrets, logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urljoin
from dotenv import load_dotenv
load_dotenv()

from flask import (Flask, render_template, request, jsonify, redirect,
                   url_for, session, send_file, abort, g)
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from flask_limiter import Limiter
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_session import Session
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd

from utils import pubmed_fetcher as pf
from utils.citation_enricher import enrich_with_citations
from utils import llm_pipeline as llm

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("msl_app")

# ── Session dir ───────────────────────────────────────────────────────
SESSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flask_session")
os.makedirs(SESSION_DIR, exist_ok=True)

# ── Free-tier limits ──────────────────────────────────────────────────
FREE_TIER_SOFT_LIMIT = 30   # Results shown by default
FREE_TIER_HARD_LIMIT = 50   # Absolute max results per query
FREE_TIER_EXTRACT_LIMIT = 30  # LLM extraction cap

# ── Runtime env guard ─────────────────────────────────────────────────
for _v in ("SECRET_KEY", "ADMIN_USERNAME", "ADMIN_PASSWORD"):
    if not os.environ.get(_v):
        raise RuntimeError(f"FATAL: env var '{_v}' is required. Application cannot start.")

# ── Redis / in-process fallback ───────────────────────────────────────
_REDIS_URI    = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
_redis_client = None
if _REDIS_URI.startswith("redis"):
    try:
        import redis as _rl
        _redis_client = _rl.from_url(_REDIS_URI, decode_responses=False)
        _redis_client.ping()
        logger.info("Redis connected")
    except Exception as e:
        logger.warning(f"Redis unavailable ({e}) — using in-process fallback")

_local: dict = {}

def _set(k, v, ttl=0):
    if _redis_client:
        _redis_client.set(k, v, ex=ttl or None)
    else:
        _local[k] = {"v": v, "exp": time.time() + ttl if ttl else None}

def _get(k):
    if _redis_client:
        return _redis_client.get(k)
    e = _local.get(k)
    if not e: return None
    if e["exp"] and time.time() > e["exp"]:
        _local.pop(k, None); return None
    return e["v"]

def _incr(k, ttl=0):
    if _redis_client:
        v = _redis_client.incr(k)
        if v == 1 and ttl: _redis_client.expire(k, ttl)
        return v
    cur = _get(k)
    nv = (int(cur) + 1) if cur else 1
    _set(k, str(nv), ttl)
    return nv

def _del(k):
    if _redis_client: _redis_client.delete(k)
    else: _local.pop(k, None)

# ── Login attempt tracking (simple, no VPN blocking) ─────────────────
MAX_ATTEMPTS = 5     # Raised from 2 — prevents lockouts for legit users
ATTEMPT_TTL  = 900
LOCKOUT_TTL  = 3600  # 1h instead of 24h
ATTEMPT_PFX  = "attempts:"
LOCKOUT_PFX  = "lockout:"

def get_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    ip  = fwd.split(",")[0].strip() if fwd else (request.remote_addr or "0.0.0.0")
    return ip

def is_locked(ip: str) -> bool:
    return bool(_get(f"{LOCKOUT_PFX}{ip}"))

def lock_ip(ip: str, reason: str):
    _set(f"{LOCKOUT_PFX}{ip}", reason, LOCKOUT_TTL)
    logger.warning(f"IP locked: {ip} reason={reason}")

def record_fail(ip: str) -> int:
    n = _incr(f"{ATTEMPT_PFX}{ip}", ATTEMPT_TTL)
    if n >= MAX_ATTEMPTS:
        lock_ip(ip, f"exceeded_{MAX_ATTEMPTS}_attempts")
    return n

def reset_attempts(ip: str):
    _del(f"{ATTEMPT_PFX}{ip}")
    _del(f"{LOCKOUT_PFX}{ip}")

def safe_url(target: str) -> bool:
    if not target: return False
    ref  = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return (test.scheme in ("http","https")
            and ref.netloc == test.netloc
            and not test.path.startswith("//"))


# ── App factory ───────────────────────────────────────────────────────
def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    IS_PROD = os.environ.get("FLASK_ENV") == "production"

    app.config.update(
        SECRET_KEY            = os.environ["SECRET_KEY"],
        WTF_CSRF_ENABLED      = True,
        WTF_CSRF_SECRET_KEY   = os.environ.get("WTF_CSRF_SECRET_KEY", secrets.token_hex(32)),
        WTF_CSRF_TIME_LIMIT   = 3600,
        SESSION_TYPE          = "filesystem",
        SESSION_FILE_DIR      = SESSION_DIR,
        SESSION_FILE_THRESHOLD= 200,
        SESSION_PERMANENT     = True,
        PERMANENT_SESSION_LIFETIME = timedelta(hours=8),  # Extended from 4h
        SESSION_COOKIE_HTTPONLY   = True,
        SESSION_COOKIE_SAMESITE   = "Lax",   # Relaxed from Strict
        SESSION_COOKIE_SECURE     = IS_PROD,
        SESSION_COOKIE_NAME       = "__Host-session" if IS_PROD else "session",
        MAX_CONTENT_LENGTH        = 1 * 1024 * 1024,  # 1MB
    )
    if _redis_client:
        app.config["SESSION_TYPE"]  = "redis"
        app.config["SESSION_REDIS"] = _redis_client

    Session(app)
    CSRFProtect(app)

    # Relaxed rate limits for small-team/free-tier usage
    limiter = Limiter(
        get_ip, app=app,
        default_limits=["200 per hour", "30 per minute"],
        storage_uri=_REDIS_URI,
        strategy="fixed-window",
    )

    login_mgr = LoginManager(app)
    login_mgr.login_view = "login"
    login_mgr.login_message = "Please sign in to continue."
    login_mgr.login_message_category = "warning"
    login_mgr.session_protection = "basic"  # Relaxed from "strong" (was breaking mobile/VPN users)

    _ADMIN  = os.environ["ADMIN_USERNAME"]
    _AHASH  = generate_password_hash(os.environ["ADMIN_PASSWORD"], method="pbkdf2:sha256:260000")

    class User(UserMixin):
        def __init__(self, uid): self.id = uid

    @login_mgr.user_loader
    def load_user(uid):
        return User(uid) if uid == _ADMIN else None

    NCBI_KEY   = os.environ.get("NCBI_API_KEY","")
    NVIDIA_KEY = os.environ.get("NVIDIA_API_KEY","")
    pf.NCBI_API_KEY = NCBI_KEY
    pf._DELAY       = 0.12 if NCBI_KEY else 0.35
    if NVIDIA_KEY:
        llm.init_client(NVIDIA_KEY)
        logger.info("NVIDIA LLM client initialised")
    else:
        logger.warning("NVIDIA_API_KEY not set — LLM features disabled, will use keyword fallback")

    # ── Nonce injection ──────────────────────────────────────────────
    @app.before_request
    def inject_nonce():
        g.csp_nonce = secrets.token_hex(16)

    @app.context_processor
    def template_globals():
        return {
            "csp_nonce": getattr(g, "csp_nonce", ""),
            "llm_enabled": bool(NVIDIA_KEY),
            "free_tier_limit": FREE_TIER_SOFT_LIMIT,
        }

    # ── Basic security gate (no IP geo-blocking) ─────────────────────
    @app.before_request
    def security_gate():
        ip = get_ip()
        if is_locked(ip):
            # Only block on non-GET pages, allow static assets
            if request.method != "GET" or request.path.startswith("/api/"):
                abort(403)
        # Block obvious scanner tools
        ua = request.headers.get("User-Agent", "")
        bad_ua = re.compile(r"(sqlmap|nikto|masscan|dirbuster|nuclei)", re.IGNORECASE)
        if bad_ua.search(ua):
            abort(403)

    # ── Security headers ──────────────────────────────────────────────
    @app.after_request
    def security_headers(resp):
        nonce = getattr(g, "csp_nonce", "")
        resp.set_cookie("csrf_token", generate_csrf(),
            samesite="Lax", secure=IS_PROD, httponly=False)
        resp.headers["Content-Security-Policy"] = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' https://cdnjs.cloudflare.com; "
            f"style-src 'self' 'nonce-{nonce}' https://fonts.googleapis.com; "
            f"font-src 'self' https://fonts.gstatic.com; "
            f"img-src 'self' data:; "
            f"connect-src 'self'; "
            f"frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none';"
        )
        if IS_PROD:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        resp.headers["X-Frame-Options"]       = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"]        = "no-referrer"
        resp.headers.pop("Server", None)
        if request.path.startswith("/api/") or request.path in ("/login","/logout"):
            resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp

    # ── Helpers ───────────────────────────────────────────────────────
    def save_records(k, d): session[k] = d; session.modified = True
    def load_records(k):    return session.get(k)
    def safe_err(msg, exc=None):
        if exc: logger.exception(msg)
        return {"error": msg}

    def set_progress(step: str, pct: int, detail: str = ""):
        session["_progress"] = {"step": step, "pct": pct, "detail": detail, "ts": time.time()}
        session.modified = True

    def clear_progress():
        session.pop("_progress", None); session.modified = True

    # ── AUTH ──────────────────────────────────────────────────────────
    @app.route("/")
    def index():
        return redirect(url_for("dashboard") if current_user.is_authenticated else url_for("login"))

    @app.route("/login", methods=["GET","POST"])
    @limiter.limit("10 per minute; 30 per hour")
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        ip = get_ip()
        error = None

        if request.method == "POST":
            if request.form.get("_email_confirm", ""):
                abort(403)

            username = request.form.get("username","").strip()
            password = request.form.get("password","")

            user_ok = secrets.compare_digest(username.encode(), _ADMIN.encode())
            pass_ok = check_password_hash(_AHASH, password)

            if user_ok and pass_ok:
                session.clear()
                login_user(User(username), remember=False)
                session.permanent = True
                reset_attempts(ip)
                nxt = request.args.get("next","")
                return redirect(url_for("dashboard") if not safe_url(nxt) else nxt)

            n = record_fail(ip)
            rem = max(0, MAX_ATTEMPTS - n)
            time.sleep(1)
            error = (f"Account locked for 1 hour due to too many failed attempts."
                     if rem == 0 else
                     f"Invalid credentials. {rem} attempt(s) remaining.")

        return render_template("login.html", error=error)

    @app.route("/logout")
    @login_required
    def logout():
        logout_user(); session.clear()
        return redirect(url_for("login"))

    @app.route("/admin/unlock/<ip_addr>", methods=["POST"])
    @login_required
    def admin_unlock(ip_addr):
        tok = (request.get_json(silent=True) or {}).get("token","")
        adm = os.environ.get("ADMIN_UNLOCK_TOKEN","")
        if not adm or not secrets.compare_digest(tok, adm): abort(403)
        for pfx in (LOCKOUT_PFX, ATTEMPT_PFX):
            _del(f"{pfx}{ip_addr}")
        return jsonify({"unlocked": ip_addr})

    # ── Pages ─────────────────────────────────────────────────────────
    @app.route("/dashboard")
    @login_required
    def dashboard():
        return render_template("dashboard.html")

    @app.route("/evidence")
    @login_required
    def evidence():
        return render_template("evidence.html")

    @app.route("/review")
    @login_required
    def review():
        return render_template("review.html")

    @app.route("/clinicaltrials")
    @login_required
    def clinicaltrials():
        return render_template("clinicaltrials.html")

    # ── Progress polling endpoint ─────────────────────────────────────
    @app.route("/api/status")
    @login_required
    def api_status():
        prog = session.get("_progress", {})
        return jsonify({
            "step":   prog.get("step", "idle"),
            "pct":    prog.get("pct", 0),
            "detail": prog.get("detail", ""),
            "llm_enabled": bool(NVIDIA_KEY),
            "free_tier_limit": FREE_TIER_SOFT_LIMIT,
        })

    # ── Health check (no auth — for Railway) ─────────────────────────
    @app.route("/health")
    def health():
        return jsonify({
            "status": "ok",
            "llm": bool(NVIDIA_KEY),
            "ncbi": bool(NCBI_KEY),
            "ts": datetime.utcnow().isoformat()
        }), 200

    # ── Evidence search ───────────────────────────────────────────────
    @app.route("/api/evidence/search", methods=["POST"])
    @login_required
    @limiter.limit("20 per minute; 200 per hour")
    def api_evidence_search():
        d        = request.get_json(silent=True) or {}
        question = (d.get("question") or "").strip()[:500]
        top_n    = min(int(d.get("top_n", 15)), FREE_TIER_HARD_LIMIT)
        min_year = d.get("min_year")
        if not question: return jsonify({"error": "Question required"}), 400

        try:
            # ── Stage 1: Smart query decomposition ────────────────
            set_progress("refining", 8, "Decomposing question into targeted queries…")
            refined = {"pubmed_query": question, "primary_query": question,
                       "population_query": question, "outcomes_query": question,
                       "clinical_context": "", "therapeutic_area": "",
                       "key_drugs": [], "key_outcomes": [], "study_types": []}
            if NVIDIA_KEY:
                try:
                    refined = llm.refine_query(question)
                except Exception as e:
                    logger.warning(f"Query decomposition failed, using raw question: {e}")

            # ── Stage 2: Multi-query PubMed fetch + deduplication ──
            # Use up to 3 targeted sub-queries; merge + dedup by PMID
            queries = []
            if NVIDIA_KEY:
                for qkey in ("primary_query", "population_query", "outcomes_query"):
                    q = refined.get(qkey, "").strip()
                    if q and q not in queries:
                        queries.append(q)
            if not queries:
                queries = [question]

            all_pmids   = []
            seen_pmids  = set()
            first_search = None

            for qi, q in enumerate(queries):
                pct = 15 + qi * 12
                set_progress("pubmed", pct, f"PubMed search {qi+1}/{len(queries)}: {q[:50]}…")
                try:
                    s = pf.esearch(q, max_results=60, sort="relevance")
                    if first_search is None:
                        first_search = s
                    for pmid in s["pmids"]:
                        if pmid not in seen_pmids:
                            seen_pmids.add(pmid)
                            all_pmids.append(pmid)
                except Exception as e:
                    logger.warning(f"PubMed query {qi+1} failed: {e}")

            if not all_pmids:
                return jsonify({"error": "PubMed search returned no results. Try a different question or check your NCBI_API_KEY."}), 500

            # Cap fetch pool — more candidates → better filtering
            fetch_limit = min(len(all_pmids), 120)
            set_progress("pubmed", 42, f"Fetching {fetch_limit} unique records…")
            try:
                records = pf.efetch_full(all_pmids[:fetch_limit])
            except Exception as e:
                logger.error(f"PubMed efetch failed: {e}")
                return jsonify({"error": f"PubMed fetch failed: {str(e)[:200]}"}), 500

            set_progress("enriching", 58, f"Retrieved {len(records)} records…")

            if d.get("enrich_citations"):
                try:
                    records = enrich_with_citations(records)
                except Exception as e:
                    logger.warning(f"Citation enrichment failed (non-fatal): {e}")

            # ── Stage 3: Abstract-aware relevance scoring + cutoff ─
            set_progress("ranking", 72, f"Scoring relevance of {len(records)} papers…")

            if d.get("use_llm_rank", True) and NVIDIA_KEY:
                try:
                    top = llm.rank_articles(
                        records, question,
                        top_n=top_n,
                        min_year=int(min_year) if min_year else None,
                        refined=refined,
                    )
                except Exception as e:
                    logger.warning(f"LLM ranking failed, falling back to recency: {e}")
                    top = sorted(records, key=lambda x: int(x.get("year",0) or 0), reverse=True)[:top_n]
            else:
                top = sorted(records, key=lambda x: int(x.get("year",0) or 0), reverse=True)[:top_n]

            clear_progress()

            warning = None
            if not top:
                warning = "All retrieved papers were filtered as irrelevant to your question. Try broadening your query."
            elif top_n >= FREE_TIER_HARD_LIMIT:
                warning = f"Results capped at {FREE_TIER_HARD_LIMIT} (free-tier limit)."

            search_ref = first_search or {"total_count": len(all_pmids), "query_translation": queries[0]}

            return jsonify({
                "articles": [{"rank": i,
                    "pmid":         r.get("pmid",""),
                    "title":        r.get("title",""),
                    "authors":      r.get("first_author",""),
                    "year":         r.get("year",""),
                    "journal":      r.get("journal_full",""),
                    "pub_types":    r.get("pub_types_str",""),
                    "citations":    r.get("citation_count",0),
                    "inf_citations":r.get("influential_citations",0),
                    "impact_est":   r.get("impact_factor_est",0.0),
                    "abstract":     r.get("abstract","")[:600],
                    "pmid_url":     r.get("url_pubmed",""),
                    "doi":          r.get("doi",""),
                    "country":      r.get("country",""),
                    "relevance_score": r.get("_relevance_score", None),
                } for i, r in enumerate(top, 1)],
                "total_pubmed":      search_ref["total_count"],
                "query_translation": search_ref.get("query_translation", queries[0]),
                "queries_used":      queries,
                "candidates_fetched": len(records),
                "clinical_context":  refined.get("clinical_context",""),
                "therapeutic_area":  refined.get("therapeutic_area",""),
                "key_drugs":         refined.get("key_drugs",[]),
                "key_outcomes":      refined.get("key_outcomes",[]),
                "llm_used":          bool(NVIDIA_KEY),
                "warning":           warning,
            })
        except Exception as e:
            clear_progress()
            return jsonify(safe_err("Search failed. Please try again.", e)), 500

    # ── Bulk Review ───────────────────────────────────────────────────
    @app.route("/api/review/search", methods=["POST"])
    @login_required
    @limiter.limit("10 per minute; 100 per hour")
    def api_review_search():
        d        = request.get_json(silent=True) or {}
        question = (d.get("question") or "").strip()[:500]
        max_r    = min(int(d.get("max_results", 30)), FREE_TIER_HARD_LIMIT)
        min_year = d.get("min_year")
        if not question: return jsonify({"error": "Question required"}), 400

        try:
            set_progress("refining", 8, "Decomposing question into targeted queries…")

            refined = {"pubmed_query": question, "primary_query": question,
                       "population_query": question, "outcomes_query": question,
                       "clinical_context": "", "therapeutic_area": "",
                       "key_drugs": [], "key_outcomes": [], "study_types": []}
            if NVIDIA_KEY:
                try:
                    refined = llm.refine_query(question)
                except Exception as e:
                    logger.warning(f"Query decomposition failed: {e}")

            # Multi-query PubMed fetch + dedup
            queries = []
            if NVIDIA_KEY:
                for qkey in ("primary_query", "population_query", "outcomes_query"):
                    q = refined.get(qkey, "").strip()
                    if q and q not in queries:
                        queries.append(q)
            if not queries:
                queries = [question]

            all_pmids  = []
            seen_pmids = set()
            first_search = None
            fetch_per_query = max(20, max_r)

            for qi, q in enumerate(queries):
                set_progress("pubmed", 15 + qi * 10, f"PubMed search {qi+1}/{len(queries)}: {q[:50]}…")
                try:
                    s = pf.esearch(q, max_results=fetch_per_query, sort="relevance")
                    if first_search is None:
                        first_search = s
                    for pmid in s["pmids"]:
                        if pmid not in seen_pmids:
                            seen_pmids.add(pmid)
                            all_pmids.append(pmid)
                except Exception as e:
                    logger.warning(f"Bulk review PubMed query {qi+1} failed: {e}")

            if not all_pmids:
                return jsonify({"error": "PubMed search returned no results. Try a different question."}), 500

            set_progress("pubmed", 42, f"Fetching {min(len(all_pmids), max_r * 2)} records…")
            try:
                records = pf.efetch_full(all_pmids[:max_r * 2])
            except Exception as e:
                clear_progress()
                return jsonify({"error": f"PubMed fetch failed: {str(e)[:200]}"}), 500

            if d.get("enrich_citations"):
                set_progress("enriching", 65, "Fetching citation counts…")
                try:
                    records = enrich_with_citations(records)
                except Exception as e:
                    logger.warning(f"Citation enrichment failed: {e}")

            if min_year:
                records = [r for r in records if int(r.get("year", 0) or 0) >= int(min_year)]

            # Apply relevance filter for bulk review too
            if NVIDIA_KEY and len(records) > max_r:
                set_progress("ranking", 80, f"Filtering {len(records)} records by relevance…")
                try:
                    records = llm.rank_articles(
                        records, question,
                        top_n=max_r,
                        min_year=int(min_year) if min_year else None,
                        refined=refined,
                    )
                except Exception as e:
                    logger.warning(f"Relevance filter failed, returning by recency: {e}")
                    records = sorted(records, key=lambda x: int(x.get("year",0) or 0), reverse=True)[:max_r]
            else:
                records = records[:max_r]

            save_records("review_records", records)
            save_records("extracted_records", [])
            clear_progress()

            rows = [{"pmid": r.get("pmid",""), "title": r.get("title",""),
                "authors": r.get("first_author",""), "year": r.get("year",""),
                "journal": r.get("journal_full",""), "pub_types": r.get("pub_types_str",""),
                "citations": r.get("citation_count",0), "doi": r.get("doi",""),
                "pmid_url": r.get("url_pubmed",""), "country": r.get("country",""),
                "abstract_short": r.get("abstract","")[:250],
                "relevance_score": r.get("_relevance_score", None),
            } for r in records]

            warning = None
            if max_r >= FREE_TIER_HARD_LIMIT:
                warning = f"Results capped at {FREE_TIER_HARD_LIMIT} records (free-tier limit)."

            search_ref = first_search or {"total_count": len(all_pmids), "query_translation": queries[0]}
            return jsonify({
                "rows": rows,
                "total_pubmed": search_ref["total_count"],
                "fetched": len(records),
                "pubmed_query": queries[0],
                "queries_used": queries,
                "warning": warning,
            })
        except Exception as e:
            clear_progress()
            return jsonify(safe_err("Review search failed.", e)), 500

    @app.route("/api/review/extract", methods=["POST"])
    @login_required
    @limiter.limit("5 per minute; 30 per hour")
    def api_review_extract():
        if not NVIDIA_KEY:
            return jsonify({"error": "LLM extraction requires NVIDIA_API_KEY to be configured."}), 503

        d       = request.get_json(silent=True) or {}
        limit   = min(int(d.get("limit", FREE_TIER_EXTRACT_LIMIT)), FREE_TIER_EXTRACT_LIMIT)
        records = load_records("review_records")
        if not records: return jsonify({"error": "No records found. Run a search first."}), 400

        extracted = []
        total = min(len(records), limit)
        for idx, rec in enumerate(records[:limit]):
            set_progress("extracting", int(20 + 70 * idx/max(total,1)),
                         f"Extracting record {idx+1}/{total}: {rec.get('title','')[:50]}…")
            try:
                row = llm.extract_evidence_row(rec)
            except Exception as e:
                logger.warning(f"Extraction failed for {rec.get('pmid','?')}: {e}")
                row = {"Study Title": rec.get("title",""), "PMID": rec.get("pmid",""),
                       "error": "Extraction failed"}
            extracted.append(row)
            time.sleep(0.3)

        save_records("extracted_records", extracted)
        clear_progress()
        return jsonify({
            "count": len(extracted),
            "rows": extracted[:3],
            "warning": f"Extraction capped at {FREE_TIER_EXTRACT_LIMIT} records on free tier." if limit >= FREE_TIER_EXTRACT_LIMIT else None,
        })

    @app.route("/api/review/download")
    @login_required
    @limiter.limit("10 per hour")
    def api_review_download():
        records = load_records("review_records")
        if not records: abort(404)
        extracted = load_records("extracted_records") or []
        xlsx = pf.to_excel_bytes(records, extracted_rows=extracted or None, include_abstract=True)
        return send_file(io.BytesIO(xlsx),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"msl_literature_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.xlsx")

    @app.route("/api/review/download_extracted")
    @login_required
    @limiter.limit("10 per hour")
    def api_review_download_extracted():
        records   = load_records("review_records")
        extracted = load_records("extracted_records")
        if not records: abort(404)
        xlsx = pf.to_excel_bytes(records, extracted_rows=extracted, include_abstract=True)
        return send_file(io.BytesIO(xlsx),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"msl_extracted_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.xlsx")

    # ── Clinical Trials ───────────────────────────────────────────────
    @app.route("/api/ct/search", methods=["POST"])
    @login_required
    @limiter.limit("20 per minute; 200 per hour")
    def api_ct_search():
        d        = request.get_json(silent=True) or {}
        question = (d.get("question") or "").strip()[:500]
        max_r    = min(int(d.get("max_results", 30)), FREE_TIER_HARD_LIMIT)
        status_f = d.get("status_filter","")
        if not question: return jsonify({"error": "Question required"}), 400

        try:
            set_progress("refining", 15, "Building clinical trials query…")

            if NVIDIA_KEY:
                try:
                    refined  = llm.refine_query(question)
                    ct_query = refined.get("general_query", question)
                    ctx      = refined.get("clinical_context","")
                except Exception as e:
                    logger.warning(f"Query refinement failed: {e}")
                    ct_query = question
                    ctx      = ""
            else:
                ct_query = question
                ctx      = ""

            set_progress("ct_fetch", 40, f"Searching ClinicalTrials.gov for: {ct_query[:60]}…")

            try:
                studies = llm.search_clinical_trials(ct_query, max_results=max_r)
            except Exception as e:
                clear_progress()
                return jsonify({"error": f"ClinicalTrials.gov search failed: {str(e)[:200]}"}), 500

            if status_f:
                studies = [s for s in studies if status_f.upper() in s.get("status","").upper()]

            save_records("ct_records", studies)
            clear_progress()

            warning = None
            if max_r >= FREE_TIER_HARD_LIMIT:
                warning = f"Results capped at {FREE_TIER_HARD_LIMIT} trials on free tier."

            return jsonify({
                "studies": studies, "ct_query": ct_query,
                "clinical_context": ctx, "total": len(studies),
                "warning": warning,
            })
        except Exception as e:
            clear_progress()
            return jsonify(safe_err("CT search failed.", e)), 500

    @app.route("/api/ct/download")
    @login_required
    @limiter.limit("10 per hour")
    def api_ct_download():
        studies = load_records("ct_records")
        if not studies: abort(404)
        try:
            xlsx = pf.ct_to_excel_bytes(studies)
            return send_file(io.BytesIO(xlsx),
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name=f"clinical_trials_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.xlsx")
        except Exception as e:
            return jsonify(safe_err("Excel generation failed.", e)), 500

    # ── Chat ──────────────────────────────────────────────────────────
    @app.route("/api/chat", methods=["POST"])
    @login_required
    @limiter.limit("30 per minute; 300 per hour")
    def api_chat():
        if not NVIDIA_KEY:
            return jsonify({"error": "AI chat requires NVIDIA_API_KEY to be configured."}), 503
        d        = request.get_json(silent=True) or {}
        question = (d.get("question") or "").strip()[:800]
        history  = d.get("history",[])[-8:]
        records  = load_records("review_records") or load_records("ct_records")
        if not records: return jsonify({"error": "No records loaded. Run a search first."}), 400
        try:
            return jsonify({"answer": llm.chatbot_answer(question, records[:20], history)})
        except Exception as e:
            return jsonify(safe_err("Chat failed. Please try again.", e)), 500

    # ── Error handlers ────────────────────────────────────────────────
    for code, msg in [(400,"Bad request"), (403,"Access denied"), (404,"Page not found"),
                      (405,"Method not allowed"), (413,"Request too large"), (500,"Internal server error")]:
        app.register_error_handler(code,
            lambda e, c=code, m=msg: (render_template("error.html", code=c, msg=m), c))

    @app.errorhandler(429)
    def rate_limited(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Too many requests. Please wait a moment and try again."}), 429
        return render_template("error.html", code=429, msg="Too many requests. Please wait a moment."), 429

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
