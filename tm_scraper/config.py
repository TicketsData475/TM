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
# Leagues/queries to discover. TM_QUERIES (comma-separated) is preferred; falls
# back to the old single TM_QUERY, else defaults to nfl+nba+nhl.
QUERIES = [q.strip().lower() for q in
           (_env("TM_QUERIES") or _env("TM_QUERY") or "nfl,nba,nhl").split(",") if q.strip()]
QUERY = QUERIES[0]                              # used for the initial Kasada page load + warm-up
REGION = _env("TM_REGION", "200")
# Which customer this discovery run's events belong to (events.customer). Set
# CUSTOMER=aceify when discovering tennis; leagues default to TC.
CUSTOMER = _env("CUSTOMER", "TC")
START_DATE = _env("TM_START_DATE") or date.today().isoformat()
END_DATE = _env("TM_END_DATE") or (date.today() + timedelta(days=180)).isoformat()

# --- Behaviour ---
SEAT_MODE = _env("SEAT_MODE", "available").lower()           # 'available' | 'full'
MAX_EVENTS = int(_env("MAX_EVENTS")) if _env("MAX_EVENTS") else None  # debug cap
CONCURRENCY = int(_env("CONCURRENCY", "3"))                   # simultaneous curl fetches (per instance)
REQ_PER_SEC = float(_env("REQ_PER_SEC", "12"))               # curl rate cap (per instance)
# After solving Kasada, fire this many in-page search fetches to upgrade the
# session cookie to a state curl can reuse (a bare page load isn't enough).
WARMUP_ROUNDS = int(_env("WARMUP_ROUNDS", "6"))

# --- Detail worker / queue ---
# Scale horizontally by running more worker INSTANCES (processes/machines), each
# = one browser + one proxy. They share the TM.detail_queue via SKIP LOCKED.
import socket as _socket
WORKER_ID = _env("WORKER_ID") or f"{_socket.gethostname()}-{os.getpid()}"
CLAIM_BATCH = int(_env("CLAIM_BATCH", "20"))          # events leased per claim
LEASE_MINUTES = int(_env("LEASE_MINUTES", "15"))      # a lease older than this = crashed worker
MAX_ATTEMPTS = int(_env("MAX_ATTEMPTS", "3"))         # hard failures before -> 'failed'
WORKER_IDLE_SLEEP = int(_env("WORKER_IDLE_SLEEP", "0"))  # queue-empty wait; 0 = exit when drained
# When whole batches keep getting Kasada-blocked (the exit IPs are burned/flagged),
# stop tight-looping: after refresh + rotate still fail, sleep this long to let the
# IPs recover before trying again.
WORKER_BLOCK_COOLDOWN = int(_env("WORKER_BLOCK_COOLDOWN", "180"))
# A Kasada-blocked batch is deferred this long (next_attempt_at) instead of going
# straight back to the front of the queue, so the worker moves on to other events
# and retries the blocked ones later (not the same 20 over and over).
BLOCK_DEFER_SECONDS = int(_env("BLOCK_DEFER_SECONDS", "900"))
# If at least this fraction of a batch is blocked, treat it as the session/IP
# degrading and refresh/rotate (not only at 100%).
BLOCK_SESSION_RATIO = float(_env("BLOCK_SESSION_RATIO", "0.5"))
# Promo/non-seatmap listings (titles containing these) have no seat map -> no
# facets. Skipped in the detail stage to avoid wasting ~3 min each on failures.
# "PASS" catches GA/pass products (Ground Pass, Grounds Pass, Passe-partout, ...)
# which have no reserved seating to scrape.
SKIP_TITLE_KEYWORDS = ("HALF PRICE", "PARKING", "HOSPITALITY", "PACKAGE", "PASS")
HEADLESS = _env("HEADLESS", "false").lower() != "false"   # Kasada blocks headless
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
# exit IP. Seconds to wait after a rotate_ip call for the new IP to take effect,
# and (longer) when the id is on cooldown (HTTP 422 = rotated too recently).
PROXY_ROTATE_WAIT = int(_env("PROXY_ROTATE_WAIT", "20"))
PROXY_COOLDOWN_WAIT = int(_env("PROXY_COOLDOWN_WAIT", "60"))

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
# Seat-level inventory comes from TWO of the app's own facets calls, merged:
#   PLACES  (services)     -> which seats are available, per section, + their offerId
#   OFFERS  (offeradapter) -> each offer's list/face/total price
# Fetched by curl using the browser's Kasada-solved cookies on the same proxy IP.
# NOTE: PLACES must NOT include embed=offer (that 400s on services); prices come
# from the separate OFFERS call and get merged into PLACES._embedded.offer.
PLACES_URL = (
    "https://services.ticketmaster.com/api/ismds/event/{event_id}/facets"
    "?by=section+seating+attributes+available+accessibility+offer+placeGroups"
    "+inventoryType+offerType+area+description"
    "&show=places&embed=area&embed=description&q=available"
    "&compress=places&resaleChannelId={channel}&apikey={apikey}&apisecret={apisecret}"
)
OFFERS_URL = (
    "https://offeradapter.ticketmaster.com/api/ismds/event/{event_id}/facets"
    "?apikey={apikey}&apisecret={apisecret}&by=inventorytypes+offer&q=available"
    "&show=listpricerange&embed=offer&resaleChannelId={channel}"
)
MANIFEST_URL = "https://pubapi.ticketmaster.com/sdk/static/manifest/v1/{event_id}"

# --- HOST-system events (e.g. Australian Open) ---
# These are NOT in ismds (facets return Error.NotFound). Their availability/prices
# come from per-brand-domain endpoints instead; the domain is taken from the
# event's own url (e.g. www.ticketmaster.com.au), else this fallback.
HOST_FALLBACK_BASE = _env("HOST_FALLBACK_BASE", "https://www.ticketmaster.com.au")
SEATMAPOFFERED_PATH = "/api/seatmap/seatmapoffered/{event_id}?resaleProvider=INTL"
QUICKPICKS_PATH = (
    "/api/quickpicks/{event_id}/list?sort=price&offset={offset}&qty=1"
    "&primary=true&resale=true&defaultToOne=true&tids={tids}&resaleProvider=INTL"
)
QUICKPICKS_MAX_PAGES = int(_env("QUICKPICKS_MAX_PAGES", "80"))  # safety cap on pagination
# quickpicks returns ~20 seats/page; paginating fast trips Kasada's per-endpoint
# rate limit (block). Pace the pages (seconds between them) to look human.
QUICKPICKS_PAGE_DELAY = float(_env("QUICKPICKS_PAGE_DELAY", "1.5"))

MANIFEST_CACHE_DIR = os.path.join(os.path.dirname(__file__), "manifests")


def dsn() -> str:
    """psycopg2 connection string from env."""
    if DATABASE_URL:
        return DATABASE_URL
    missing = [k for k in ("host", "user", "password", "dbname") if not PG[k]]
    if missing:
        raise SystemExit(f"Missing DB config in .env: {missing} (or set DATABASE_URL)")
    return " ".join(f"{k}={v}" for k, v in PG.items() if v)
