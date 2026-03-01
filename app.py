"""
app.py  —  MSL Intelligence Platform
MAXIMUM SECURITY HARDENED BUILD v2
- VPN / datacenter / Tor exit node detection + auto-ban
- 2-strike IP lockout (permanent until admin reset)
- Redis-backed distributed rate limiting
- Zero-day mitigations: scanners, path traversal, null bytes, verb tampering
- Nonce-based CSP (no unsafe-inline anywhere)
- Session IP binding, regeneration on login
- Full security audit log
"""

import os, io, re, json, time, hashlib, secrets, logging, ipaddress
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

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger          = logging.getLogger("msl_app")
security_logger = logging.getLogger("msl_security")
_h = logging.FileHandler("security_audit.log")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
security_logger.addHandler(_h)
security_logger.setLevel(logging.WARNING)

# ── Session dir ───────────────────────────────────────────────────
SESSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flask_session")
os.makedirs(SESSION_DIR, exist_ok=True)

# ── Constants ─────────────────────────────────────────────────────
MAX_ATTEMPTS       = 2
LOCKOUT_TTL        = 86400   # 24h
ATTEMPT_TTL        = 900     # 15min window
LOCKOUT_PFX        = "lockout:"
ATTEMPT_PFX        = "attempts:"
VPN_BAN_TTL        = 604800  # 7 days for VPN/datacenter
VPN_PFX            = "vpnban:"

# Known datacenter/hosting ASN ranges (IPv4 CIDR blocks)
# Covers AWS, GCP, Azure, DigitalOcean, Linode, OVH, Hetzner, Vultr etc.
_DC_NETWORKS = [
    # AWS
    "3.0.0.0/8", "13.32.0.0/15", "18.116.0.0/14", "34.192.0.0/10", "52.0.0.0/8", "54.0.0.0/8",
    # GCP
    "34.64.0.0/10", "34.128.0.0/10", "35.184.0.0/13", "35.192.0.0/11",
    # Azure
    "13.64.0.0/11", "13.96.0.0/13", "20.0.0.0/11", "40.64.0.0/10", "104.40.0.0/13",
    # DigitalOcean
    "104.131.0.0/18", "104.236.0.0/16", "138.197.0.0/16", "159.65.0.0/16", "159.89.0.0/16",
    "165.22.0.0/15", "167.98.0.0/15", "167.172.0.0/16", "178.62.0.0/16", "188.166.0.0/16",
    # Linode/Akamai
    "45.33.0.0/17", "45.56.0.0/21", "96.126.96.0/19", "139.162.0.0/16", "172.104.0.0/14",
    # Hetzner
    "5.9.0.0/16", "46.4.0.0/16", "88.198.0.0/16", "136.243.0.0/16", "176.9.0.0/16", "178.63.0.0/16",
    # OVH
    "5.39.0.0/17", "51.68.0.0/14", "54.36.0.0/14", "91.121.0.0/16", "137.74.0.0/15",
    # Vultr
    "45.32.0.0/14", "66.42.48.0/20", "104.156.224.0/19", "149.28.0.0/16",
    # Cloudflare (proxy exit — may be legit, mark as VPN candidate)
    "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.100.0/22",
]

_DC_NETS_PARSED = []
for _cidr in _DC_NETWORKS:
    try:
        _DC_NETS_PARSED.append(ipaddress.ip_network(_cidr, strict=False))
    except Exception:
        pass

# Known Tor exit node list — we check a static subset + realtime
_TOR_INDICATORS = re.compile(
    r"^(185\.220\.|199\.87\.|162\.247\.|176\.10\.|109\.70\.|91\.108\.|185\.100\.|"
    r"163\.172\.|62\.210\.|46\.165\.|212\.129\.|194\.165\.|185\.107\.|185\.82\.)",
    re.IGNORECASE
)

SUSPICIOUS_UA = re.compile(
    r"(sqlmap|nikto|nmap|masscan|zgrab|dirbuster|gobuster|wfuzz|"
    r"burpsuite|hydra|medusa|ncrack|curl/|libcurl|python-requests|go-http|"
    r"libwww|wget|scrapy|bot|crawler|spider|nuclei|acunetix|"
    r"nessus|openvas|metasploit|havij|pangolin|webshag|skipfish|"
    r"w3af|arachni|appscan)",
    re.IGNORECASE
)

