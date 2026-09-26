"""Central configuration, loaded from .env with sane defaults."""
import os
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()


def _env(name, default=None):
    """Read an env var, stripping any inline '# comment' and surrounding space.
    (NOT used for DB secrets, whose values may legitimately contain '#'.)"""
    v = os.getenv(name)
    if v is None:
        return default
    v = v.split("#", 1)[0].strip()
    return v or default


# --- Database ---
DATABASE_URL = os.getenv("DATABASE_URL") or None
PG = {
    "host": os.getenv("PGHOST"),
    "port": os.getenv("PGPORT", "5432"),
    "user": os.getenv("PGUSER"),
    "password": os.getenv("PGPASSWORD"),
    "dbname": os.getenv("PGDATABASE"),
}
DB_SCHEMA = os.getenv("DB_SCHEMA", "TM")

# --- Scrape scope ---
QUERY = _env("TM_QUERY", "nfl")
REGION = _env("TM_REGION", "200")
START_DATE = _env("TM_START_DATE") or date.today().isoformat()
END_DATE = _env("TM_END_DATE") or (date.today() + timedelta(days=180)).isoformat()

# --- Behaviour ---
SEAT_MODE = _env("SEAT_MODE", "available").lower()           # 'available' | 'full'
CONCURRENCY = int(_env("CONCURRENCY", "5"))                   # simultaneous facets fetches
REQ_PER_SEC = float(_env("REQ_PER_SEC", "5"))                 # global facets rate cap
MAX_EVENTS = int(_env("MAX_EVENTS")) if _env("MAX_EVENTS") else None  # debug cap
HEADLESS = _env("HEADLESS", "true").lower() != "false"
REFRESH_MANIFEST = _env("REFRESH_MANIFEST", "false").lower() == "true"
# Browser channel for the stealth (patchright) session. "chrome" drives real
# Google Chrome (best against Kasada); set to "" to use patchright's bundled
# Chromium instead. Kasada reliably blocks non-stealth browsers, so we always
# run patchright, but real Chrome is the most human-looking.
BROWSER_CHANNEL = _env("BROWSER_CHANNEL", "chrome")

# --- Proxies (same system as axs-scraper-2) ---
# Enable with PROXY_ENABLED=true. Source resolution order (first that applies):
#   1. Proxies DB by id  (PROXIES_DB_* + PROXY_DB_IDS)  -- preferred, like axs
#   2. single proxy      (PROXY_HOST/PORT/USERNAME/PASSWORD)
# Usernames may contain ${random_alphanumeric_10} etc. (expanded per request).
PROXY_ENABLED = _env("PROXY_ENABLED", "false").lower() == "true"

# 1) Proxies DB (separate infra Postgres holding the shared `proxies` table)
PROXIES_DB_HOST = _env("PROXIES_DB_HOST")
PROXIES_DB_PORT = int(_env("PROXIES_DB_PORT", "5432"))
PROXIES_DB_NAME = _env("PROXIES_DB_NAME")
PROXIES_DB_USER = _env("PROXIES_DB_USER")
PROXIES_DB_PASSWORD = _env("PROXIES_DB_PASSWORD")
PROXY_DB_IDS = [int(x) for x in (_env("PROXY_DB_IDS", "") or "").replace(",", " ").split() if x.strip().isdigit()]
PROXY_DB_CLAIM_MODE = _env("PROXY_DB_CLAIM_MODE", "true").lower() == "true"  # claim/release vs static round-robin
PROXY_DB_STALE_MINUTES = int(_env("PROXY_DB_STALE_MINUTES", "10"))
# One sticky proxy is reused across events; on a 403 we rotate to a new proxy and
# retry the same event, up to this many times. If still blocked, we then renew
# the tmpt token (browser reload) and retry, up to TOKEN_MAX_RENEWS times.
PROXY_MAX_ROTATIONS = int(_env("PROXY_MAX_ROTATIONS", "1"))
TOKEN_MAX_RENEWS = int(_env("TOKEN_MAX_RENEWS", "1"))
# Each proxy id is a self-rotating endpoint: hitting its refresh_url swaps the
# exit IP. Seconds to wait after a rotate_ip call for the new IP to take effect.
PROXY_ROTATE_WAIT = int(_env("PROXY_ROTATE_WAIT", "13"))

# 2) single proxy
PROXY_TYPE = _env("PROXY_TYPE", "http")
PROXY_HOST = _env("PROXY_HOST")
PROXY_PORT = int(_env("PROXY_PORT", "0")) or None
PROXY_USERNAME = _env("PROXY_USERNAME")
PROXY_PASSWORD = _env("PROXY_PASSWORD")

# --- Logging ---
LOG_LEVEL = _env("LOG_LEVEL", "INFO")
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")

# --- API constants (public web keys, same ones the site uses) ---
APIKEY = "b462oi7fic6pehcdkzony5bxhe"
APISECRET = "pquzpfrfz7zd2ylvtz3w5dtyse"
RESALE_CHANNEL = "internal.ecommerce.consumer.desktop.web.browser.ticketmaster.us"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)

SEARCH_PAGE_URL = (
    "https://www.ticketmaster.com/search?q={q}&sort=date&startDate={start}&endDate={end}"
)
SEARCH_API_URL = (
    "https://www.ticketmaster.com/api/search/events"
    "?q={q}&region={region}&startDate={start}&endDate={end}&sort=date&page={page}"
)
FACETS_URL = (
    "https://services.ticketmaster.com/api/ismds/event/{event_id}/facets"
    "?by=section+seating+attributes+available+accessibility+offer+placeGroups"
    "+inventoryType+offerType+area+description"
    "&show=places&embed=area&embed=description&embed=offer&q=available"
    "&compress=places&resaleChannelId={channel}&apikey={apikey}&apisecret={apisecret}"
)
MANIFEST_URL = "https://pubapi.ticketmaster.com/sdk/static/manifest/v1/{event_id}"

MANIFEST_CACHE_DIR = os.path.join(os.path.dirname(__file__), "manifests")


def dsn() -> str:
    """psycopg2 connection string from env."""
    if DATABASE_URL:
        return DATABASE_URL
    missing = [k for k in ("host", "user", "password", "dbname") if not PG[k]]
    if missing:
        raise SystemExit(f"Missing DB config in .env: {missing} (or set DATABASE_URL)")
    return " ".join(f"{k}={v}" for k, v in PG.items() if v)
