"""Pull the supplier catalogue into DealerDesk (contract section 8).

Fetching and applying are separate phases: the database is only touched after
every page has been fetched, so a failed feed changes nothing.
"""
import logging
import math
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.db import connection, transaction

from core.models import Product

logger = logging.getLogger(__name__)

PAGE_LIMIT = 20
MAX_ATTEMPTS = 5          # per request, retries included
REQUEST_TIMEOUT = 10      # seconds per HTTP request
MAX_RETRY_AFTER = 30      # never sleep longer than this for one 429
TOTAL_BUDGET = 100        # seconds; the endpoint must answer within 120
BACKOFF_BASE = 0.5        # seconds, doubled for each failed attempt
SYNC_LOCK_ID = 7_340_201  # postgres advisory lock: one sync at a time
MAX_PRICE = Decimal("9999999999.99")  # fits Product.price (12 digits)
TEXT_FIELDS = {
    "name": 255,
    "category": 100,
    "brand": 100,
    "vehicle_fitment": 255,
    "description": None,
}


class SupplierUnavailable(Exception):
    """The full feed could not be fetched."""


def _retry_after_seconds(response):
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(seconds) or seconds < 0:
        return 1.0
    return min(seconds, MAX_RETRY_AFTER)


def _fetch_page(session, url, cursor, stats, deadline):
    """GET one page, with at most MAX_ATTEMPTS tries. Returns parsed JSON."""
    params = {"limit": PAGE_LIMIT}
    if cursor:
        params["cursor"] = cursor

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if time.monotonic() > deadline:
            raise SupplierUnavailable("Time budget exhausted.")
        stats["requests_made"] += 1
        wait = BACKOFF_BASE * 2 ** (attempt - 1)
        try:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.warning("Supplier request failed: %s", exc)
        else:
            code = response.status_code
            if code == 200:
                try:
                    data = response.json()
                    items = data["items"]
                    next_cursor = data.get("next_cursor")
                    if isinstance(items, list) and (
                        next_cursor is None or isinstance(next_cursor, str)
                    ):
                        return items, next_cursor or None
                except (ValueError, KeyError, TypeError, AttributeError):
                    pass
                logger.warning("Supplier returned a malformed page.")
            elif code == 429:
                wait = _retry_after_seconds(response)
            elif code >= 500:
                logger.warning("Supplier returned %s.", code)
            else:
                # 401 (bad key) and other 4xx will not fix themselves.
                raise SupplierUnavailable(f"Supplier returned HTTP {code}.")

        if attempt == MAX_ATTEMPTS:
            break
        if time.monotonic() + wait > deadline:
            raise SupplierUnavailable("Time budget exhausted.")
        time.sleep(wait)

    raise SupplierUnavailable("Supplier request kept failing.")


def fetch_feed():
    """Fetch every page. Returns (items, pages_fetched, requests_made)."""
    base = (settings.SUPPLIER_API_URL or "").rstrip("/")
    if not base:
        raise SupplierUnavailable("SUPPLIER_API_URL is not configured.")

    stats = {"requests_made": 0}
    deadline = time.monotonic() + TOTAL_BUDGET
    items, pages, cursor, seen = [], 0, None, set()
    session = requests.Session()
    session.headers["X-Api-Key"] = settings.SUPPLIER_API_KEY or ""
    url = f"{base}/v1/products"

    while True:
        page_items, cursor = _fetch_page(session, url, cursor, stats, deadline)
        items.extend(page_items)
        pages += 1
        if cursor is None:
            return items, pages, stats["requests_made"]
        if cursor in seen:
            raise SupplierUnavailable("Supplier cursor did not advance.")
        seen.add(cursor)


def _parse_price(value):
    """A positive amount with at most 2 decimals, else None."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        price = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not price.is_finite() or price <= 0 or price > MAX_PRICE:
        return None
    if price != price.quantize(Decimal("0.01")):
        return None
    return price.quantize(Decimal("0.01"))


def _parse_time(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
    return datetime.min.replace(tzinfo=timezone.utc)


def clean_item(item):
    """Return (fields, None) for a valid item, or (None, reason)."""
    if not isinstance(item, dict):
        return None, "item is not an object"
    sku = item.get("sku")
    if not isinstance(sku, str) or not sku.strip():
        return None, "missing sku"
    sku = sku.strip()
    if "\x00" in sku:
        return None, "invalid sku"
    if len(sku) > Product._meta.get_field("sku").max_length:
        return None, "sku too long"
    price = _parse_price(item.get("price"))
    if price is None:
        return None, "invalid price"

    fields = {"sku": sku, "price": price}
    for name, max_length in TEXT_FIELDS.items():
        value = item.get(name)
        value = "" if value is None else value
        if not isinstance(value, str):
            return None, f"invalid {name}"
        if "\x00" in value:
            return None, f"invalid {name}"
        if max_length is not None and len(value) > max_length:
            return None, f"{name} too long"
        fields[name] = value
    return fields, None


def dedupe(valid):
    """Keep, for each SKU, the entry with the latest updated_at."""
    best = {}
    for fields, updated_at in valid:
        current = best.get(fields["sku"])
        if current is None or updated_at > current[1]:
            best[fields["sku"]] = (fields, updated_at)
    return [fields for fields, _ in best.values()]


def apply_feed(feed):
    """Create/update products from the cleaned feed. Never touches stock."""
    created = updated = unchanged = 0
    changeable = ["price", *TEXT_FIELDS]
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [SYNC_LOCK_ID])
        existing = {
            p.sku: p
            for p in Product.objects.select_for_update().filter(
                sku__in=[f["sku"] for f in feed]
            )
        }
        to_create, to_update = [], []
        for fields in feed:
            product = existing.get(fields["sku"])
            if product is None:
                to_create.append(Product(stock=0, **fields))
            elif any(getattr(product, n) != fields[n] for n in changeable):
                for name in changeable:
                    setattr(product, name, fields[name])
                to_update.append(product)
            else:
                unchanged += 1
        Product.objects.bulk_create(to_create)
        if to_update:
            Product.objects.bulk_update(to_update, changeable)
        created, updated = len(to_create), len(to_update)
    return created, updated, unchanged


def run_sync():
    items, pages, requests_made = fetch_feed()

    rejected, valid = [], []
    for item in items:
        fields, reason = clean_item(item)
        if fields is None:
            supplier_sku = item.get("supplier_sku") if isinstance(item, dict) else None
            rejected.append({"supplier_sku": supplier_sku, "reason": reason})
        else:
            valid.append((fields, _parse_time(item.get("updated_at"))))

    created, updated, unchanged = apply_feed(dedupe(valid))
    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "rejected": rejected,
        "pages_fetched": pages,
        "requests_made": requests_made,
    }