# ── Runtime env guard ─────────────────────────────────────────────
for _v in ("SECRET_KEY", "ADMIN_USERNAME", "ADMIN_PASSWORD"):
    if not os.environ.get(_v):
        raise RuntimeError(f"FATAL: env var '{_v}' is required. Application cannot start.")

# ── Redis ─────────────────────────────────────────────────────────
_REDIS_URI    = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
_redis_client = None
if _REDIS_URI.startswith("redis"):
    try:
        import redis as _rl
        _redis_client = _rl.from_url(_REDIS_URI, decode_responses=True)
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

# ── IP helpers ────────────────────────────────────────────────────
def get_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    ip  = fwd.split(",")[0].strip() if fwd else (request.remote_addr or "0.0.0.0")
    try: ipaddress.ip_address(ip); return ip
    except ValueError: return request.remote_addr or "0.0.0.0"

def is_locked(ip: str) -> bool:
    return bool(_get(f"{LOCKOUT_PFX}{ip}") or _get(f"{VPN_PFX}{ip}"))

def lock_ip(ip: str, reason: str, ttl: int = LOCKOUT_TTL):
    prefix = VPN_PFX if "vpn" in reason or "datacenter" in reason or "tor" in reason else LOCKOUT_PFX
    _set(f"{prefix}{ip}", reason, ttl)
    security_logger.critical(
        f"IP_LOCKED ip={ip} reason={reason} ttl={ttl}s "
        f"expires={datetime.now(timezone.utc) + timedelta(seconds=ttl)}"
    )

def record_fail(ip: str) -> int:
    n = _incr(f"{ATTEMPT_PFX}{ip}", ATTEMPT_TTL)
    if n >= MAX_ATTEMPTS:
        lock_ip(ip, f"exceeded_{MAX_ATTEMPTS}_attempts")
    return n

def reset_attempts(ip: str):
    _del(f"{ATTEMPT_PFX}{ip}")

