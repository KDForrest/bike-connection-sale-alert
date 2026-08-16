"""
Scrapes the sale-item counts by category (Bikes, Accessories, Parts,
Clothing, etc.) from Bike Connection's "Categories" filter on the Sale
page, and emails them via Gmail. Meant to be run once a day by a
scheduled task (Windows Task Scheduler) or a launchd job / cron (Mac).

SETUP (one-time):
1. pip install requests beautifulsoup4
2. Turn on 2-Step Verification on the Gmail account that will SEND
   the email: https://myaccount.google.com/security
3. Create an "App Password" for this script:
   https://myaccount.google.com/apppasswords
   (choose "Mail" as the app) -> Google gives you a 16-character
   password like "abcd efgh ijkl mnop".
4. Set these as environment variables (locally) or as GitHub Actions
   repo secrets (see the workflow file): GMAIL_ADDRESS,
   GMAIL_APP_PASSWORD, RECIPIENT_EMAIL.
   Do NOT use your normal Gmail password -- only the app password,
   and never commit real credentials into this file or the repo.
5. Test it once locally by setting the env vars and running:
   python daily_instore_email.py
"""

import os
import re
import smtplib
import sys
from datetime import datetime
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ---- CONFIG: read from environment variables ----
SALE_URL = "https://www.bikeconnection.net/product-list/sale-pg515/"
SENDER_EMAIL = os.environ["GMAIL_ADDRESS"]
APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
# RECIPIENT_EMAIL can be one address or several, comma-separated, e.g.
# "dad@example.com, you@example.com"
RECIPIENT_EMAILS = [
    addr.strip()
    for addr in os.environ["RECIPIENT_EMAIL"].split(",")
    if addr.strip()
]
# --------------------------------------------------

# File that remembers the date (YYYY-MM-DD, Pacific time) the email was
# last sent, so a delayed or repeated hourly check-in doesn't send twice
# -- and a late trigger still sends, instead of being skipped outright.
MARKER_FILE = "last_sent_date.txt"
SEND_AFTER_HOUR = 10  # Pacific hour after which the daily email is due

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def get_category_counts(url: str) -> "dict[str, int]":
    """Scrape the "Categories" filter block on the Sale page (Bikes,
    Accessories, Parts, Clothing, ...) and return {label: count},
    e.g. {"Bikes": 118, "Accessories": 14, "Parts": 6, "Clothing": 3}.

    NOTE: this is the "Categories" facet, not "Availability" / "In
    Store" -- "In Store" only reflects items available in the physical
    shop right now, not the true count of sale items per category.
    """
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    categories_block = soup.find(id=re.compile(r"Facets-categories", re.I))
    if categories_block is None:
        # Don't fall back to scraping the whole page here -- without a
        # scoped block, unrelated numbers elsewhere on the page (nav,
        # prices, etc.) could get misread as category counts. Better to
        # fail loudly (main() emails a "scrape FAILED" alert) than to
        # silently send wrong numbers.
        raise ValueError("Could not find the 'Categories' filter block on this page.")

    # Strip heading/toggle text (e.g. a "Categories" button/legend) so it
    # doesn't get glued onto the first category label below -- without
    # this, "Categories" + "Bikes (118)" can merge into one bad label.
    for heading in categories_block.find_all(["h1", "h2", "h3", "h4", "legend", "button"]):
        heading.extract()

    full_text = categories_block.get_text(" ", strip=True)
    # Extra safety net in case the heading wasn't in one of the tags
    # above (e.g. it's a <span> or <div> instead of h2/legend/button).
    full_text = re.sub(r"^\s*Categories\b", "", full_text, flags=re.I)

    counts: "dict[str, int]" = {}
    for label, number in re.findall(r"([A-Za-z][A-Za-z&'\- ]*?)\s*\((\d+)\)", full_text):
        label = label.strip()
        if label and label.lower() != "categories":
            counts.setdefault(label, int(number))

    if not counts:
        raise ValueError("Could not find any category counts on this page.")

    return counts


def send_email(subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(RECIPIENT_EMAILS)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAILS, msg.as_string())


def already_sent_today(today_str: str) -> bool:
    if not os.path.exists(MARKER_FILE):
        return False
    with open(MARKER_FILE, "r") as f:
        return f.read().strip() == today_str


def mark_sent_today(today_str: str) -> None:
    with open(MARKER_FILE, "w") as f:
        f.write(today_str)


def main():
    # The workflow checks in every hour (schedule triggers can be delayed
    # or occasionally dropped by GitHub, so we don't rely on hitting one
    # exact minute). Each check-in looks at the real Pacific time and
    # sends the email the FIRST time it's at or after the target hour,
    # then remembers today's date in a marker file so later check-ins
    # the same day don't send a duplicate.
    # A manual "Run workflow" click always sends immediately, for testing.
    is_manual_run = os.environ.get("GITHUB_EVENT_NAME") != "schedule"
    pacific_now = datetime.now(ZoneInfo("America/Los_Angeles"))
    today_str = pacific_now.date().isoformat()

    if not is_manual_run:
        if pacific_now.hour < SEND_AFTER_HOUR:
            print(f"Skipping: it's {pacific_now.hour}:00 Pacific, too early.")
            return
        if already_sent_today(today_str):
            print(f"Skipping: already sent today ({today_str}).")
            return

    try:
        counts = get_category_counts(SALE_URL)
        summary = ", ".join(f"{label} {count}" for label, count in counts.items())
        subject = f"Bike Connection Sale Counts: {summary}"
        lines = "\n".join(f"{label}: {count}" for label, count in counts.items())
        body = (
            "Today's sale counts by category:\n\n"
            f"{lines}\n\n"
            f"Source: {SALE_URL}"
        )
    except Exception as exc:
        subject = "Bike Connection Sale scrape FAILED"
        body = f"The daily scrape script hit an error:\n\n{exc}"

    send_email(subject, body)
    mark_sent_today(today_str)
    print(f"Email sent to {', '.join(RECIPIENT_EMAILS)}: {subject}")


if __name__ == "__main__":
    main()
