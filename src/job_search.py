"""Finds real job postings matching the candidate's role/skills in a city,
by querying job board listing pages directly (StepStone, LinkedIn's guest
search, Xing) rather than going through a general search engine.

StepStone's job cards are plain server-rendered HTML (data-at="job-item"
attributes), so no JS execution is needed to read them. LinkedIn's guest
job search (no login) is also server-rendered and, unusually, exposes an
exact ISO `datetime` per posting — the most reliable "posted today" signal
of the three. Xing is server-rendered too, but the listing page has no
date at all; its `datePosted` only shows up in the JSON-LD on each job's
own detail page, so "posted today" filtering there costs one extra
request per candidate.

Indeed is not included — it returns 403 on plain requests (bot-walled).
"""

import datetime
import re
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

from search import USER_AGENT

BASE_URL = "https://www.stepstone.de"


def _slugify(text):
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return text.strip("-")


def search_stepstone_jobs(city, role, skills, max_results=25, delay=2.0, session=None):
    """Returns a list of {"title", "company", "location", "link", "query"}
    dicts, deduplicated by link, from StepStone job listings for the
    candidate's role and top skills in `city` — covering full-time roles as
    well as internships (Praktikum) and working-student (Werkstudent)
    postings, since the candidate is open to any of those."""
    session = session or requests.Session()
    queries = (
        [role, f"praktikum {role}", f"werkstudent {role}"]
        + [f"{skill} developer" for skill in skills[:3]]
    )

    seen_links = set()
    results = []
    for query in queries:
        url = f"{BASE_URL}/jobs/{_slugify(query)}/in-{_slugify(city)}"
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        for item in soup.find_all(attrs={"data-at": "job-item"}):
            title_el = item.find(attrs={"data-at": "job-item-title"})
            if not title_el:
                continue
            link_el = title_el if title_el.name == "a" else title_el.find_parent("a")
            href = link_el.get("href", "") if link_el else ""
            if not href:
                continue
            link = href if href.startswith("http") else f"{BASE_URL}{href}"
            if link in seen_links:
                continue
            seen_links.add(link)

            company_el = item.find(attrs={"data-at": "job-item-company-name"})
            location_el = item.find(attrs={"data-at": "job-item-location"})

            results.append({
                "title": title_el.get_text(strip=True),
                "company": company_el.get_text(strip=True) if company_el else "",
                "location": location_el.get_text(strip=True) if location_el else "",
                "link": link,
                "query": query,
            })
            if len(results) >= max_results:
                return results

        time.sleep(delay)

    return results


def search_stepstone_city_today(city, role, skills, max_results=50, delay=2.0, session=None):
    """Like search_stepstone_jobs, but scoped to `city` AND filtered to
    postings StepStone shows as from today (see
    search_stepstone_internships_today for why "vor X Stunden" is used as
    the "today" signal). Covers both Praktikum and Werkstudent postings."""
    session = session or requests.Session()
    queries = (
        [f"praktikum {role}", f"werkstudent {role}"]
        + [f"praktikum {skill}" for skill in skills[:3]]
        + [f"werkstudent {skill}" for skill in skills[:3]]
    )

    seen_links = set()
    results = []
    for query in queries:
        url = f"{BASE_URL}/jobs/{_slugify(query)}/in-{_slugify(city)}"
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        for item in soup.find_all(attrs={"data-at": "job-item"}):
            title_el = item.find(attrs={"data-at": "job-item-title"})
            if not title_el:
                continue
            link_el = title_el if title_el.name == "a" else title_el.find_parent("a")
            href = link_el.get("href", "") if link_el else ""
            if not href:
                continue
            link = href if href.startswith("http") else f"{BASE_URL}{href}"
            if link in seen_links:
                continue

            timeago_el = item.find(attrs={"data-at": "job-item-timeago"})
            timeago = timeago_el.get_text(strip=True) if timeago_el else ""
            if "Stunde" not in timeago:
                continue

            seen_links.add(link)
            company_el = item.find(attrs={"data-at": "job-item-company-name"})
            location_el = item.find(attrs={"data-at": "job-item-location"})

            results.append({
                "title": title_el.get_text(strip=True),
                "company": company_el.get_text(strip=True) if company_el else "",
                "location": location_el.get_text(strip=True) if location_el else "",
                "link": link,
                "source": "StepStone",
                "posted": timeago,
                "query": query,
            })
            if len(results) >= max_results:
                return results

        time.sleep(delay)

    return results


