"""
bot/rba_scraper.py
------------------
Playwright + stealth scraper for rbauction.com.au

Flow:
  1. /heavy-equipment-auctions  →  collect all "Bidding Open" event URLs
  2. For each event URL          →  paginate / scroll through ALL lot items
  3. Return raw lot dicts        →  caller calls filter_lots() + format_lot()

Strategy (layered, most efficient first):
  A) __NEXT_DATA__ JSON blob  (fast, no extra network calls)
  B) XHR/Fetch API interception (catches dynamic API calls)
  C) Full DOM scraping           (final fallback, slowest)

Stealth: navigator.webdriver patched out, realistic UA, locale, timezone.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urljoin

from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PWTimeout

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
BASE_URL     = "https://www.rbauction.com.au"
AUCTIONS_URL = f"{BASE_URL}/heavy-equipment-auctions"

STEALTH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Selectors to try for auction event cards on the listing page
_EVENT_CARD_SELECTORS = [
    "[data-testid='auction-card']",
    ".AuctionCard",
    "[class*='AuctionCard']",
    "[class*='auction-card']",
    "article[class*='auction']",
    "[class*='EventCard']",
    "[class*='event-card']",
]

# Selectors to try for individual lot cards on an event page
_LOT_CARD_SELECTORS = [
    "[data-testid='lot-card']",
    "[data-testid='lot-item']",
    ".LotCard",
    "[class*='LotCard']",
    "[class*='lot-card']",
    "[class*='lot-item']",
    "[class*='SearchResultCard']",
    "[class*='SearchResult']",
    "[class*='ItemCard']",
    "[class*='inventory-item']",
    "[class*='EquipmentCard']",
    "[class*='equipment-card']",
]


# ─── Browser helpers ─────────────────────────────────────────────────────────

async def _new_stealth_context(playwright):
    """Launch Chromium with stealth settings that hide automation fingerprints."""
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-extensions",
        ],
    )
    context = await browser.new_context(
        user_agent=STEALTH_UA,
        viewport={"width": 1366, "height": 768},
        locale="en-AU",
        timezone_id="Australia/Sydney",
        java_script_enabled=True,
    )
    # Patch navigator.webdriver so the site can't detect Playwright
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-AU', 'en'] });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'permissions', {
            value: { query: () => Promise.resolve({ state: 'granted' }) }
        });
    """)
    return browser, context


# ─── Step 1: Auction Event Discovery ─────────────────────────────────────────

async def fetch_bidding_open_events() -> list[dict]:
    """
    Navigate to /heavy-equipment-auctions and return a list of
    {"title": ..., "url": ..., "location": ...}
    for every event currently showing 'Bidding Open' or 'Online Bidding Open'.
    """
    async with async_playwright() as pw:
        browser, ctx = await _new_stealth_context(pw)
        page = await ctx.new_page()
        try:
            logger.info("RBA Scraper: loading auction events page …")
            await page.goto(AUCTIONS_URL, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4_000)       # let React hydrate

            # ── A) __NEXT_DATA__ ──────────────────────────────────────────────
            events = await _events_from_next_data(page)
            if events:
                logger.info("Found %d Bidding Open events (via __NEXT_DATA__)", len(events))
                return events

            # ── B) DOM cards ──────────────────────────────────────────────────
            events = await _events_from_dom(page)
            if events:
                logger.info("Found %d Bidding Open events (via DOM)", len(events))
                return events

            # ── C) Link pattern fallback ──────────────────────────────────────
            events = await _events_from_links(page)
            logger.info("Found %d Bidding Open events (via link pattern)", len(events))
            return events

        except Exception as exc:
            logger.exception("fetch_bidding_open_events failed: %s", exc)
            return []
        finally:
            await browser.close()


