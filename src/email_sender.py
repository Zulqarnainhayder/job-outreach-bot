"""Sends an outreach email with resume attached via Gmail SMTP."""

import mimetypes
import os
import smtplib
from email.message import EmailMessage

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def _attach_file(msg, path):
    ctype, _ = mimetypes.guess_type(path)
    maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
    with open(path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype=maintype,
            subtype=subtype,
            filename=os.path.basename(path),
        )


def build_message(from_email, to_email, subject, body, resume_path, extra_attachments=None, cc_email=None):
    """extra_attachments: optional list of additional file paths (e.g.
    reference letters, transcripts) attached alongside the resume."""
    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    if cc_email:
        msg["Cc"] = cc_email
    msg["Subject"] = subject
    msg.set_content(body)

    _attach_file(msg, resume_path)
    for path in extra_attachments or []:
        _attach_file(msg, path)

    return msg


def send_email(from_email, app_password, to_email, subject, body, resume_path, extra_attachments=None, cc_email=None):
    """Sends immediately. Caller is responsible for gating this behind a
    dry-run check — this function has no confirmation step of its own."""
    msg = build_message(from_email, to_email, subject, body, resume_path, extra_attachments, cc_email)
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(from_email, app_password)
        server.send_message(msg)