def search_linkedin_jobs_today(city, role, skills, max_results=75, delay=2.0, session=None):
    """LinkedIn's guest job search (no login required) for internship
    (Praktikum) and working-student (Werkstudent) postings in `city`,
    restricted server-side to the last 24h (f_TPR=r86400) and then
    strictly filtered in Python to postings whose <time datetime="..."> is
    today's date — LinkedIn is the only one of these sources that exposes
    an exact ISO date per listing rather than a fuzzy "vor X Tagen" label.
    """
    session = session or requests.Session()
    today = datetime.date.today().isoformat()
    queries = (
        [f"praktikum {role}", f"werkstudent {role}"]
        + [f"praktikum {skill}" for skill in skills[:3]]
        + [f"werkstudent {skill}" for skill in skills[:3]]
    )

    seen_links = set()
    results = []
    for query in queries:
        params = {
            "keywords": query,
            "location": city,
            "f_TPR": "r86400",
        }
        url = f"https://www.linkedin.com/jobs/search?{urllib.parse.urlencode(params)}"
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "lxml")
        for card in soup.find_all("div", class_="base-card"):
            link_el = card.find("a", class_="base-card__full-link", href=True)
            if not link_el:
                continue
            link = link_el["href"].split("?")[0]
            if link in seen_links:
                continue

            time_el = card.find("time")
            posted_date = time_el.get("datetime") if time_el else ""
            if posted_date != today:
                continue

            seen_links.add(link)
            title_el = card.find("h3", class_="base-search-card__title")
            company_el = card.find("h4", class_="base-search-card__subtitle")
            location_el = card.find("span", class_="job-search-card__location")

            results.append({
                "title": title_el.get_text(strip=True) if title_el else "",
                "company": company_el.get_text(strip=True) if company_el else "",
                "location": location_el.get_text(strip=True) if location_el else "",
                "link": link,
                "source": "LinkedIn",
                "posted": time_el.get_text(strip=True) if time_el else "",
                "query": query,
            })
            if len(results) >= max_results:
                return results

        time.sleep(delay)

    return results