async def _events_from_next_data(page) -> list[dict]:
    """Try to parse auction events from the embedded __NEXT_DATA__ JSON blob."""
    try:
        raw = await page.evaluate("""
            () => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            }
        """)
        if not raw:
            return []

        import json
        data = json.loads(raw)

        open_events: list[dict] = []

        def walk(obj, depth=0):
            if depth > 10 or not isinstance(obj, (dict, list)):
                return
            if isinstance(obj, list):
                for item in obj:
                    walk(item, depth + 1)
            elif isinstance(obj, dict):
                keys_lower = {k.lower() for k in obj.keys()}
                # Does this dict look like an auction event?
                if any(k in keys_lower for k in
                       ("auctionstatus", "biddingtatus", "status", "auctioneventid")):
                    ev_lower = {k.lower(): v for k, v in obj.items()}
                    status_val = str(
                        ev_lower.get("auctionstatus",
                        ev_lower.get("biddingstatus",
                        ev_lower.get("status", "")))
                    ).lower()
                    if _is_bidding_open(status_val):
                        url_val = (
                            ev_lower.get("url") or
                            ev_lower.get("href") or
                            ev_lower.get("slug") or
                            ev_lower.get("path") or ""
                        )
                        if url_val:
                            open_events.append({
                                "title":    str(ev_lower.get("name", ev_lower.get("title", url_val))),
                                "url":      urljoin(BASE_URL, str(url_val)),
                                "location": str(ev_lower.get("location",
                                                ev_lower.get("city",
                                                ev_lower.get("region", "")))),
                            })
                for v in obj.values():
                    walk(v, depth + 1)

        walk(data)
        return open_events

    except Exception as exc:
        logger.debug("__NEXT_DATA__ event extraction failed: %s", exc)
        return []


async def _events_from_dom(page) -> list[dict]:
    """Scrape auction event cards from the rendered DOM."""
    for selector in _EVENT_CARD_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=5_000)
            cards = await page.query_selector_all(selector)
            if not cards:
                continue

            open_events: list[dict] = []
            for card in cards:
                try:
                    text = (await card.inner_text()).lower()
                    if not _is_bidding_open(text):
                        continue

                    link_el = await card.query_selector("a[href]")
                    if not link_el:
                        continue
                    href = (await link_el.get_attribute("href") or "").strip()
                    if not href or href.startswith("#"):
                        continue

                    title_el = await card.query_selector(
                        "h2, h3, h4, [class*='title'], [class*='Title'], [class*='name']"
                    )
                    title = (await title_el.inner_text()).strip() if title_el else href

                    loc_el = await card.query_selector(
                        "[class*='location'], [class*='city'], [class*='Location']"
                    )
                    location = (await loc_el.inner_text()).strip() if loc_el else ""

                    open_events.append({
                        "title":    title,
                        "url":      urljoin(BASE_URL, href),
                        "location": location,
                    })
                except Exception as exc:
                    logger.debug("Event card parse error: %s", exc)

            if open_events:
                return open_events

        except PWTimeout:
            continue

    return []


