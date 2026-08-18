"""Scrapes Gelbe Seiten (gelbeseiten.de, the German "Yellow Pages") for IT
companies in a city.

For small German towns, general web search (DuckDuckGo/Google) mostly
surfaces directory/aggregator pages rather than individual companies —
those companies exist, they just don't rank well for broad queries. Gelbe
Seiten's own category listings are a much richer source in that case:
each listing includes the company's real website, base64-encoded in a
`data-webseitelink` attribute (simple obfuscation, not real protection).
"""

import base64
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

from search import USER_AGENT

SEARCH_URL = "https://www.gelbeseiten.de/Suche/{category}/{city}"

# Category terms that cover IT/software companies on Gelbe Seiten. Each is
# a separate search — the site doesn't support OR'd categories.
CATEGORIES = [
    "IT-Dienstleister",
    "Softwareentwicklung",
    "EDV-Beratung",
    "Systemhaus",
    "Computer",
    "Webdesign",
]


def _domain_of(url):
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def _decode_website(article):
    span = article.find(attrs={"data-webseitelink": True})
    if not span:
        return None
    try:
        decoded = base64.b64decode(span["data-webseitelink"]).decode()
    except Exception:
        return None
    return decoded if decoded.startswith("http") else f"https://{decoded}"


def search_gelbe_seiten(city, categories=None, delay=1.5, session=None):
    """Returns a list of {"name": str, "website": str} dicts, deduplicated
    by domain, scraped from Gelbe Seiten category listings for `city`."""
    session = session or requests.Session()
    categories = categories or CATEGORIES

    seen_domains = set()
    results = []
    for category in categories:
        url = SEARCH_URL.format(
            category=urllib.parse.quote(category), city=urllib.parse.quote(city)
        )
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        for article in soup.find_all("article", class_="mod-Treffer"):
            heading = article.find(["h2", "h3"])
            name = heading.get_text(strip=True) if heading else None
            website = _decode_website(article)
            if not (name and website):
                continue

            domain = _domain_of(website)
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)

            # "98693 Ilmenau" — postal code + city, the actual registered
            # address. Gelbe Seiten's radius search includes nearby towns,
            # so this is what tells a real Ilmenau listing apart from one
            # picked up from a neighboring town.
            ort_span = article.find("span", class_="mod-AdresseKompakt__adress__ort")
            location = ort_span.get_text(strip=True) if ort_span else None

            results.append({"name": name, "website": f"https://{domain}", "location": location})

        time.sleep(delay)

    return results


def find_company_website(name, city, session=None):
    """Looks up one specific company by name (not category) on Gelbe
    Seiten, e.g. to backfill a company that was only discovered via a job
    posting (no email lookup done for it yet). Gelbe Seiten's search URL
    takes any free-text query in the category slot, not just a category
    name. Returns {"website": str, "location": str | None} or None."""
    session = session or requests.Session()
    url = SEARCH_URL.format(category=urllib.parse.quote(name), city=urllib.parse.quote(city))
    try:
        resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    article = soup.find("article", class_="mod-Treffer")
    if not article:
        return None

    website = _decode_website(article)
    if not website:
        return None
    domain = _domain_of(website)

    ort_span = article.find("span", class_="mod-AdresseKompakt__adress__ort")
    location = ort_span.get_text(strip=True) if ort_span else None

    return {"website": f"https://{domain}", "location": location}
