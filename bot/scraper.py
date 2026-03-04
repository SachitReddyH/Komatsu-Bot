"""
bot/scraper.py
--------------
Agent 1 helper: fetches listing pages from tradeearthmovers (the iframe
that powers komatsu.com.au/equipment/used-equipment) and returns
structured listing data without needing a real browser.

The iframe is a Next.js SSR app, so every page response already contains
all listing data inside a <script id="__NEXT_DATA__"> tag.
"""

import json
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---- Constants -----------------------------------------------------------

_SEARCH_BASE = (
    "https://iframe.tradeearthmovers.com.au/externalsearch"
    "/code-dd74cd3a-7423-41a3-8227-e1a92b01925a"
)
_DETAIL_BASE = "https://iframe.tradeearthmovers.com.au"
_KOMATSU_PAGE = "https://www.komatsu.com.au/equipment/used-equipment"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
    "Referer": "https://www.komatsu.com.au/equipment/used-equipment",
}

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


# ---- URL builders --------------------------------------------------------

def _search_url(keyword: str, page: int = 1) -> str:
    """Build the search URL for a keyword + optional page number."""
    slug = keyword.strip().lower().replace(" ", "-")
    url = f"{_SEARCH_BASE}/keywords-{slug}"
    if page > 1:
        url += f"/page-{page}"
    return url


# ---- HTML / data helpers -------------------------------------------------

def _extract_next_data(html: str) -> dict:
    """Pull the __NEXT_DATA__ JSON blob out of the page HTML."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("__NEXT_DATA__ not found in response – page structure may have changed")
    return json.loads(m.group(1))


def parse_price(price_str: str) -> Optional[int]:
    """Convert '$160,000' → 160000.  Returns None for POA / empty."""
    if not price_str:
        return None
    cleaned = re.sub(r"[^\d]", "", price_str)
    return int(cleaned) if cleaned else None


def parse_year(title: str) -> Optional[int]:
    """Extract the 4-digit year from a title like '2018 KOMATSU HD785'."""
    m = re.search(r"\b(19|20)\d{2}\b", title)
    return int(m.group()) if m else None


# ---- Core fetch ----------------------------------------------------------

def fetch_listings(keyword: str, client: httpx.Client) -> list[dict]:
    """
    Fetch ALL listing pages for *keyword* and return a flat list of raw
    classified dicts as returned by the Next.js SSR payload.
    """
    all_classifieds: list[dict] = []

    # --- Page 1 ---
    url = _search_url(keyword, page=1)
    logger.info("Fetching %s", url)
    resp = client.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()

    data = _extract_next_data(resp.text)
    pp = data["props"]["pageProps"]
    pagination = pp.get("pagination", {})
    all_classifieds.extend(pp.get("classifieds", []))

    total = pagination.get("total", 0)
    page_size = pagination.get("pageSize", 12)
    total_pages = max(1, (total + page_size - 1) // page_size)

    logger.info("Total results for '%s': %d  (pages: %d)", keyword, total, total_pages)

    # --- Pages 2+ ---
    for page in range(2, total_pages + 1):
        url = _search_url(keyword, page=page)
        logger.info("Fetching page %d: %s", page, url)
        try:
            resp = client.get(url, headers=_HEADERS, timeout=30)
            if resp.status_code != 200:
                logger.warning("Page %d returned HTTP %d – stopping pagination", page, resp.status_code)
                break
            data = _extract_next_data(resp.text)
            pp = data["props"]["pageProps"]
            batch = pp.get("classifieds", [])
            if not batch:
                break
            all_classifieds.extend(batch)
        except Exception as exc:
            logger.warning("Error fetching page %d: %s", page, exc)
            break

    return all_classifieds


# ---- Filtering -----------------------------------------------------------

def filter_listings(
    listings: list[dict],
    model: str,
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
) -> list[dict]:
    """
    Client-side filter on top of the keyword-narrowed server results.
    Checks model substring, year range, and price range.
    """
    model_upper = model.upper()
    filtered = []

    for item in listings:
        title = item.get("title", "").upper()

        # Model must appear in the title
        if model_upper not in title:
            continue

        # Year filter
        year = parse_year(item.get("title", ""))
        if year_min and year and year < year_min:
            continue
        if year_max and year and year > year_max:
            continue

        # Price filter
        price = parse_price(item.get("price", ""))
        if price_min and price and price < price_min:
            continue
        if price_max and price and price > price_max:
            continue

        filtered.append(item)

    return filtered


# ---- Output formatter ----------------------------------------------------

def format_listing(item: dict) -> dict:
    """Convert a raw classified dict into a clean, flat dict for notifications / DB."""
    raw_url = item.get("url", "")
    detail_url = f"{_DETAIL_BASE}{raw_url}" if raw_url.startswith("/") else raw_url

    # Build absolute image URL (tradeearthmovers uses protocol-relative URLs)
    raw_img = (
        item.get("image_w600")
        or item.get("image_w300")
        or item.get("image_w280")
        or item.get("image")
        or ""
    )
    image_url = ("https:" + raw_img) if raw_img.startswith("//") else raw_img

    return {
        "id": str(item.get("id", "")),
        "title": item.get("title", ""),
        "year": parse_year(item.get("title", "")),
        "price": item.get("price", "N/A"),
        "price_int": parse_price(item.get("price", "")),
        "seller_name": item.get("sellerName", ""),
        "seller_phone": item.get("sellerPhone", "N/A"),
        "location": item.get("location", ""),
        "seller_address": item.get("sellerAddress", ""),
        "short_description": (item.get("shortDescription") or "")[:500],
        "image_url": image_url,
        "detail_url": detail_url,
        "komatsu_url": _KOMATSU_PAGE,
        "category_type": item.get("categoryType", ""),
        "category_subtype": item.get("categorySubtype", ""),
    }