async def _events_from_links(page) -> list[dict]:
    """Last-resort: collect hrefs matching the /heavy-equipment-auctions/<slug>-<id> pattern."""
    hrefs = await page.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]'))
                   .map(a => ({ href: a.href, text: a.innerText.trim().slice(0, 120) }))
    """)
    pattern = re.compile(
        r"/heavy-equipment-auctions/[a-z0-9][\w-]+-\d+$", re.I
    )
    seen_urls: set[str] = set()
    events: list[dict] = []
    for item in hrefs:
        href = item.get("href", "")
        if pattern.search(href) and href not in seen_urls:
            seen_urls.add(href)
            events.append({
                "title":    item.get("text", href).split("\n")[0].strip(),
                "url":      href,
                "location": "",
            })
    return events


# ─── Step 2: Lot scraping for a single event ─────────────────────────────────

async def fetch_event_lots(event_url: str) -> list[dict]:
    """
    Scrape all lot items from a single Bidding Open event page.
    Returns a list of raw lot dicts (un-filtered).
    """
    async with async_playwright() as pw:
        browser, ctx = await _new_stealth_context(pw)
        page = await ctx.new_page()
        try:
            return await _scrape_event_page(page, event_url)
        except Exception as exc:
            logger.exception("fetch_event_lots failed for %s: %s", event_url, exc)
            return []
        finally:
            await browser.close()


async def _scrape_event_page(page, event_url: str) -> list[dict]:
    logger.info("  Scraping event page: %s", event_url)

    # Set up network interceptor BEFORE navigating
    captured_responses: list[dict] = []

    async def _on_response(response):
        try:
            url = response.url.lower()
            if any(kw in url for kw in ("lot", "item", "inventory", "search", "equipment")):
                if response.status == 200:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        body = await response.json()
                        captured_responses.append(body)
        except Exception:
            pass

    page.on("response", _on_response)

    await page.goto(event_url, wait_until="domcontentloaded", timeout=45_000)
    await page.wait_for_timeout(4_000)

    # ── A) __NEXT_DATA__ ──────────────────────────────────────────────────────
    lots = await _lots_from_next_data(page, event_url)
    if lots:
        logger.info("  → %d lots via __NEXT_DATA__", len(lots))
        return lots

    # ── B) Captured XHR/fetch responses ──────────────────────────────────────
    await page.wait_for_timeout(2_000)     # give network calls time to complete
    if captured_responses:
        lots = _lots_from_captured(captured_responses, event_url)
        if lots:
            logger.info("  → %d lots via network intercept", len(lots))
            return lots

    # ── C) DOM scraping ───────────────────────────────────────────────────────
    lots = await _lots_from_dom(page, event_url)
    logger.info("  → %d lots via DOM", len(lots))
    return lots


async def _lots_from_next_data(page, event_url: str) -> list[dict]:
    try:
        raw = await page.evaluate("""
            () => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            }
        """)
        if not raw:
            return []

        import json
        data = json.loads(raw)

        # Walk the tree looking for arrays that look like lot lists
        def find_lot_arrays(obj, depth=0) -> list:
            if depth > 12 or not isinstance(obj, (dict, list)):
                return []
            if isinstance(obj, list) and len(obj) >= 1:
                first = obj[0]
                if isinstance(first, dict):
                    keys_lower = {k.lower() for k in first.keys()}
                    lot_indicators = {"lotnumber", "lot_number", "lotid", "make",
                                      "model", "currentbid", "highbid", "inventoryid"}
                    if lot_indicators & keys_lower:
                        return [obj]
            results = []
            if isinstance(obj, dict):
                for v in obj.values():
                    results.extend(find_lot_arrays(v, depth + 1))
            elif isinstance(obj, list):
                for item in obj:
                    results.extend(find_lot_arrays(item, depth + 1))
            return results

        lot_arrays = find_lot_arrays(data)
        if not lot_arrays:
            return []

        # Use the largest array found (most likely to be the full lot list)
        best = max(lot_arrays, key=len)
        return [_raw_dict_to_lot(item, event_url) for item in best
                if isinstance(item, dict)]

    except Exception as exc:
        logger.debug("Lot __NEXT_DATA__ extraction failed: %s", exc)
        return []


def _lots_from_captured(captured: list[dict], event_url: str) -> list[dict]:
    """Parse lots from captured XHR/fetch JSON responses."""
    lots: list[dict] = []

    def find_lot_arrays(obj, depth=0):
        if depth > 8 or not isinstance(obj, (dict, list)):
            return []
        if isinstance(obj, list) and len(obj) >= 1 and isinstance(obj[0], dict):
            first_keys = {k.lower() for k in obj[0].keys()}
            if {"make", "model"} & first_keys or {"lotnumber", "lot_number"} & first_keys:
                return [obj]
        results = []
        if isinstance(obj, dict):
            for v in obj.values():
                results.extend(find_lot_arrays(v, depth + 1))
        elif isinstance(obj, list):
            for item in obj:
                results.extend(find_lot_arrays(item, depth + 1))
        return results

    for response_body in captured:
        for arr in find_lot_arrays(response_body):
            for item in arr:
                if isinstance(item, dict):
                    lot = _raw_dict_to_lot(item, event_url)
                    if lot.get("title"):
                        lots.append(lot)

    return lots


async def _lots_from_dom(page, event_url: str) -> list[dict]:
    """Full DOM scrape of lot cards — handles infinite scroll / load-more."""
    await _scroll_to_load_all(page)

    cards = []
    used_selector = ""
    for selector in _LOT_CARD_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=5_000)
            cards = await page.query_selector_all(selector)
            if cards:
                used_selector = selector
                logger.debug("Lot cards found with selector '%s': %d", selector, len(cards))
                break
        except PWTimeout:
            continue

    if not cards:
        logger.warning("No lot cards found on %s (tried all selectors)", event_url)
        return []

    lots: list[dict] = []
    for card in cards:
        try:
            lot = await _parse_lot_card(card, event_url)
            if lot:
                lots.append(lot)
        except Exception as exc:
            logger.debug("Lot card parse error: %s", exc)
    return lots


async def _scroll_to_load_all(page, max_scrolls: int = 25):
    """Scroll page bottom repeatedly to trigger lazy-load / infinite scroll."""
    prev_height = 0
    for _ in range(max_scrolls):
        curr_height = await page.evaluate("document.body.scrollHeight")
        if curr_height == prev_height:
            break
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1_800)

        # Click "Load More" / "Show More" buttons if present
        for btn_text in ("load more", "show more", "view more", "see more"):
            try:
                btn = await page.query_selector(
                    f"button:text-matches('{btn_text}', 'i'), "
                    f"a:text-matches('{btn_text}', 'i')"
                )
                if btn and await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(2_000)
                    break
            except Exception:
                pass

        prev_height = curr_height


async def _parse_lot_card(card, event_url: str) -> dict | None:
    """Extract all relevant fields from a single lot card DOM element."""

    # Title / equipment name
    title_el = await card.query_selector(
        "h2, h3, h4, [class*='title'], [class*='Title'], "
        "[class*='name'], [class*='Name'], [class*='description']"
    )
    title = (await title_el.inner_text()).strip() if title_el else ""

    # Year – explicit element or extract from title text
    year_el = await card.query_selector("[class*='year'], [class*='Year']")
    year = (await year_el.inner_text()).strip() if year_el else ""
    if not year:
        m = re.search(r"\b(19|20)\d{2}\b", title)
        year = m.group(0) if m else ""

    # Lot number
    lot_number = ""
    lot_el = await card.query_selector(
        "[class*='lot'], [class*='Lot'], [data-testid*='lot'], [class*='batch']"
    )
    if lot_el:
        lot_text = await lot_el.inner_text()
        m = re.search(r"#?\s*(\d+)", lot_text)
        lot_number = m.group(1) if m else lot_text.strip()

    # Current bid / price
    current_bid = "N/A"
    for bid_sel in [
        "[class*='bid'], [class*='Bid']",
        "[class*='price'], [class*='Price']",
        "[class*='amount'], [class*='Amount']",
        "[class*='value'], [class*='Value']",
    ]:
        el = await card.query_selector(bid_sel)
        if el:
            text = (await el.inner_text()).strip()
            if text and any(c.isdigit() for c in text):
                current_bid = text
                break

    # Image (primary thumbnail)
    image_url = ""
    img_el = await card.query_selector("img[src]:not([src='']), img[data-src]")
    if img_el:
        image_url = (
            await img_el.get_attribute("src")
            or await img_el.get_attribute("data-src")
            or ""
        )

    # Detail URL
    detail_url = event_url
    link_el = await card.query_selector("a[href]")
    if link_el:
        href = (await link_el.get_attribute("href") or "").strip()
        if href and not href.startswith("#"):
            detail_url = urljoin(BASE_URL, href)

    # Location
    loc_el = await card.query_selector(
        "[class*='location'], [class*='Location'], [class*='city'], [class*='City']"
    )
    location = (await loc_el.inner_text()).strip() if loc_el else ""

    # Hours / meter reading
    hours = ""
    hours_el = await card.query_selector(
        "[class*='hour'], [class*='Hour'], [class*='meter'], [class*='Meter']"
    )
    if hours_el:
        hours = (await hours_el.inner_text()).strip()

    # Category / equipment type
    category = ""
    cat_el = await card.query_selector(
        "[class*='category'], [class*='Category'], [class*='type'], [class*='Type']"
    )
    if cat_el:
        category = (await cat_el.inner_text()).strip()

    if not title and not lot_number:
        return None

    return {
        "lot_number":   lot_number,
        "title":        title,
        "year":         year,
        "make":         "",           # often part of title in DOM
        "model":        "",           # often part of title in DOM
        "category":     category,
        "current_bid":  current_bid,
        "image_url":    image_url,
        "detail_url":   detail_url,
        "event_url":    event_url,
        "description":  "",
        "location":     location,
        "hours":        hours,
    }


# ─── Filtering ───────────────────────────────────────────────────────────────

def filter_lots(lots: list[dict], target: dict) -> list[dict]:
    """
    Filter lot items against a target definition.

    model/make  –  MANDATORY  (substring match, case-insensitive)
    year_min / year_max / price_max  –  OPTIONAL
    """
    model_query = target.get("model", "").strip().lower()
    if not model_query:
        return []

    results: list[dict] = []
    for lot in lots:
        # Build a combined searchable string from all text fields
        searchable = " ".join([
            lot.get("title",    ""),
            lot.get("make",     ""),
            lot.get("model",    ""),
            lot.get("category", ""),
        ]).lower()

        if model_query not in searchable:
            continue

        # Optional: year range filter
        if target.get("year_min") or target.get("year_max"):
            year_digits = re.sub(r"\D", "", lot.get("year", ""))
            if year_digits:
                try:
                    y = int(year_digits)
                    if target.get("year_min") and y < int(target["year_min"]):
                        continue
                    if target.get("year_max") and y > int(target["year_max"]):
                        continue
                except ValueError:
                    pass

        # Optional: maximum price filter
        if target.get("price_max"):
            bid_int = _price_str_to_int(lot.get("current_bid", ""))
            if bid_int and bid_int > int(target["price_max"]):
                continue

        results.append(lot)

    return results


# ─── Formatting ──────────────────────────────────────────────────────────────

def format_lot(raw: dict, event: dict) -> dict:
    """
    Normalise a raw lot dict into the standard listing schema used by InformerAgent.
    Adds RBA-specific fields alongside the base fields.
    """
    return {
        # ── Base fields (InformerAgent compat) ───────────────────────────────
        "title":             raw.get("title", "Unknown"),
        "price":             raw.get("current_bid", "N/A"),   # alias
        "seller_name":       event.get("title", "RB Auction"),
        "seller_phone":      "",
        "location":          raw.get("location") or event.get("location", ""),
        "detail_url":        raw.get("detail_url", event.get("url", "")),
        "komatsu_url":       "",
        "short_description": _build_description(raw),
        # ── RBA-specific ─────────────────────────────────────────────────────
        "source":            "rbauction",
        "current_bid":       raw.get("current_bid", "N/A"),
        "lot_number":        raw.get("lot_number", ""),
        "year":              raw.get("year", ""),
        "make":              raw.get("make", ""),
        "model":             raw.get("model", ""),
        "category":          raw.get("category", ""),
        "image_url":         raw.get("image_url", ""),
        "hours":             raw.get("hours", ""),
        "auction_event":     event.get("title", ""),
        "auction_url":       event.get("url", ""),
    }


# ─── Private helpers ─────────────────────────────────────────────────────────

def _is_bidding_open(text: str) -> bool:
    t = text.lower()
    return (
        ("bidding" in t and "open" in t)
        or "online bidding" in t
        or "bid now" in t
        or "timed auction" in t
    )


def _raw_dict_to_lot(item: dict, event_url: str) -> dict:
    """Convert a raw API/JSON dict (from __NEXT_DATA__ or XHR) into a lot dict."""
    kl = {k.lower(): v for k, v in item.items()}

    title = (
        f"{kl.get('year','')} {kl.get('make','')} {kl.get('model','')}".strip()
        or str(kl.get("title", kl.get("description", "")))
    ).strip()

    image_url = ""
    for k in ("imageurl", "image_url", "thumbnailurl", "thumbnail", "primaryimageurl",
              "photo", "listingimage", "imageuri"):
        if kl.get(k):
            image_url = str(kl[k])
            break

    detail_path = kl.get("url") or kl.get("href") or kl.get("detailurl") or kl.get("path") or ""
    detail_url  = urljoin(BASE_URL, str(detail_path)) if detail_path else event_url

    current_bid = _normalise_price(
        kl.get("currentbid") or kl.get("current_bid") or
        kl.get("highbid")    or kl.get("high_bid")    or
        kl.get("price")      or kl.get("startingbid") or ""
    )

    return {
        "lot_number":   str(kl.get("lotnumber", kl.get("lot_number", kl.get("lotid", "")))),
        "title":        title,
        "year":         str(kl.get("year", "")),
        "make":         str(kl.get("make", "")),
        "model":        str(kl.get("model", "")),
        "category":     str(kl.get("category", kl.get("equipmenttype", kl.get("type", "")))),
        "current_bid":  current_bid,
        "image_url":    image_url,
        "detail_url":   detail_url,
        "event_url":    event_url,
        "description":  str(kl.get("description", kl.get("shortdescription", ""))),
        "location":     str(kl.get("location", kl.get("city", kl.get("region", "")))),
        "hours":        str(kl.get("hours", kl.get("meterreading", kl.get("smr", "")))),
    }


def _build_description(raw: dict) -> str:
    parts = []
    if raw.get("lot_number"):
        parts.append(f"Lot #{raw['lot_number']}")
    if raw.get("hours"):
        parts.append(f"Hours: {raw['hours']}")
    if raw.get("category"):
        parts.append(f"Type: {raw['category']}")
    if raw.get("description"):
        parts.append(raw["description"])
    return "  |  ".join(parts)


def _normalise_price(val) -> str:
    if val is None or val == "":
        return "N/A"
    if isinstance(val, (int, float)):
        return f"${val:,.0f}"
    s = str(val).strip()
    if s.lower() in ("", "0", "n/a", "none"):
        return "N/A"
    if "$" in s or any(c.isdigit() for c in s):
        return s
    return "N/A"


def _price_str_to_int(price_str: str) -> int | None:
    if not price_str:
        return None
    digits = re.sub(r"[^\d]", "", price_str)
    return int(digits) if digits else None
