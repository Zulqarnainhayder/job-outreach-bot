"""Orchestrates the full pipeline: find companies in a city, find their
official email, draft a personalized outreach email, and (optionally) send
it with your resume attached.

Safe by default: this script only ever WRITES a CSV of drafts. It never
sends an email unless you pass --send. Review the CSV first.

Usage:
    python src/main.py --city "Lahore"                # dry run -> CSV of drafts
    python src/main.py --city "Lahore" --send          # actually sends emails
"""

import argparse
import csv
import datetime
import os
import re
import time
import urllib.parse

import requests
from dotenv import load_dotenv

from search import search_companies
from directory_scraper import search_gelbe_seiten, find_company_website
from email_finder import find_official_email
from email_composer import compose_email
from pdf_report import generate_pdf
from email_sender import send_email
from relevance import score_relevance
from job_search import search_stepstone_jobs

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# German directories/job boards list cities by their German name, which
# sometimes differs entirely from the English exonym (not just accents) —
# "Munich" vs "München", "Cologne" vs "Köln". Comparing the raw English
# input against a scraped "München" address would silently reject every
# genuine match. Map the common cases both ways.
CITY_EXONYMS = {
    "munich": "münchen", "cologne": "köln", "nuremberg": "nürnberg",
    "hanover": "hannover", "brunswick": "braunschweig", "cassel": "kassel",
}
CITY_EXONYMS.update({v: k for k, v in CITY_EXONYMS.items()})


def _city_name_variants(city):
    """Lowercase city name plus its English<->German exonym, if any."""
    city_lower = city.lower()
    variants = {city_lower}
    if city_lower in CITY_EXONYMS:
        variants.add(CITY_EXONYMS[city_lower])
    return variants


def _text_mentions_city(text, city_variants):
    text_lower = text.lower()
    return any(variant in text_lower for variant in city_variants)
SENT_LOG_PATH = os.path.join(DATA_DIR, "sent_log.csv")


def load_contacted_domains():
    if not os.path.exists(SENT_LOG_PATH):
        return set()
    with open(SENT_LOG_PATH, newline="", encoding="utf-8") as f:
        return {row["domain"] for row in csv.DictReader(f)}


def record_sent(domain, email):
    is_new = not os.path.exists(SENT_LOG_PATH)
    with open(SENT_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["domain", "email", "sent_at"])
        if is_new:
            writer.writeheader()
        writer.writerow({
            "domain": domain,
            "email": email,
            "sent_at": datetime.datetime.now().isoformat(timespec="seconds"),
        })


def load_candidate_profile():
    required = ["YOUR_NAME", "TARGET_ROLE", "YOUR_SKILLS", "RESUME_PATH"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            f"Missing required .env values: {', '.join(missing)}. "
            "Copy .env.example to .env and fill it in."
        )
    return {
        "name": os.environ["YOUR_NAME"],
        "target_role": os.environ["TARGET_ROLE"],
        "skills": [s.strip() for s in os.environ["YOUR_SKILLS"].split(",")],
        "summary": os.environ.get("YOUR_SUMMARY", ""),
        "university": os.environ.get("YOUR_UNIVERSITY", ""),
    }, os.environ["RESUME_PATH"]


_LEGAL_SUFFIX_RE = re.compile(
    r"\b(gmbh|ag|kg|ug|e\.?k\.?|inc|llc|ltd|corp|co\.?|gbr|se|mbh)\b", re.IGNORECASE
)


