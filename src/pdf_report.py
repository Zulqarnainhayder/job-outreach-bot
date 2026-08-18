"""Generates a single-table PDF report: every company (with official email
+ skill relevance) plus, in its own column, any matching job opening found
for that company -- a human-friendly companion to the CSV written by
main.py."""

from fpdf import FPDF


# Common unicode punctuation/whitespace that isn't in latin-1 but has an
# obvious ASCII equivalent -- normalize these before falling back to
# "replace" for anything else (e.g. "ᐅ"), so scraped job/company
# titles don't fill up with "?". Written as \uXXXX escapes rather than
# literal characters so the source stays unambiguous byte-for-byte.
_PUNCTUATION_MAP = {
    "–": "-", "—": "-", "‑": "-",  # en dash, em dash, non-breaking hyphen
    "‘": "'", "’": "'",                 # curly single quotes
    "“": '"', "”": '"',                 # curly double quotes
    "…": "...",                              # ellipsis
    " ": " ", " ": " ", " ": " ",  # non-breaking / narrow / thin space
    "​": "", "­": "",                   # zero-width space, soft hyphen
}


def _safe(text):
    """PDF core fonts only support latin-1; scraped titles can contain
    arbitrary unicode. Normalize common punctuation/whitespace to its ASCII
    equivalent, then replace anything still unencodable rather than crash."""
    text = text or ""
    for char, replacement in _PUNCTUATION_MAP.items():
        text = text.replace(char, replacement)
    return text.encode("latin-1", "replace").decode("latin-1")


class OutreachPDF(FPDF):
    title_text = ""
    subtitle_text = ""

    def header(self):
        self.set_font("helvetica", "B", 14)
        self.cell(0, 10, _safe(self.title_text), ln=1)
        self.set_font("helvetica", size=9)
        self.set_text_color(110)
        self.cell(0, 6, _safe(self.subtitle_text), ln=1)
        self.set_text_color(0)
        self.ln(2)

    def footer(self):
        self.set_y(-15)
        self.set_font("helvetica", size=8)
        self.set_text_color(150)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def generate_pdf(city, rows, output_path):
    """rows: dicts with company, email, website, matched_skills, status,
    and job_opening (the matching job title for that company, or "" if
    none was found) -- built by main.py, which merges the company list and
    job-search results before calling this."""
    pdf = OutreachPDF(orientation="L", unit="mm", format="A4")
    pdf.title_text = f"IT Company Outreach - {city}"
    pdf.subtitle_text = f"{len(rows)} companies, with matching job openings where found"
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("helvetica", size=10)

    col_widths = (55, 48, 48, 30, 68, 27)
    with pdf.table(col_widths=col_widths, text_align="LEFT", line_height=6) as table:
        header_row = table.row()
        for heading in ("Company", "Email", "Website", "Matched Skills", "Job Opening", "Status"):
            header_row.cell(_safe(heading))

        for row in rows:
            table_row = table.row()
            table_row.cell(_safe(row["company"]))
            table_row.cell(_safe(row["email"]))
            table_row.cell(_safe(row["website"]), link=row["website"] if row["website"].startswith("http") else None)
            table_row.cell(_safe(row.get("matched_skills", "")))
            table_row.cell(_safe(row.get("job_opening", "")))
            table_row.cell(_safe(row["status"]))

    pdf.output(output_path)


def generate_jobs_pdf(city, rows, output_path, subtitle=None):
    """rows: dicts with title, company, location, link, source, posted
    (and optionally query) -- the shape written by job_search.py's
    search_*_today functions, not the outreach company table above."""
    pdf = OutreachPDF(orientation="L", unit="mm", format="A4")
    pdf.title_text = f"Job Openings - {city}"
    pdf.subtitle_text = subtitle or f"{len(rows)} postings"
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("helvetica", size=10)

    col_widths = (85, 55, 40, 22, 22, 63)
    with pdf.table(col_widths=col_widths, text_align="LEFT", line_height=6) as table:
        header_row = table.row()
        for heading in ("Title", "Company", "Location", "Source", "Posted", "Link"):
            header_row.cell(_safe(heading))

        for row in rows:
            table_row = table.row()
            table_row.cell(_safe(row["title"]))
            table_row.cell(_safe(row["company"]))
            table_row.cell(_safe(row["location"]))
            table_row.cell(_safe(row.get("source", "")))
            table_row.cell(_safe(row.get("posted", "")))
            link = row["link"]
            table_row.cell(_safe(link), link=link if link.startswith("http") else None)

    pdf.output(output_path)
