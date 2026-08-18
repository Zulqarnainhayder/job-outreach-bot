"""Builds a personalized outreach email for one company from a template.

No paid API required. Personalization comes from a short blurb scraped from
the company's own homepage (see email_finder._extract_blurb) — when no
blurb was found, falls back to a more generic opener.

Wording is kept short and plain on purpose — simple sentences, everyday
words, no corporate filler ("I'd welcome the opportunity to..."), so the
email reads like something a person typed in five minutes, not a
generated form letter.
"""

# Common non-English stopwords/characters — a rough signal that a scraped
# blurb isn't in English, so we don't drop foreign-language text into an
# English sentence.
NON_ENGLISH_MARKERS = (
    "ä", "ö", "ü", "ß", "é", "è", "ê", "ñ", "ç",
    " und ", " für ", " mit ", " der ", " die ", " das ", " ihr ", " ist ",
)


def _looks_english(text):
    lowered = f" {text.lower()} "
    return not any(marker in lowered for marker in NON_ENGLISH_MARKERS)


OPENER_WITH_BLURB = "I saw that you work on {blurb} — that's the kind of work I'd like to do."
OPENER_GENERIC = "I've been looking at IT companies nearby and wanted to reach out."

# Plain-language description of what I actually do, so the email isn't
# pinned to one narrow job title — kept broad on purpose since I'm open to
# full stack, frontend-only, DevOps, or QA/testing work, not just "Full
# Stack Developer" roles specifically.
WHAT_I_DO_LINE = (
    "I build software — mostly full stack web apps, but I'm just as happy "
    "doing frontend-only work, DevOps/cloud stuff, or QA and testing. "
    "I like the variety and I'm not fixed on one narrow lane."
)

# Stated plainly once, so it's clear I'm open to more than one type of role.
AVAILABILITY_LINE = (
    "I'm looking for work right now — full-time, part-time, working student, "
    "or an internship (paid or unpaid) would all work for me, so happy to fit "
    "whatever you need."
)

TEMPLATE = """Hi {company_name} team,

My name is {name}. {opener}

{what_i_do_line}

{summary}{university_clause}

{availability_line}

I've attached my resume. Happy to talk whenever suits you.

Thanks,
{name}"""


def compose_email(candidate, company):
    """candidate: dict with name, target_role, skills, summary, and
    optionally university. `skills` isn't quoted directly in the email body
    (it's already in `summary`, and quoting it twice reads redundant) — it's
    used elsewhere for relevance scoring.
    company: dict with name, website, and optionally blurb (from email_finder).
    Returns {"subject": str, "body": str}.
    """
    blurb = (company.get("blurb") or "").strip()
    if blurb and not _looks_english(blurb):
        blurb = ""
    if blurb:
        opener = OPENER_WITH_BLURB.format(blurb=blurb.rstrip("."))
    else:
        opener = OPENER_GENERIC

    university = (candidate.get("university") or "").strip()
    university_clause = f" I'm also part of {university}." if university else ""

    body = TEMPLATE.format(
        company_name=company["name"],
        opener=opener,
        name=candidate["name"],
        what_i_do_line=WHAT_I_DO_LINE,
        university_clause=university_clause,
        summary=candidate.get("summary", ""),
        availability_line=AVAILABILITY_LINE,
    )

    subject = f"Looking for work at {company['name']}"

    return {"subject": subject, "body": body}


APPLICATION_TEMPLATE = """Hi {company_name} team,

My name is {name}, a {target_role}. I saw your posting for "{job_title}" and wanted to apply.

{summary}{university_clause}

I've attached my resume. Happy to talk whenever suits you.

Thanks,
{name}"""


def compose_application_email(candidate, company, job_title):
    """Like compose_email, but for a specific named opening the candidate
    found on a job board (rather than a cold-outreach guess that the
    company might be hiring) — references the exact posting title instead
    of a generic/blurb-based opener.
    candidate: dict with name, target_role, summary, and optionally university.
    company: dict with name.
    job_title: the exact posting title, e.g. "Werkstudent Software Engineering".
    Returns {"subject": str, "body": str}.
    """
    university = (candidate.get("university") or "").strip()
    university_clause = f" I'm also part of {university}." if university else ""

    body = APPLICATION_TEMPLATE.format(
        company_name=company["name"],
        job_title=job_title,
        name=candidate["name"],
        target_role=candidate["target_role"],
        university_clause=university_clause,
        summary=candidate.get("summary", ""),
    )

    subject = f"Application for {job_title} at {company['name']}"

    return {"subject": subject, "body": body}