def _normalize_company_name(name):
    name = _LEGAL_SUFFIX_RE.sub("", name.lower())
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def _names_match(a, b):
    """Company names rarely match exactly between sources (our search list
    says "INVERSO GmbH", StepStone says "INVERSO Gesellschaft fuer
    innovative Versicherungssoftware mbH") — match on whether either
    name's first significant word appears in the other."""
    na, nb = _normalize_company_name(a), _normalize_company_name(b)
    if not na or not nb:
        return False
    first_a, first_b = na.split()[0], nb.split()[0]
    return first_a == first_b or first_a in nb or first_b in na


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--city", required=True, help="City to search for IT companies in")
    parser.add_argument("--max-companies", type=int, default=20)
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between outbound requests")
    parser.add_argument("--send", action="store_true", help="Actually send emails (default: dry run only)")
    args = parser.parse_args()

    candidate, resume_path = load_candidate_profile()
    if not os.path.exists(resume_path):
        raise SystemExit(f"RESUME_PATH does not exist: {resume_path}")

    from_email = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if args.send and not (from_email and app_password):
        raise SystemExit("--send requires GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env")

    os.makedirs(DATA_DIR, exist_ok=True)
    contacted = load_contacted_domains()

    print(f"Searching for IT companies in {args.city}...")
    companies = search_companies(args.city, max_results=args.max_companies, delay=args.delay)

    print(f"Checking Gelbe Seiten directory listings for {args.city}...")
    seen_domains = {urllib.parse.urlparse(c["website"]).netloc.lower().removeprefix("www.") for c in companies}
    for company in search_gelbe_seiten(args.city, delay=max(args.delay, 1.5)):
        domain = urllib.parse.urlparse(company["website"]).netloc.lower().removeprefix("www.")
        if domain not in seen_domains:
            seen_domains.add(domain)
            companies.append(company)

    print(f"Found {len(companies)} candidate companies.")

    session = requests.Session()
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(DATA_DIR, f"outreach_{args.city.replace(' ', '_')}_{timestamp}.csv")
    city_variants = _city_name_variants(args.city)

    rows = []
    for company in companies:
        domain = urllib.parse.urlparse(company["website"]).netloc.lower().removeprefix("www.")
        if domain in contacted:
            print(f"  skip (already contacted): {company['name']}")
            continue

        print(f"  finding email for {company['name']} ({company['website']})...")
        found = find_official_email(company["website"], session=session)
        time.sleep(args.delay)
        if not found:
            print("    no email found, skipping")
            continue

        # Gelbe Seiten's radius search includes nearby towns, and a DDG hit
        # for "IT companies in {city}" doesn't guarantee the company is
        # actually based there. Trust the scraped Gelbe Seiten address when
        # we have one; otherwise fall back to checking the company's own
        # site text for the city name.
        if company.get("location") is not None:
            actually_in_city = _text_mentions_city(company["location"], city_variants)
        else:
            actually_in_city = _text_mentions_city(found.get("page_text", ""), city_variants)
        if not actually_in_city:
            print(f"    not actually based in {args.city} (location: {company.get('location') or 'unconfirmed'}), skipping")
            continue

        print(f"    found {found['email']}, drafting email...")
        draft = compose_email(candidate, {**company, "blurb": found.get("blurb")})

        relevance_score, matched_skills = score_relevance(found.get("page_text", ""), candidate["skills"])

        status = "dry-run (not sent)"
        if args.send:
            try:
                send_email(from_email, app_password, found["email"], draft["subject"], draft["body"], resume_path)
                record_sent(domain, found["email"])
                contacted.add(domain)
                status = "sent"
                print(f"    sent to {found['email']}")
            except Exception as e:
                status = f"failed: {e}"
                print(f"    FAILED to send: {e}")
            time.sleep(args.delay)

        rows.append({
            "company": company["name"],
            "website": company["website"],
            "email": found["email"],
            "source_page": found["source_page"],
            "subject": draft["subject"],
            "body": draft["body"],
            "status": status,
            "relevance_score": relevance_score,
            "matched_skills": ", ".join(matched_skills),
        })

    print(f"\nSearching StepStone for {candidate['target_role']}/skill-matching jobs in {args.city}...")
    all_jobs = search_stepstone_jobs(args.city, candidate["target_role"], candidate["skills"], delay=args.delay)
    # StepStone's own radius search returns postings across nearby cities
    # and remote/bundesweit roles too — keep only ones actually in this city.
    jobs = [j for j in all_jobs if _text_mentions_city(j["location"], city_variants)]
    print(f"Found {len(all_jobs)} matching job postings, {len(jobs)} of them actually in {args.city}.")

    jobs_csv_path = os.path.join(DATA_DIR, f"jobs_{args.city.replace(' ', '_')}_{timestamp}.csv")
    with open(jobs_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["title", "company", "location", "link", "query"])
        writer.writeheader()
        writer.writerows(jobs or [{"title": "N/A", "company": "N/A", "location": "N/A", "link": "N/A", "query": "N/A"}])
    print(f"Wrote {len(jobs)} job postings to {jobs_csv_path}")

    # Fold job openings into the company table as a column, instead of a
    # separate section: attach matching job title(s) to existing company
    # rows by fuzzy name match, and add a row for any job whose company
    # isn't already in the outreach list (found via job search only, no
    # email lookup done for it yet).
    matched_job_indices = set()
    for row in rows:
        titles = []
        for i, job in enumerate(jobs):
            if _names_match(row["company"], job["company"]):
                titles.append(job["title"])
                matched_job_indices.add(i)
        row["job_opening"] = ", ".join(titles)

    # Group remaining postings by company name first — several openings
    # from the same employer (e.g. one company with 4 open roles) must
    # become one row with all titles joined, not one duplicate row per
    # posting each re-triggering the same website/email lookup.
    unmatched_by_company = {}
    for i, job in enumerate(jobs):
        if i in matched_job_indices:
            continue
        unmatched_by_company.setdefault(job["company"], []).append(job["title"])

    for company_name, titles in unmatched_by_company.items():
        # Company only surfaced via job search — look up its real site and
        # email the same way we do for search_companies/Gelbe Seiten hits,
        # rather than leaving it as an unusable "N/A" row.
        print(f"  looking up website for {company_name} (found via job posting only)...")
        lookup = find_company_website(company_name, args.city, session=session)
        time.sleep(args.delay)

        found = None
        if lookup:
            found = find_official_email(lookup["website"], session=session)
            time.sleep(args.delay)

        job_opening = ", ".join(titles)
        if found:
            relevance_score, matched_skills = score_relevance(found.get("page_text", ""), candidate["skills"])
            draft = compose_email(candidate, {"name": company_name, "website": lookup["website"], "blurb": found.get("blurb")})
            print(f"    found {found['email']}")
            rows.append({
                "company": company_name,
                "website": lookup["website"],
                "email": found["email"],
                "source_page": found["source_page"],
                "subject": draft["subject"],
                "body": draft["body"],
                "status": "dry-run (not sent)",
                "relevance_score": relevance_score,
                "matched_skills": ", ".join(matched_skills),
                "job_opening": job_opening,
            })
        else:
            print("    no email found, listing job only")
            rows.append({
                "company": company_name,
                "website": lookup["website"] if lookup else next(j["link"] for j in jobs if j["company"] == company_name),
                "email": "N/A (no email found)",
                "source_page": "",
                "subject": "",
                "body": "",
                "status": "N/A",
                "relevance_score": 0,
                "matched_skills": "",
                "job_opening": job_opening,
            })

    # Most relevant companies (by matched skills) first.
    rows.sort(key=lambda r: r["relevance_score"], reverse=True)

    if not rows:
        rows = [{
            "company": "N/A", "website": "N/A", "email": "N/A", "source_page": "N/A",
            "subject": "N/A", "body": "N/A", "status": "N/A",
            "relevance_score": 0, "matched_skills": "N/A", "job_opening": "N/A",
        }]

    fieldnames = ["company", "website", "email", "source_page", "subject", "body", "status", "relevance_score", "matched_skills", "job_opening"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {out_path}")
    if not args.send:
        print("This was a DRY RUN — no emails were sent. Review the CSV/PDF, then rerun with --send.")

    pdf_path = out_path.rsplit(".", 1)[0] + ".pdf"
    generate_pdf(args.city, rows, pdf_path)
    print(f"Wrote combined PDF (companies + job openings) to {pdf_path}")


if __name__ == "__main__":
    main()
