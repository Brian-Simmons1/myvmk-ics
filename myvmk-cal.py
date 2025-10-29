#!/usr/bin/env python3
r"""
myvmk-cal.py
Scrape the MyVMK events calendar and convert visible events to an .ics feed
you can import or subscribe to in Google Calendar.

Examples:
  python myvmk-cal.py --url https://www.myvmk.com/events --out myvmk.ics --tz America/New_York
  python myvmk-cal.py --url https://www.myvmk.com/events --out myvmk.ics --verbose
  python myvmk-cal.py --url https://www.myvmk.com/events --out myvmk.ics --year 2025 --month 10

Requires:
  pip install playwright beautifulsoup4
  playwright install chromium
"""
import argparse
import datetime as dt
import hashlib
import re
import sys
from typing import Optional, Tuple, List

try:
    from playwright.sync_api import sync_playwright
    from bs4 import BeautifulSoup
except Exception:
    print("This script requires 'playwright' and 'beautifulsoup4'. Install with:")
    print("  pip install playwright beautifulsoup4")
    print("  playwright install chromium")
    raise

MONTHS = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
}

TIME_RANGE_RE = re.compile(r'^\s*([0-9]{1,2}:[0-9]{2}\s*[AP]M)\s*-\s*([0-9]{1,2}:[0-9]{2}\s*[AP]M)\s*$', re.I)

def safe_text(x) -> str:
    return (x.get_text(strip=True) if x else "").strip()

def load_html(url: str, verbose: bool = False) -> str:
    """Load HTML with JavaScript rendering using Playwright."""
    if verbose:
        print(f"[debug] Loading {url} with Playwright (JavaScript rendering enabled)")

    with sync_playwright() as p:
        if verbose:
            print("[debug] Launching browser...")
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        if verbose:
            print("[debug] Navigating to URL...")
        page.goto(url, wait_until='load', timeout=30000)

        if verbose:
            print("[debug] Waiting for calendar to render...")
        # Wait for network to be idle and give time for JavaScript to run
        page.wait_for_load_state('networkidle', timeout=30000)

        # Give extra time for events to populate
        page.wait_for_timeout(3000)

        if verbose:
            print("[debug] Calendar loaded, extracting HTML")
        html = page.content()
        browser.close()
        return html

def parse_header_month_year(soup) -> Tuple[Optional[int], Optional[int]]:
    """Best-effort parse of 'October 2025' from header/h1/h2."""
    header = soup.select_one(".header") or soup.find("div", class_="header")
    candidates = [header] if header else []
    candidates += soup.find_all(["h1", "h2"])
    for node in candidates:
        text = safe_text(node).lower()
        m = re.search(r'(' + '|'.join(MONTHS.keys()) + r')\s+(\d{4})', text, re.I)
        if m:
            return int(m.group(2)), MONTHS[m.group(1).lower()]
    return None, None

def parse_time_range(text: str, base_date: dt.date) -> Tuple[dt.datetime, dt.datetime]:
    """Parse '6:00 PM - 7:00 PM'; fallback to all-day if not matched."""
    m = TIME_RANGE_RE.match(text or "")
    if not m:
        start = dt.datetime.combine(base_date, dt.time(0, 0))
        end = start + dt.timedelta(hours=23, minutes=59)
        return start, end

    def to_hm(s: str) -> Tuple[int, int]:
        t = dt.datetime.strptime(s.upper().replace(" ", ""), "%I:%M%p")
        return t.hour, t.minute

    sh, sm = to_hm(m.group(1))
    eh, em = to_hm(m.group(2))
    start = dt.datetime.combine(base_date, dt.time(sh, sm))
    end = dt.datetime.combine(base_date, dt.time(eh, em))
    if end <= start:
        end += dt.timedelta(days=1)
    return start, end

def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")

def ics_dt(dt_obj: dt.datetime, tzid: Optional[str]) -> str:
    stamp = dt_obj.strftime("%Y%m%dT%H%M%S")
    return (f";TZID={tzid}:{stamp}" if tzid else f":{stamp}")

def make_uid(title: str, start: dt.datetime, end: dt.datetime) -> str:
    data = f"{title}|{start.isoformat()}|{end.isoformat()}".encode("utf-8")
    return hashlib.sha1(data).hexdigest() + "@myvmk"

