"""Finds IT company websites for a given city by scraping DuckDuckGo's
HTML search results (https://html.duckduckgo.com/html/).

DuckDuckGo's HTML endpoint is used instead of Google: it needs no
JavaScript, has no EU cookie-consent interstitial (Google returns a
consent page instead of results for many EU IP addresses, which breaks
plain-requests scraping), and is far less aggressive about rate-limiting
or CAPTCHA-ing simple scripted requests.
"""

import re
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SEARCH_URL = "https://html.duckduckgo.com/html/"

QUERY_TEMPLATES = [
    "IT companies in {city}",
    "software companies in {city}",
    "tech companies in {city}",
    "IT Unternehmen {city}",
    "Softwareentwicklung {city}",
    "web development company {city}",
    "IT consulting {city}",
]

# Domains that show up in results but are never the company's own site.
DOMAIN_BLOCKLIST = {
    "google.com", "facebook.com", "linkedin.com", "twitter.com", "x.com",
    "instagram.com", "youtube.com", "wikipedia.org", "indeed.com",
    "glassdoor.com", "yelp.com", "clutch.co", "goodfirms.co", "crunchbase.com",
    "pinterest.com", "reddit.com", "medium.com", "yellowpages.com",
    "bbb.org", "duckduckgo.com", "kununu.com", "stepstone.de", "xing.com",
}


def _unwrap_ddg_url(href):
    """DuckDuckGo's HTML results wrap the real URL as
    //duckduckgo.com/l/?uddg=<encoded-real-url>&... — unwrap it."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc:
        qs = urllib.parse.parse_qs(parsed.query)
        real = qs.get("uddg", [None])[0]
        return urllib.parse.unquote(real) if real else None
    if href.startswith("http"):
        return href
    return None


def _domain_of(url):
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


# Generic "this is the homepage" boilerplate that search-result titles often
# lead with, in English and German — not part of the actual company name.
HOMEPAGE_BOILERPLATE = {
    "startseite", "home", "homepage", "willkommen", "welcome", "index",
}
LEGAL_SUFFIX_RE = re.compile(
    r"\b(gmbh|ag|kg|ug|e\.?k\.?|inc|llc|ltd|corp|co\.?|gbr|se)\b", re.IGNORECASE
)


def _clean_company_name(title, domain):
    """Search-result titles are often 'Startseite - Real Company Name' or
    'Real Company Name | Some Slogan | City' — strip the boilerplate and
    keep the part that looks like an actual company name. Prefer a segment
    with a legal-entity suffix (GmbH, Ltd, Inc, ...) when one is present;
    otherwise fall back to the first remaining segment, since search
    engines conventionally lead with the entity name."""
    segments = [s.strip() for s in re.split(r"\s[-|–]\s", title) if s.strip()]
    segments = [s for s in segments if s.lower() not in HOMEPAGE_BOILERPLATE]

    if not segments:
        return domain

    for segment in segments:
        if LEGAL_SUFFIX_RE.search(segment):
            return segment[:60].strip()

    return segments[0][:60].strip() or domain


def _is_bot_challenge(resp):
    """DuckDuckGo replies with HTTP 202 and an anomaly.js challenge page
    instead of results when it suspects automated traffic (e.g. after many
    rapid requests). Detect it so callers can back off and retry rather
    than silently treating it as zero results."""
    return resp.status_code == 202 or "anomaly.js" in resp.text[:3000]


def _post_with_backoff(session, query, offset, max_retries=3):
    """POSTs the search request, retrying with exponential backoff if
    DuckDuckGo's bot-detection challenge is hit. Returns the response, or
    None if every retry was also challenged/failed."""
    for attempt in range(max_retries + 1):
        try:
            resp = session.post(
                SEARCH_URL,
                data={"q": query, "s": str(offset), "kl": "wt-wt"},
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
        except requests.RequestException:
            return None

        if not _is_bot_challenge(resp):
            return resp if resp.status_code == 200 else None

        if attempt < max_retries:
            time.sleep(5 * (2 ** attempt))  # 5s, 10s, 20s

    return None


def search_companies(city, max_results=30, pages_per_query=3, delay=2.0):
    """Scrapes DuckDuckGo for IT companies in `city`. Returns a list of
    {"name": str, "website": str} dicts, deduplicated by domain.
    """
    seen_domains = set()
    results = []
    session = requests.Session()

    for query in QUERY_TEMPLATES:
        offset = 0
        for page in range(pages_per_query):
            if len(results) >= max_results:
                return results

            resp = _post_with_backoff(session, query.format(city=city), offset)
            if resp is None:
                break

            soup = BeautifulSoup(resp.text, "lxml")
            links = soup.select("a.result__a")
            if not links:
                break

            for a in links:
                url = _unwrap_ddg_url(a.get("href", ""))
                if not url:
                    continue
                domain = _domain_of(url)
                if not domain or domain in DOMAIN_BLOCKLIST or domain in seen_domains:
                    continue

                raw_title = a.get_text(strip=True) or domain
                name = _clean_company_name(raw_title, domain)
                seen_domains.add(domain)
                results.append({"name": name, "website": f"https://{domain}"})
                if len(results) >= max_results:
                    return results

            offset += 30
            time.sleep(delay)

    return results
