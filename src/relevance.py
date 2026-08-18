"""Scores how relevant a company's own homepage text is to the candidate's
skills, so results can be ranked/filtered by fit rather than treated as a
flat list."""

import re

TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.#]*")


def _tokenize(text):
    return {t.lower() for t in TOKEN_RE.findall(text)}


def score_relevance(page_text, skills):
    """page_text: raw text scraped from the company's site (e.g. homepage).
    skills: candidate's skill list, e.g. ["Python", "Django", "React"].

    Returns (score: int, matched_skills: list[str]) — score is simply the
    count of distinct candidate skills that appear on the page, matched_skills
    preserves the candidate's original casing for display.
    """
    if not page_text or not skills:
        return 0, []

    page_tokens = _tokenize(page_text)
    matched = [skill for skill in skills if skill.lower() in page_tokens]
    return len(matched), matched