def build_ics(events: List[dict], tzid: Optional[str], cal_name: str = "MyVMK Events") -> str:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//MyVMK Scraper//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{ics_escape(cal_name)}",
    ]
    for ev in events:
        title = ev["title"] or "MyVMK Event"
        start = ev["start"]
        end = ev["end"]
        uid = make_uid(title, start, end)
        lines += [
            "BEGIN:VEVENT",
            f"DTSTAMP:{now}",
            f"UID:{uid}",
            f"SUMMARY:{ics_escape(title)}",
            f"DTSTART{ics_dt(start, tzid)}",
            f"DTEND{ics_dt(end, tzid)}",
        ]
        if ev.get("source"):
            lines.append(f"DESCRIPTION:{ics_escape(ev['source'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\n".join(lines) + "\n"

def scrape_events(url: str, forced_year: Optional[int], forced_month: Optional[int], verbose: bool = False) -> List[dict]:
    html = load_html(url, verbose)

    # Debug: save HTML to file
    if verbose:
        with open("calendar_debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("[debug] Saved HTML to calendar_debug.html for inspection")

    soup = BeautifulSoup(html, "html.parser")

    # Determine month/year
    year, month = parse_header_month_year(soup)
    if forced_year:
        year = forced_year
    if forced_month:
        month = forced_month
    if not (year and month):
        today = dt.date.today()
        year, month = today.year, today.month
        if verbose:
            print(f"[warn] Could not parse header month/year; defaulting to {month}/{year}")

    # Day containers: find all divs with class containing 'day' but not 'hidden'
    all_day_divs = soup.find_all("div", class_=lambda c: c and "day" in c.split() and "hidden" not in c.split())
    if verbose:
        print(f"[debug] found {len(all_day_divs)} visible day divs")
    events: List[dict] = []

    for day_div in all_day_divs:
        # Find the day number from p.number or similar
        num_tag = day_div.find("p", class_=lambda c: c and "number" in c.split())
        if not num_tag:
            continue

        day_str = safe_text(num_tag)
        if not day_str.isdigit():
            continue
        day_num = int(day_str)

        try:
            base_date = dt.date(year, month, day_num)
        except ValueError:
            continue

        # Find event list items
        li_nodes = day_div.find_all("li", class_=lambda c: c and "event-li" in c.split())
        if verbose and li_nodes:
            print(f"[debug] {base_date}: found {len(li_nodes)} event li nodes")

        for li in li_nodes:
            # Look for the event container div
            container = li.find("div", class_=lambda c: c and "event" in c.split() and "day-targetable" in c.split())
            if not container:
                container = li

            # Extract title
            title_tag = container.find("p", class_=lambda c: c and "event-title" in c.split())
            title = safe_text(title_tag)

            # Extract time
            time_tag = container.find("p", class_=lambda c: c and "event-time" in c.split())
            time_txt = safe_text(time_tag)

            if not title and not time_txt:
                continue

            start_dt, end_dt = parse_time_range(time_txt, base_date)
            events.append({
                "title": title or "MyVMK Event",
                "start": start_dt,
                "end": end_dt,
                "source": url
            })

    if verbose:
        print(f"[info] Parsed {len(events)} events")
    return events

def main():
    ap = argparse.ArgumentParser(description="Convert MyVMK events calendar to .ics using Playwright")
    ap.add_argument("--url", default="https://download.myvmk.com/calendar.html",
                    help="URL to the MyVMK events calendar")
    ap.add_argument("--out", default="myvmk.ics", help="Output .ics filename")
    ap.add_argument("--year", type=int, default=None, help="Force year (e.g., 2025)")
    ap.add_argument("--month", type=int, default=None, help="Force month 1-12 (e.g., 10)")
    ap.add_argument("--tz", default="America/New_York",
                    help="TZID label for DTSTART/DTEND (no VTIMEZONE emitted)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    try:
        events = scrape_events(args.url, args.year, args.month, args.verbose)
        ics_text = build_ics(events, args.tz)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(ics_text)
        print(f"Wrote {len(events)} events to {args.out}")
    except Exception as e:
        print("Error:", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
