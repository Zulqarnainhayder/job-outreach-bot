"""Crawls a company's own website to find an official contact email."""

import codecs
import re
import urllib.parse

import requests
from bs4 import BeautifulSoup

from search import USER_AGENT

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

# Local-parts that indicate a generic/junk address rather than a real inbox.
JUNK_LOCAL_PARTS = {
    "example", "test", "sentry", "wixpress", "godaddy", "yourname",
    "noreply", "no-reply", "donotreply", "postmaster", "webmaster",
}
JUNK_DOMAIN_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")

# Whole domains reserved for documentation/placeholder use (RFC 2606) or
# otherwise never a real company inbox — e.g. a template CMS theme left
# "m.muster@example.com" (German placeholder name) in a site's markup.
JUNK_DOMAINS = {
    "example.com", "example.org", "example.net", "example.edu",
    "test.com", "yourdomain.com", "yourdomain.de", "domain.com",
    "email.com", "sample.com", "mustermann.de",
}

# Local-part keywords, in priority order — first match wins.
PRIORITY_KEYWORDS = [
    ["hr", "careers", "career", "jobs", "recruiting", "recruitment", "talent"],
    ["info", "contact", "hello", "hi"],
]

CANDIDATE_PATHS = ["", "/contact", "/contact-us", "/about", "/about-us", "/careers", "/jobs"]


def _is_junk(email):
    local, _, domain = email.partition("@")
    domain = domain.lower()
    if local.lower() in JUNK_LOCAL_PARTS:
        return True
    if domain in JUNK_DOMAINS:
        return True
    if domain.endswith(JUNK_DOMAIN_SUFFIXES):
        return True
    return False


def _clean_email(email):
    """Strips trailing sentence punctuation the regex can't distinguish
    from a real domain suffix (e.g. "...contact support@zwei.gmbh." picks
    up the sentence-ending period — genuine TLDs like .gmbh make a
    length/shape check unreliable, so just strip trailing punctuation)."""
    return email.strip().rstrip(".,;:)]}")


# Some sites obfuscate even their mailto: href itself, not just the
# visible text (e.g. href="mailto:info AT example DOT com"), as a crude
# anti-scraping measure. Safe to de-obfuscate specifically within a mailto
# href — unlike scanning arbitrary body text for "at"/"dot", there's no
# ambiguity here: everything after "mailto:" is meant to be an address.
_AT_RE = re.compile(r"\s*[\(\[]?\s*at\s*[\)\]]?\s*", re.IGNORECASE)
_DOT_RE = re.compile(r"\s*[\(\[]?\s*dot\s*[\)\]]?\s*", re.IGNORECASE)


def _deobfuscate_mailto(addr):
    if "@" in addr:
        return addr
    addr = _AT_RE.sub("@", addr, count=1)
    addr = _DOT_RE.sub(".", addr)
    return addr


def _extract_emails(html):
    soup = BeautifulSoup(html, "lxml")
    found = set()

    for a in soup.select("a[href^='mailto:']"):
        addr = a["href"][len("mailto:"):].split("?")[0].strip()
        addr = _deobfuscate_mailto(addr)
        if addr:
            found.add(_clean_email(addr))

    for match in EMAIL_RE.findall(soup.get_text(" ")):
        found.add(_clean_email(match))

    return {e for e in found if e and not _is_junk(e)}


def _rank_email(email, domain):
    """Lower score = better. Emails on the company's own domain rank first,
    then by keyword priority, then alphabetically as a tiebreaker."""
    local = email.split("@")[0].lower()
    same_domain = domain not in email.lower()

    keyword_rank = len(PRIORITY_KEYWORDS)
    for i, keywords in enumerate(PRIORITY_KEYWORDS):
        if any(kw in local for kw in keywords):
            keyword_rank = i
            break

    return (same_domain, keyword_rank, email)


def _extract_blurb(html):
    """Pulls a short, real description of the company from its own homepage
    (meta description, og:description, or the first substantial paragraph)
    so outreach emails can reference something specific for free, with no
    LLM call. Returns None if nothing usable was found."""
    soup = BeautifulSoup(html, "lxml")

    for attrs in ({"name": "description"}, {"property": "og:description"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            text = " ".join(tag["content"].split())
            if len(text) > 20:
                return text[:220]

    for p in soup.find_all("p"):
        text = " ".join(p.get_text(" ", strip=True).split())
        if len(text) > 60:
            return text[:220]

    return None


def find_official_email(website, session=None, timeout=10):
    """Crawls a handful of likely pages on `website` and returns the best
    candidate email plus a short blurb about the company scraped from its
    homepage, or None if no email was found.

    Returns {"email": str, "source_page": str, "blurb": str | None,
    "page_text": str} or None. `page_text` is the combined visible text of
    every page crawled — reusable for skill-relevance scoring without a
    second round of requests.
    """
    session = session or requests.Session()
    domain = urllib.parse.urlparse(website).netloc.lower().removeprefix("www.")

    all_emails = {}  # email -> source page url
    blurb = None
    page_text_parts = []
    for path in CANDIDATE_PATHS:
        url = website.rstrip("/") + path
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        page_text_parts.append(soup.get_text(" "))

        if path == "" and blurb is None:
            blurb = _extract_blurb(resp.text)

        for email in _extract_emails(resp.text):
            all_emails.setdefault(email, url)

        # Once we have a same-domain HR/careers address, no need to keep crawling.
        best_so_far = min(all_emails, key=lambda e: _rank_email(e, domain), default=None)
        if best_so_far and _rank_email(best_so_far, domain)[:2] == (False, 0):
            break

    if not all_emails:
        return None

    # Some sites ROT13-cipher the email in the mailto: href itself (e.g.
    # "vasb@xhamr-zrqvra.qr" — ciphertext, not a typo). ROT13 only shifts
    # letters, so "@" and "." pass through unchanged and the result still
    # looks like a syntactically valid address, which is why it survives
    # extraction. Detect it by checking whether decoding recovers the
    # site's own domain; if so, add the decoded form as a candidate too —
    # ranking then prefers it automatically since it's same-domain.
    for email in list(all_emails):
        if domain and domain in email.lower():
            continue
        decoded = codecs.encode(email, "rot13")
        if domain and domain in decoded.lower():
            all_emails.setdefault(decoded, all_emails[email])

    best = min(all_emails, key=lambda e: _rank_email(e, domain))
    return {
        "email": best,
        "source_page": all_emails[best],
        "blurb": blurb,
        "page_text": " ".join(page_text_parts),
    }