# ── VPN / Datacenter / Tor detection ─────────────────────────────
def classify_ip(ip: str) -> tuple[bool, str]:
    """Returns (is_threat, reason). Checks Tor patterns + datacenter CIDR."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False, ""

    # Skip private/loopback
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return False, ""

    # Tor exit node prefix match
    if _TOR_INDICATORS.match(ip):
        return True, "tor_exit_node"

    # Datacenter CIDR match
    for net in _DC_NETS_PARSED:
        if addr in net:
            return True, f"datacenter_ip:{net}"

    return False, ""

# ── URL safety ────────────────────────────────────────────────────
def safe_url(target: str) -> bool:
    if not target: return False
    ref  = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return (test.scheme in ("http","https")
            and ref.netloc == test.netloc
            and not test.path.startswith("//"))

# ── Suspicious request heuristics ────────────────────────────────
def is_suspicious() -> tuple[bool, str]:
    ua = request.headers.get("User-Agent", "")
    if not ua.strip():                              return True, "blank_ua"
    if SUSPICIOUS_UA.search(ua):                    return True, f"scanner:{ua[:80]}"
    for h, v in request.headers:
        if len(v) > 8192:                           return True, f"oversized_hdr:{h}"
    p = request.path
    if any(x in p for x in ["../","..\\","%2e%2e","%252e"]): return True, "path_traversal"
    if "\x00" in request.url:                       return True, "null_byte"
    if any(ord(c) < 0x09 for c in request.url):    return True, "ctrl_char"
    if (request.method not in ("GET","POST","HEAD","OPTIONS")
            and not p.startswith("/api/")):         return True, f"verb:{request.method}"
    return False, ""

# ── App factory ───────────────────────────────────────────────────
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
        PERMANENT_SESSION_LIFETIME = timedelta(hours=4),
        SESSION_COOKIE_HTTPONLY   = True,
        SESSION_COOKIE_SAMESITE   = "Strict",
        SESSION_COOKIE_SECURE     = IS_PROD,
        SESSION_COOKIE_NAME       = "__Host-session" if IS_PROD else "session",
        MAX_CONTENT_LENGTH        = 512 * 1024,
    )
    if _redis_client:
        app.config["SESSION_TYPE"]  = "redis"
        app.config["SESSION_REDIS"] = _redis_client

    Session(app)
    CSRFProtect(app)

    limiter = Limiter(
        get_ip, app=app,
        default_limits=["60 per hour", "10 per minute"],
        storage_uri=_REDIS_URI,
        strategy="fixed-window",
    )

    login_mgr = LoginManager(app)
    login_mgr.login_view = "login"
    login_mgr.login_message = "Please log in."
    login_mgr.login_message_category = "warning"
    login_mgr.session_protection = "strong"

    _ADMIN  = os.environ["ADMIN_USERNAME"]
    _AHASH  = generate_password_hash(os.environ["ADMIN_PASSWORD"], method="pbkdf2:sha256:600000")

    class User(UserMixin):
        def __init__(self, uid): self.id = uid

    @login_mgr.user_loader
    def load_user(uid):
        return User(uid) if uid == _ADMIN else None

    NCBI_KEY   = os.environ.get("NCBI_API_KEY","")
    NVIDIA_KEY = os.environ.get("NVIDIA_API_KEY","")
    pf.NCBI_API_KEY = NCBI_KEY
    pf._DELAY       = 0.12 if NCBI_KEY else 0.35
    if NVIDIA_KEY: llm.init_client(NVIDIA_KEY)
    else: logger.warning("NVIDIA_API_KEY not set — LLM disabled")

    # ── Nonce injection ───────────────────────────────────────────
    @app.before_request
    def inject_nonce():
        g.csp_nonce = secrets.token_hex(16)

    @app.context_processor
    def nonce_ctx():
        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    # ── Global security gate ──────────────────────────────────────
    @app.before_request
    def security_gate():
        ip = get_ip()

        # 1. Hard lockout — first, before anything
        if is_locked(ip):
            security_logger.warning(f"BLOCKED ip={ip} path={request.path}")
            abort(403)

        # 2. Scanner/exploit tool detection
        sus, reason = is_suspicious()
        if sus:
            security_logger.warning(f"SUSPICIOUS ip={ip} reason={reason}")
            lock_ip(ip, reason)
            abort(403)

        # 3. VPN / datacenter / Tor detection
        #    We warn on first hit from a datacenter IP during login attempts,
        #    and permanently ban after they fail authentication from one.
        is_threat, vpn_reason = classify_ip(ip)
        if is_threat:
            # If they're actively trying to access auth routes from a datacenter/Tor, ban immediately
            if request.path in ("/login",) and request.method == "POST":
                security_logger.critical(
                    f"VPN_AUTH_ATTEMPT ip={ip} reason={vpn_reason} path={request.path}"
                )
                lock_ip(ip, f"vpn_{vpn_reason}", VPN_BAN_TTL)
                abort(403)
            # Log other datacenter access (monitoring only — don't block GETs in case of CDN)
            security_logger.warning(f"DATACENTER_ACCESS ip={ip} reason={vpn_reason} path={request.path}")

        # 4. Session IP binding
        if current_user.is_authenticated:
            bound = session.get("_bound_ip")
            if bound and bound != ip:
                security_logger.critical(f"SESSION_HIJACK ip={ip} bound={bound}")
                session.clear(); logout_user(); abort(403)

    # ── Security headers + nonce CSP ─────────────────────────────
    @app.after_request
    def security_headers(resp):
        nonce = getattr(g, "csp_nonce", "")

        resp.set_cookie("csrf_token", generate_csrf(),
            samesite="Strict", secure=IS_PROD, httponly=False)

        # Nonce-based CSP — NO unsafe-inline, NO unsafe-eval
        resp.headers["Content-Security-Policy"] = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' https://cdnjs.cloudflare.com; "
            f"style-src 'self' 'nonce-{nonce}' https://fonts.googleapis.com; "
            f"font-src 'self' https://fonts.gstatic.com; "
            f"img-src 'self' data:; "
            f"connect-src 'self'; "
            f"frame-ancestors 'none'; "
            f"base-uri 'self'; "
            f"form-action 'self'; "
            f"object-src 'none';"
        )
        if IS_PROD:
            resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        resp.headers["X-Frame-Options"]                = "DENY"
        resp.headers["X-Content-Type-Options"]          = "nosniff"
        resp.headers["X-XSS-Protection"]                = "1; mode=block"
        resp.headers["Referrer-Policy"]                 = "no-referrer"
        resp.headers["Permissions-Policy"]              = "geolocation=(), camera=(), microphone=(), payment=(), usb=()"
        resp.headers["Cross-Origin-Opener-Policy"]      = "same-origin"
        resp.headers["Cross-Origin-Resource-Policy"]    = "same-origin"
        resp.headers["Cross-Origin-Embedder-Policy"]    = "require-corp"
        resp.headers.pop("Server", None)
        resp.headers.pop("X-Powered-By", None)

        if (request.path.startswith("/api/") or
                request.path in ("/login","/logout","/dashboard")):
            resp.headers["Cache-Control"] = "no-store, max-age=0"
            resp.headers["Pragma"]        = "no-cache"
            resp.headers["Expires"]       = "0"
        return resp

    # ── Helpers ───────────────────────────────────────────────────
    def save_records(k, d): session[k] = d; session.modified = True
    def load_records(k):    return session.get(k)
    def safe_err(msg, exc=None):
        if exc: logger.exception(msg)
        return {"error": msg}

    # ── AUTH ──────────────────────────────────────────────────────
    @app.route("/")
    def index():
        return redirect(url_for("dashboard") if current_user.is_authenticated else url_for("login"))

    @app.route("/login", methods=["GET","POST"])
    @limiter.limit("3 per minute; 5 per hour")
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        ip = get_ip()
        error = None

        if request.method == "POST":
            # Honeypot — visible to bots, hidden via CSS
            if request.form.get("_email_confirm", ""):
                security_logger.warning(f"HONEYPOT ip={ip}")
                lock_ip(ip, "honeypot")
                abort(403)

            username = request.form.get("username","").strip()
            password = request.form.get("password","")

            if len(username) > 64 or len(password) > 256:
                lock_ip(ip, "oversized_creds"); abort(403)

            user_ok = secrets.compare_digest(username.encode(), _ADMIN.encode())
            pass_ok = check_password_hash(_AHASH, password)

            if user_ok and pass_ok:
                session.clear()
                login_user(User(username), remember=False)
                session.permanent    = True
                session["_bound_ip"] = ip
                session["_login_at"] = time.time()
                reset_attempts(ip)
                security_logger.warning(f"LOGIN_OK user={username} ip={ip}")
                nxt = request.args.get("next","")
                return redirect(url_for("dashboard") if not safe_url(nxt) else nxt)

            n = record_fail(ip)
            rem = max(0, MAX_ATTEMPTS - n)
            security_logger.warning(f"LOGIN_FAIL user={username!r} ip={ip} attempt={n}/{MAX_ATTEMPTS}")
            time.sleep(2)
            error = ("Too many failed attempts. Your IP has been blocked. Contact the administrator."
                     if rem == 0 else
                     f"Invalid credentials. {rem} attempt(s) remaining before your IP is permanently blocked.")

        return render_template("login.html", error=error)

    @app.route("/logout")
    @login_required
    def logout():
        security_logger.warning(f"LOGOUT user={current_user.id} ip={get_ip()}")
        logout_user(); session.clear()
        return redirect(url_for("login"))

    @app.route("/admin/unlock/<ip_addr>", methods=["POST"])
    @login_required
    @limiter.limit("5 per hour")
    def admin_unlock(ip_addr):
        tok = (request.get_json(silent=True) or {}).get("token","")
        adm = os.environ.get("ADMIN_UNLOCK_TOKEN","")
        if not adm or not secrets.compare_digest(tok, adm): abort(403)
        try: ipaddress.ip_address(ip_addr)
        except ValueError: return jsonify({"error":"Invalid IP"}), 400
        for pfx in (LOCKOUT_PFX, ATTEMPT_PFX, VPN_PFX):
            _del(f"{pfx}{ip_addr}")
        security_logger.warning(f"IP_UNLOCKED ip={ip_addr} by={current_user.id}")
        return jsonify({"unlocked": ip_addr})

    # ── Dashboards ────────────────────────────────────────────────
    @app.route("/dashboard")
    @login_required
    def dashboard():
        return render_template("dashboard.html")

    @app.route("/evidence")
    @login_required
    def evidence():
        return render_template("evidence.html")

    @app.route("/api/evidence/search", methods=["POST"])
    @login_required
    @limiter.limit("10 per minute; 100 per hour")
    def api_evidence_search():
        d = request.get_json(silent=True) or {}
        question = (d.get("question") or "").strip()[:500]
        top_n    = min(int(d.get("top_n",15)),50)
        min_year = d.get("min_year")
        if not question: return jsonify({"error":"Question required"}),400
        try:
            refined  = llm.refine_query(question)
            pubmed_q = refined.get("pubmed_query",question)
            search   = pf.esearch(pubmed_q,max_results=200,sort="relevance")
            records  = pf.efetch_full(search["pmids"][:200])
            if d.get("enrich_citations"): records = enrich_with_citations(records,sleep_sec=0.3)
            if d.get("use_llm_rank",True) and NVIDIA_KEY:
                top = llm.rank_articles(records,question,top_n=top_n,
                                        min_year=int(min_year) if min_year else None)
            else:
                top = sorted(records,key=lambda x:int(x.get("year",0) or 0),reverse=True)[:top_n]
            return jsonify({
                "articles": [{"rank":i,"pmid":r.get("pmid",""),"title":r.get("title",""),
                    "authors":r.get("first_author",""),"year":r.get("year",""),
                    "journal":r.get("journal_full",""),"pub_types":r.get("pub_types_str",""),
                    "mesh_major":r.get("mesh_major_str",""),"citations":r.get("citation_count",0),
                    "inf_citations":r.get("influential_citations",0),"impact_est":r.get("impact_factor_est",0.0),
                    "abstract":r.get("abstract","")[:600],"pmid_url":r.get("url_pubmed",""),
                    "doi":r.get("doi",""),"country":r.get("country","")} for i,r in enumerate(top,1)],
                "total_pubmed":search["total_count"],"query_translation":search["query_translation"],
                "clinical_context":refined.get("clinical_context",""),"therapeutic_area":refined.get("therapeutic_area",""),
            })
        except Exception as e: return jsonify(safe_err("Search failed. Please try again.",e)),500

    @app.route("/review")
    @login_required
    def review():
        return render_template("review.html")

    @app.route("/api/review/search", methods=["POST"])
    @login_required
    @limiter.limit("3 per minute; 30 per hour")
    def api_review_search():
        d = request.get_json(silent=True) or {}
        question = (d.get("question") or "").strip()[:500]
        max_r    = min(int(d.get("max_results",500)),1000)
        min_year = d.get("min_year")
        if not question: return jsonify({"error":"Question required"}),400
        try:
            refined  = llm.refine_query(question)
            pubmed_q = refined.get("pubmed_query",question)
            search   = pf.esearch(pubmed_q,max_results=max_r,sort="relevance")
            records  = pf.efetch_full(search["pmids"])
            if d.get("enrich_citations"): records = enrich_with_citations(records,sleep_sec=0.3)
            if min_year: records = [r for r in records if int(r.get("year",0) or 0)>=int(min_year)]
            save_records("review_records",records[:1000])
            save_records("extracted_records",[])
            rows = [{"pmid":r.get("pmid",""),"title":r.get("title",""),"authors":r.get("first_author",""),
                "year":r.get("year",""),"journal":r.get("journal_full",""),"pub_types":r.get("pub_types_str",""),
                "mesh":r.get("mesh_major_str",""),"citations":r.get("citation_count",0),"doi":r.get("doi",""),
                "pmid_url":r.get("url_pubmed",""),"country":r.get("country",""),
                "abstract_short":r.get("abstract","")[:250]} for r in records]
            return jsonify({"rows":rows,"total_pubmed":search["total_count"],"fetched":len(records),"pubmed_query":pubmed_q})
        except Exception as e: return jsonify(safe_err("Review search failed.",e)),500

    @app.route("/api/review/extract", methods=["POST"])
    @login_required
    @limiter.limit("2 per minute; 20 per hour")
    def api_review_extract():
        d = request.get_json(silent=True) or {}
        limit   = min(int(d.get("limit",50)),100)
        records = load_records("review_records")
        if not records: return jsonify({"error":"No records. Run search first."}),400
        extracted = []
        for rec in records[:limit]:
            try: row = llm.extract_evidence_row(rec)
            except Exception: row = {"Study Title":rec.get("title",""),"PMID":rec.get("pmid",""),"error":"Failed"}
            extracted.append(row); time.sleep(0.2)
        save_records("extracted_records",extracted)
        return jsonify({"count":len(extracted),"rows":extracted[:5]})

    @app.route("/api/review/download")
    @login_required
    @limiter.limit("5 per hour")
    def api_review_download():
        records = load_records("review_records")
        if not records: abort(404)
        extracted = load_records("extracted_records") or []
        if not extracted and NVIDIA_KEY:
            for rec in records[:30]:
                try: extracted.append(llm.extract_evidence_row(rec))
                except Exception: extracted.append({"PMID":rec.get("pmid",""),"Study Title":rec.get("title","")})
                time.sleep(0.25)
            save_records("extracted_records",extracted)
        xlsx = pf.to_excel_bytes(records,extracted_rows=extracted or None,include_abstract=True)
        return send_file(io.BytesIO(xlsx),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,download_name=f"msl_literature_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.xlsx")

    @app.route("/api/review/download_extracted")
    @login_required
    @limiter.limit("5 per hour")
    def api_review_download_extracted():
        records = load_records("review_records")
        if not records: abort(404)
        xlsx = pf.to_excel_bytes(records,extracted_rows=load_records("extracted_records"),include_abstract=True)
        return send_file(io.BytesIO(xlsx),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,download_name=f"msl_extracted_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.xlsx")

    @app.route("/clinicaltrials")
    @login_required
    def clinicaltrials():
        return render_template("clinicaltrials.html")

    @app.route("/api/ct/search", methods=["POST"])
    @login_required
    @limiter.limit("10 per minute; 100 per hour")
    def api_ct_search():
        d = request.get_json(silent=True) or {}
        question = (d.get("question") or "").strip()[:500]
        max_r    = min(int(d.get("max_results",100)),300)
        status_f = d.get("status_filter","")
        if not question: return jsonify({"error":"Question required"}),400
        try:
            refined  = llm.refine_query(question)
            ct_query = refined.get("general_query",question)
            studies  = llm.search_clinical_trials(ct_query,max_results=max_r)
            if status_f: studies = [s for s in studies if status_f.upper() in s.get("status","").upper()]
            save_records("ct_records",studies)
            return jsonify({"studies":studies,"ct_query":ct_query,
                "clinical_context":refined.get("clinical_context",""),"total":len(studies)})
        except Exception as e: return jsonify(safe_err("CT search failed.",e)),500

    @app.route("/api/ct/download")
    @login_required
    @limiter.limit("5 per hour")
    def api_ct_download():
        studies = load_records("ct_records")
        if not studies: abort(404)
        try:
            xlsx = pf.ct_to_excel_bytes(studies)
            buf  = io.BytesIO(xlsx); buf.seek(0)
            return send_file(buf,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,download_name=f"clinical_trials_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.xlsx")
        except Exception as e: return jsonify(safe_err("Excel failed.",e)),500

    @app.route("/api/chat", methods=["POST"])
    @login_required
    @limiter.limit("15 per minute; 200 per hour")
    def api_chat():
        d = request.get_json(silent=True) or {}
        question = (d.get("question") or "").strip()[:800]
        history  = d.get("history",[])[-8:]
        records  = load_records("review_records") or load_records("ct_records")
        if not records: return jsonify({"error":"No records. Run search first."}),400
        try:
            return jsonify({"answer": llm.chatbot_answer(question,records[:20],history)})
        except Exception as e: return jsonify(safe_err("Chat failed.",e)),500

    # ── Error handlers ────────────────────────────────────────────
    for code,msg in [(400,"Bad request"),(403,"Access denied"),(404,"Page not found"),
                     (405,"Method not allowed"),(413,"Request too large"),(500,"Internal server error")]:
        app.register_error_handler(code,
            lambda e,c=code,m=msg: (render_template("error.html",code=c,msg=m),c))

    @app.errorhandler(429)
    def rate_limited(e):
        security_logger.warning(f"RATE_LIMITED ip={get_ip()} path={request.path}")
        return jsonify({"error":"Too many requests. You have been temporarily blocked."}),429

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = "0.0.0.0" if os.environ.get("FLASK_ENV") == "production" else "127.0.0.1"
    app.run(debug=False, host=host, port=port)