def search_xing_jobs_today(city, role, skills, max_candidates=60, delay=1.5, session=None):
    """Xing job search for internship/working-student postings in `city`.
    The listing page carries no date at all, so this fetches each
    candidate's own job page (up to `max_candidates`, after dedup) to read
    its JSON-LD `datePosted` and keeps only postings from today."""
    session = session or requests.Session()
    today = datetime.date.today().isoformat()
    queries = (
        [f"praktikum {role}", f"werkstudent {role}"]
        + [f"praktikum {skill}" for skill in skills[:3]]
        + [f"werkstudent {skill}" for skill in skills[:3]]
    )

    candidates = {}  # link -> {title, company, location}
    for query in queries:
        params = {"keywords": query, "location": city}
        url = f"https://www.xing.com/jobs/search?{urllib.parse.urlencode(params)}"
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "lxml")
        for article in soup.find_all(attrs={"data-testid": "job-search-result"}):
            link_el = article.find("a", href=True)
            if not link_el:
                continue
            link = urllib.parse.urljoin("https://www.xing.com", link_el["href"])
            if link in candidates:
                continue

            title_el = article.find(attrs={"data-testid": "job-teaser-list-title"})
            company_el = article.find("p")

            candidates[link] = {
                "title": title_el.get_text(strip=True) if title_el else "",
                "company": company_el.get_text(strip=True) if company_el else "",
            }
            if len(candidates) >= max_candidates:
                break

        time.sleep(delay)
        if len(candidates) >= max_candidates:
            break

    date_posted_re = re.compile(r'"datePosted"\s*:\s*"(\d{4}-\d{2}-\d{2})')
    results = []
    for link, info in candidates.items():
        try:
            resp = session.get(link, headers={"User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        resp.encoding = "utf-8"

        match = date_posted_re.search(resp.text)
        if not match or match.group(1) != today:
            time.sleep(delay)
            continue

        results.append({
            "title": info["title"],
            "company": info["company"],
            "location": city,
            "link": link,
            "source": "Xing",
            "posted": "today",
            "query": "",
        })
        time.sleep(delay)

    return results


def search_stepstone_by_queries(city, queries, max_results=150, delay=1.5, session=None, today_only=False):
    """Like search_stepstone_jobs, but takes an explicit list of search
    terms instead of deriving them from role/skills — for a broad sweep
    across several unrelated role categories (e.g. frontend, QA, devops)
    at once. today_only=True applies the same "vor X Stunden" filter as
    search_stepstone_city_today; False (default) returns everything
    currently listed, no date filter."""
    session = session or requests.Session()

    seen_links = set()
    results = []
    for query in queries:
        url = f"{BASE_URL}/jobs/{_slugify(query)}/in-{_slugify(city)}"
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        for item in soup.find_all(attrs={"data-at": "job-item"}):
            title_el = item.find(attrs={"data-at": "job-item-title"})
            if not title_el:
                continue
            link_el = title_el if title_el.name == "a" else title_el.find_parent("a")
            href = link_el.get("href", "") if link_el else ""
            if not href:
                continue
            link = href if href.startswith("http") else f"{BASE_URL}{href}"
            if link in seen_links:
                continue

            timeago_el = item.find(attrs={"data-at": "job-item-timeago"})
            timeago = timeago_el.get_text(strip=True) if timeago_el else ""
            if today_only and "Stunde" not in timeago:
                continue

            seen_links.add(link)
            company_el = item.find(attrs={"data-at": "job-item-company-name"})
            location_el = item.find(attrs={"data-at": "job-item-location"})

            results.append({
                "title": title_el.get_text(strip=True),
                "company": company_el.get_text(strip=True) if company_el else "",
                "location": location_el.get_text(strip=True) if location_el else "",
                "link": link,
                "source": "StepStone",
                "posted": timeago,
                "query": query,
            })
            if len(results) >= max_results:
                return results

        time.sleep(delay)

    return results


def search_linkedin_by_queries(city, queries, max_results=200, delay=1.5, session=None, today_only=False):
    """Like search_linkedin_jobs_today, but takes an explicit query list.
    today_only=True restricts server-side to the last 24h (f_TPR=r86400)
    and keeps only postings whose exact <time datetime="..."> is today;
    False (default) returns everything currently listed, no date filter."""
    session = session or requests.Session()
    today = datetime.date.today().isoformat()

    seen_links = set()
    results = []
    for query in queries:
        params = {"keywords": query, "location": city}
        if today_only:
            params["f_TPR"] = "r86400"
        url = f"https://www.linkedin.com/jobs/search?{urllib.parse.urlencode(params)}"
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "lxml")
        for card in soup.find_all("div", class_="base-card"):
            link_el = card.find("a", class_="base-card__full-link", href=True)
            if not link_el:
                continue
            link = link_el["href"].split("?")[0]
            if link in seen_links:
                continue

            time_el = card.find("time")
            posted_date = time_el.get("datetime") if time_el else ""
            if today_only and posted_date != today:
                continue

            seen_links.add(link)
            title_el = card.find("h3", class_="base-search-card__title")
            company_el = card.find("h4", class_="base-search-card__subtitle")
            location_el = card.find("span", class_="job-search-card__location")

            results.append({
                "title": title_el.get_text(strip=True) if title_el else "",
                "company": company_el.get_text(strip=True) if company_el else "",
                "location": location_el.get_text(strip=True) if location_el else "",
                "link": link,
                "source": "LinkedIn",
                "posted": time_el.get_text(strip=True) if time_el else "",
                "query": query,
            })
            if len(results) >= max_results:
                return results

        time.sleep(delay)

    return results


def search_xing_by_queries(city, queries, max_results=150, delay=1.2, session=None, today_only=False, max_candidates=100):
    """Like search_xing_jobs_today, but takes an explicit query list.
    today_only=False (default) is a single cheap listing-page scrape per
    query with no date filter. today_only=True additionally fetches each
    candidate's own job page (up to max_candidates, after dedup) to read
    its JSON-LD datePosted and keeps only postings from today — one extra
    request per candidate, since Xing's listing page carries no date."""
    session = session or requests.Session()

    candidates = {}
    seen_links = set()
    results = []
    for query in queries:
        params = {"keywords": query, "location": city}
        url = f"https://www.xing.com/jobs/search?{urllib.parse.urlencode(params)}"
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "lxml")
        for article in soup.find_all(attrs={"data-testid": "job-search-result"}):
            link_el = article.find("a", href=True)
            if not link_el:
                continue
            link = urllib.parse.urljoin("https://www.xing.com", link_el["href"])

            title_el = article.find(attrs={"data-testid": "job-teaser-list-title"})
            company_el = article.find("p")
            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""

            if not today_only:
                if link in seen_links:
                    continue
                seen_links.add(link)
                results.append({
                    "title": title, "company": company, "location": city,
                    "link": link, "source": "Xing", "posted": "", "query": query,
                })
                if len(results) >= max_results:
                    return results
            elif link not in candidates:
                candidates[link] = {"title": title, "company": company}
                if len(candidates) >= max_candidates:
                    break

        time.sleep(delay)
        if today_only and len(candidates) >= max_candidates:
            break

    if not today_only:
        return results

    today = datetime.date.today().isoformat()
    date_posted_re = re.compile(r'"datePosted"\s*:\s*"(\d{4}-\d{2}-\d{2})')
    for link, info in candidates.items():
        try:
            resp = session.get(link, headers={"User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        resp.encoding = "utf-8"

        match = date_posted_re.search(resp.text)
        if not match or match.group(1) != today:
            time.sleep(delay)
            continue

        results.append({
            "title": info["title"], "company": info["company"], "location": city,
            "link": link, "source": "Xing", "posted": "today", "query": query,
        })
        time.sleep(delay)
        if len(results) >= max_results:
            return results

    return results


def search_stepstone_internships_today(role, skills, max_results=50, delay=2.0, session=None):
    """Nationwide (no city) search for internship/Praktikum postings
    matching the candidate's role and skills, filtered to ones StepStone
    shows as posted today.

    StepStone doesn't expose a calendar date, only a relative "timeago"
    string per job card ("vor 4 Stunden" / "vor 2 Tagen" / "vor 1 Woche").
    "Posted today" is approximated as anything shown in hours ("Stunde"),
    since day/week-scale postings are, by definition, not from today.

    Returns a list of {"title", "company", "location", "link", "timeago",
    "query"} dicts, deduplicated by link.
    """
    session = session or requests.Session()
    queries = [f"praktikum {role}"] + [f"praktikum {skill}" for skill in skills[:4]]

    seen_links = set()
    results = []
    for query in queries:
        url = f"{BASE_URL}/jobs/{_slugify(query)}"
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        for item in soup.find_all(attrs={"data-at": "job-item"}):
            title_el = item.find(attrs={"data-at": "job-item-title"})
            if not title_el:
                continue
            link_el = title_el if title_el.name == "a" else title_el.find_parent("a")
            href = link_el.get("href", "") if link_el else ""
            if not href:
                continue
            link = href if href.startswith("http") else f"{BASE_URL}{href}"
            if link in seen_links:
                continue

            timeago_el = item.find(attrs={"data-at": "job-item-timeago"})
            timeago = timeago_el.get_text(strip=True) if timeago_el else ""
            if "Stunde" not in timeago:
                continue  # posted more than a day ago — not "today"

            seen_links.add(link)
            company_el = item.find(attrs={"data-at": "job-item-company-name"})
            location_el = item.find(attrs={"data-at": "job-item-location"})

            results.append({
                "title": title_el.get_text(strip=True),
                "company": company_el.get_text(strip=True) if company_el else "",
                "location": location_el.get_text(strip=True) if location_el else "",
                "link": link,
                "timeago": timeago,
                "query": query,
            })
            if len(results) >= max_results:
                return results

        time.sleep(delay)

    return results
