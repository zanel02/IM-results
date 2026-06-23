#!/usr/bin/env python3
"""
ironman_scraper.py
------------------
Discovers group UUIDs from ironman.com race results pages and
saves them to a JSON seed file for use with the results fetcher.

STRATEGY
--------
ironman.com is a React SPA. The races listing page (/races) is
JS-rendered, so we use one of two discovery modes:

  Mode 1 (--sitemap):  Pull all URLs from ironman.com's XML sitemap
                       (https://www.ironman.com/sitemap.xml?page=N),
                       filter to results pages, fetch each one, and
                       grep the HTML for the competitor.com iframe src.

  Mode 2 (--fetch-page URL [URL ...]):  Fetch specific ironman.com results
                       pages you already know about and extract their UUIDs.

In both modes the HTML of each results page contains an iframe like:
  <iframe src="https://labs-v2.competitor.com/results/event/{group-uuid}">
We extract the UUID from there.

URL FORMATS
-----------
ironman.com has two results URL formats:
  New: https://www.ironman.com/races/<slug>/results
  Old: https://www.ironman.com/<slug>-results
Both are supported.

OUTPUT
------
  ironman_races.json — one entry per race:
    {
      "race_name":   "IRONMAN 70.3 Chattanooga",
      "slug":        "im703-chattanooga",
      "group_uuid":  "e2352999-586b-e411-93fa-005056951bf1",
      "ironman_url": "https://www.ironman.com/races/im703-chattanooga/results",
      "subevents":   []
    }

USAGE
-----
  # Discover via sitemap (comprehensive, may take ~5 min):
  python ironman_scraper.py --sitemap

  # Discover from specific pages you already know:
  python ironman_scraper.py --fetch-page \\
      https://www.ironman.com/races/im703-coeur-dalene/results \\
      https://www.ironman.com/races/im703-chattanooga/results

  # Merge into an existing file without overwriting:
  python ironman_scraper.py --sitemap --append

  # Dry run — print discovered URLs without fetching:
  python ironman_scraper.py --sitemap --dry-run

  # Adjust delay between requests (default 0.5s; robots.txt asks for 10s):
  python ironman_scraper.py --sitemap --delay 1.0
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SITEMAP_BASE    = "https://www.ironman.com/sitemap.xml"
COMPETITOR_BASE = "https://labs-v2.competitor.com"

GROUP_UUID_RE = re.compile(
    r"labs-v2\.competitor\.com/(?:results|clubpoints)/event/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)

# Matches both URL formats:
#   /races/<slug>/results   (new)
#   /<slug>-results         (old)
RESULTS_URL_RE = re.compile(
    r"ironman\.com(?:/races/([a-z0-9-]+)/results|/([a-z0-9-]+-results))/?$",
    re.IGNORECASE,
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def fetch(url: str, extra_headers: dict | None = None, timeout: int = 15) -> str:
    h = {**DEFAULT_HEADERS, **(extra_headers or {})}
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"URL error fetching {url}: {e.reason}") from e


# ---------------------------------------------------------------------------
# UUID + slug extraction
# ---------------------------------------------------------------------------

def extract_group_uuid(html: str) -> str | None:
    m = GROUP_UUID_RE.search(html)
    return m.group(1).lower() if m else None


def slug_from_url(url: str) -> str:
    """Return the race slug from either URL format."""
    m = RESULTS_URL_RE.search(url)
    if m:
        return m.group(1) or re.sub(r"-results$", "", m.group(2), flags=re.IGNORECASE)
    # Fallback: last non-empty path segment
    return urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]


def race_name_from_slug(slug: str) -> str:
    """Best-effort display name from a race slug."""
    name = re.sub(r"^im-?703-", "IRONMAN 70.3 ", slug, flags=re.IGNORECASE)
    name = re.sub(r"^ironman-", "IRONMAN ", name, flags=re.IGNORECASE)
    name = re.sub(r"^im-(?!703)", "IRONMAN ", name, flags=re.IGNORECASE)
    parts = name.split(" ", 1)
    if len(parts) == 2:
        return f"{parts[0]} {parts[1].replace('-', ' ').title()}".strip()
    return name.replace("-", " ").title()


# ---------------------------------------------------------------------------
# Sitemap discovery
# ---------------------------------------------------------------------------

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def fetch_sitemap_page(page: int, delay: float) -> list[str]:
    url = f"{SITEMAP_BASE}?page={page}"
    print(f"  Fetching sitemap page {page}: {url}")
    try:
        xml_text = fetch(url)
    except RuntimeError as e:
        print(f"  WARNING: {e}")
        return []
    time.sleep(delay)

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  WARNING: Could not parse XML: {e}")
        return []

    return [
        node.text.strip()
        for node in root.findall(".//sm:url/sm:loc", NS)
        if node.text
    ]


def discover_sitemap_pages(delay: float) -> int:
    """Return the number of sitemap pages from the sitemap index."""
    try:
        xml_text = fetch(SITEMAP_BASE)
    except RuntimeError as e:
        print(f"  WARNING: {e}")
        return 1
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return 1
    pages = root.findall(".//sm:sitemap", NS)
    return max(len(pages), 1)


def fetch_all_results_urls(delay: float) -> list[str]:
    n_pages = discover_sitemap_pages(delay)
    print(f"  Sitemap has {n_pages} page(s)")
    all_results: list[str] = []
    for page in range(1, n_pages + 1):
        urls = fetch_sitemap_page(page, delay)
        results = [u for u in urls if RESULTS_URL_RE.search(u)]
        all_results.extend(results)
        print(f"    Page {page}: {len(urls)} URLs, {len(results)} results pages")
    return all_results


# ---------------------------------------------------------------------------
# Core scraping
# ---------------------------------------------------------------------------

def scrape_race_page(url: str, delay: float, cookie: str | None = None) -> dict | None:
    headers = {"Cookie": cookie} if cookie else {}
    try:
        html = fetch(url, extra_headers=headers)
    except RuntimeError as e:
        print(f"  ERROR: {e}")
        return None
    finally:
        time.sleep(delay)

    uuid = extract_group_uuid(html)
    if not uuid:
        print(f"  WARNING: No UUID found in {url}")
        return None

    slug = slug_from_url(url)
    return {
        "race_name":   race_name_from_slug(slug),
        "slug":        slug,
        "group_uuid":  uuid,
        "ironman_url": url,
        "subevents":   [],
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_existing(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open() as f:
        races = json.load(f)
    return {r["group_uuid"]: r for r in races if "group_uuid" in r}


def save_races(races: dict[str, dict], path: Path) -> None:
    sorted_list = sorted(races.values(), key=lambda r: r.get("race_name", ""))
    with path.open("w") as f:
        json.dump(sorted_list, f, indent=2)
    print(f"\nSaved {len(sorted_list)} races to {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sitemap", action="store_true",
                      help="Discover all races via the ironman.com XML sitemap.")
    mode.add_argument("--fetch-page", nargs="+", metavar="URL",
                      help="Fetch specific ironman.com results page URLs.")
    p.add_argument("--output", "-o", default="ironman_races.json",
                   help="Output JSON file (default: ironman_races.json).")
    p.add_argument("--append", "-a", action="store_true",
                   help="Merge into an existing file instead of overwriting.")
    p.add_argument("--delay", type=float, default=0.5,
                   help="Seconds between requests (default: 0.5).")
    p.add_argument("--cookie",
                   help="Cookie header value from browser DevTools (if getting 403s).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print URLs that would be fetched without fetching them.")
    return p


def main() -> None:
    args = build_parser().parse_args()
    output_path = Path(args.output)
    races = load_existing(output_path) if args.append else {}
    newly_found = 0
    failed = 0

    if args.sitemap:
        print(f"Step 1: Discovering results URLs from sitemap...")
        results_urls = fetch_all_results_urls(args.delay)
        print(f"        Found {len(results_urls)} race results pages total.")
    else:
        results_urls = args.fetch_page

    if not results_urls:
        print("No results URLs found. Exiting.")
        sys.exit(1)

    if args.dry_run:
        print("\nDry run — would fetch these URLs:")
        for u in results_urls:
            print(f"  {u}")
        sys.exit(0)

    print(f"\nStep 2: Scraping {len(results_urls)} results pages...")
    for i, url in enumerate(results_urls, 1):
        slug = slug_from_url(url)
        print(f"  [{i}/{len(results_urls)}] {slug}")
        race = scrape_race_page(url, args.delay, args.cookie)
        if race is None:
            failed += 1
            continue
        if race["group_uuid"] in races:
            print(f"           Already known — skipping.")
        else:
            races[race["group_uuid"]] = race
            newly_found += 1
            print(f"           UUID: {race['group_uuid']}  ({race['race_name']})")

    print(f"\n{'='*60}")
    print(f"  New races discovered:  {newly_found}")
    print(f"  Total races in file:   {len(races)}")
    if failed:
        print(f"  Pages with no UUID:    {failed}  (may be JS-rendered or 403'd)")

    if races:
        save_races(races, output_path)
    else:
        print("No races to save.")


if __name__ == "__main__":
    main()
