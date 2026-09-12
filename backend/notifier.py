"""
Email notifications for shift changes detected during calendar sync.
Sends a summary email whenever a shift is added, modified (time changed
on an already-booked day), or removed, so you're not surprised by a
schedule change without noticing.
"""
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")          # e.g. cosminxgl@gmail.com
SMTP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD")  # Gmail App Password, NOT your normal password
NOTIFY_TO = os.environ.get("NOTIFY_EMAIL", SMTP_USER)  # where alerts go, defaults to sender


def _format_dt(iso_str):
    dt = datetime.fromisoformat(iso_str)
    return dt.strftime("%a %d %b, %H:%M")


def _build_body(changes):
    lines = []
    added = [c for c in changes if c["type"] == "added"]
    modified = [c for c in changes if c["type"] == "modified"]
    deleted = [c for c in changes if c["type"] == "deleted"]

    if modified:
        lines.append("TIME CHANGES on already-booked days:")
        for c in modified:
            lines.append(
                "  - {} ({}): {} - {}  =>  {} - {}".format(
                    c["date"], c["function"],
                    _format_dt(c["old_start"]), _format_dt(c["old_end"]),
                    _format_dt(c["new_start"]), _format_dt(c["new_end"]),
                )
            )
        lines.append("")

    if added:
        lines.append("NEW shifts added:")
        for c in added:
            lines.append(
                "  - {} ({}): {} - {}".format(
                    c["date"], c["function"], _format_dt(c["new_start"]), _format_dt(c["new_end"])
                )
            )
        lines.append("")

    if deleted:
        lines.append("Shifts REMOVED:")
        for c in deleted:
            lines.append(
                "  - {} ({}): was {} - {}".format(
                    c["date"], c["function"], _format_dt(c["old_start"]), _format_dt(c["old_end"])
                )
            )
        lines.append("")

    lines.append("-- Sent automatically by autoshift sync --")
    return "\n".join(lines)


def notify_changes(changes):
    """
    Send an email if there are any changes worth flagging.
    Only sends for 'modified' and 'deleted' by default (the surprising ones);
    set NOTIFY_ON_ADD=true to also get emailed for brand-new shifts.

    Only considers changes for shifts dated tomorrow or later -- today's
    shift is already locked in/in progress, so a same-day change isn't
    something worth an email alert for.
    """
    tomorrow = datetime.now().astimezone().date() + timedelta(days=1)
    changes = [c for c in changes if datetime.fromisoformat(c["date"]).date() >= tomorrow]

    notify_on_add = os.environ.get("NOTIFY_ON_ADD", "false").lower() == "true"

    relevant = [c for c in changes if c["type"] in ("modified", "deleted")]
    if notify_on_add:
        relevant += [c for c in changes if c["type"] == "added"]

    if not relevant:
        print("No notable shift changes from tomorrow onward -- skipping email.")
        return False

    if not SMTP_USER or not SMTP_PASSWORD:
        print("WARNING: SMTP_USER/SMTP_APP_PASSWORD not set -- cannot send change notification email.")
        return False

    modified_count = sum(1 for c in relevant if c["type"] == "modified")
    deleted_count = sum(1 for c in relevant if c["type"] == "deleted")

    subject_parts = []
    if modified_count:
        subject_parts.append("{} shift time change(s)".format(modified_count))
    if deleted_count:
        subject_parts.append("{} shift removed".format(deleted_count))
    subject = "Parpas Shifts: " + " & ".join(subject_parts)

    body = _build_body(relevant)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = NOTIFY_TO

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, [NOTIFY_TO], msg.as_string())
        print("Change notification email sent to {}".format(NOTIFY_TO))
        return True
    except Exception as err:
        print("WARNING: failed to send change notification email: {}".format(err))
        return False
