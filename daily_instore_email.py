"""
Scrapes the "In Store" sale-item count from Bike Connection and emails
it via Gmail. Meant to be run once a day by a scheduled task (Windows
Task Scheduler) or a launchd job / cron (Mac).

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
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

# ---- CONFIG: read from environment variables ----
SALE_URL = "https://www.bikeconnection.net/product-list/sale-pg515/"
SENDER_EMAIL = os.environ["GMAIL_ADDRESS"]
APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]
# --------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def get_instore_count(url: str) -> int:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    availability_block = soup.find(id=re.compile(r"Facets-availability", re.I))
    search_scope = availability_block if availability_block else soup

    candidates = search_scope.find_all(string=re.compile(r"In\s*Store", re.I))
    for text_node in candidates:
        container = text_node.parent
        full_text = container.get_text(" ", strip=True)
        match = re.search(r"In\s*Store\D*(\d+)", full_text, re.I)
        if match:
            return int(match.group(1))

    match = re.search(r"In\s*Store\D{0,20}?(\d+)", resp.text, re.I)
    if match:
        return int(match.group(1))

    raise ValueError("Could not find an 'In Store' count on this page.")


def send_email(subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.sendmail(SENDER_EMAIL, [RECIPIENT_EMAIL], msg.as_string())


def main():
    try:
        count = get_instore_count(SALE_URL)
        subject = f"Bike Connection Sales Bike Count: {count}"
        body = (
            f"Today's sales bike count: {count}\n\n"
            f"Source: {SALE_URL}"
        )
    except Exception as exc:
        subject = "Bike Connection Sale scrape FAILED"
        body = f"The daily scrape script hit an error:\n\n{exc}"

    send_email(subject, body)
    print(f"Email sent to {RECIPIENT_EMAIL}: {subject}")


if __name__ == "__main__":
    main()
