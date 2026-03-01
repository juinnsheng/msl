# MSL Intel Platform — Deployment Guide

## Quick Fix Checklist (if APIs not working)

### 1. Verify environment variables are set in Railway
Go to Railway → your project → Variables tab. You need:
```
SECRET_KEY          = <random 64-char hex>
ADMIN_USERNAME      = your_username
ADMIN_PASSWORD      = your_password
NVIDIA_API_KEY      = nvapi-xxxx...
NCBI_API_KEY        = (optional but recommended)
FLASK_ENV           = production
```

### 2. Test APIs directly with Postman
Import `MSL_Intel_Postman_Collection.json` into Postman.
- Run request **#11** to test PubMed connectivity (no auth needed)
- Run request **#10** to test NVIDIA NIM directly (put your key in the header)
- These bypass the app entirely — if they fail, it's an API key/network issue

### 3. Check Railway logs
In Railway → Deployments → click the active deployment → View Logs.
Look for:
- `NVIDIA LLM client initialised` — good
- `NVIDIA_API_KEY not set` — key is missing in env vars
- `Redis unavailable` — normal on free tier, uses in-process fallback

---

## Free-Tier Limitations

| Feature | Free Tier Cap | Reason |
|---------|---------------|--------|
| Evidence search results | 50 max (30 default) | NCBI rate limits |
| Bulk review records | 50 max (30 default) | NCBI rate limits |
| LLM extraction | 30 records | NVIDIA token limits |
| Clinical trials | 50 max (30 default) | ClinicalTrials.gov best practice |
| Session duration | 8 hours | Memory constraints |

To increase limits, set `NCBI_API_KEY` (free from NCBI) and upgrade NVIDIA plan.

---

## NVIDIA NIM Setup

1. Go to https://build.nvidia.com/
2. Sign up / sign in
3. Go to "API Key" section → generate a key
4. Free tier: 1,000 credits/month (each query uses ~5-20 credits)
5. Add as `NVIDIA_API_KEY` in Railway environment variables

**Without NVIDIA key:** The app still works but AI ranking and LLM extraction are disabled.
Results are sorted by recency instead of relevance.

---

## NCBI API Key Setup

1. Go to https://www.ncbi.nlm.nih.gov/account/
2. Register for a free account
3. Settings → API Key → generate
4. Add as `NCBI_API_KEY` in Railway environment variables

**Without NCBI key:** Works but rate-limited to 3 requests/second (vs 10/sec with key).
For queries fetching 30-50 records, this adds ~10-15 seconds.

---

## Railway Deployment Steps

```bash
# 1. Install Railway CLI
npm install -g @railway/cli

# 2. Login
railway login

# 3. In your project directory
railway init      # (first time only)
railway up        # deploy

# 4. Set environment variables
railway variables set SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
railway variables set ADMIN_USERNAME="your_username"
railway variables set ADMIN_PASSWORD="your_password"
railway variables set NVIDIA_API_KEY="nvapi-..."
railway variables set NCBI_API_KEY="..."
railway variables set FLASK_ENV="production"
```

---

## Local Development

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
python app.py
# Open http://localhost:5000
```

---

## What Changed from v1

| Issue | Old Behavior | New Behavior |
|-------|-------------|--------------|
| Corporate users blocked | VPN/datacenter IPs auto-banned (affected Railway's own IP range) | No IP geo-blocking |
| Users locked out quickly | 2 failed logins = 24h ban | 5 failed logins = 1h ban |
| No progress feedback | Spinner only, no status | Progress bar polls `/api/status` |
| API errors silent | Generic 500 error | Specific error messages with fix hints |
| Rate limits too strict | 3 searches/min | 20 searches/min |
| No free-tier warnings | Silent cap | User-facing cap notices |
| Session too short | 4 hours | 8 hours |
| Session IP binding | Kicked out on network change | Relaxed to basic protection |
