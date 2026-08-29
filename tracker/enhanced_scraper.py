"""Second-pass browser scraper for career pages that defeat the normal adapters.

This module is intentionally conservative: it only runs when the existing
adapter returned no jobs. It observes the page's JSON/network traffic, checks
iframes, expands common job-list controls, and parses job-shaped JSON records.
"""
from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urljoin, urlparse

from .adapters import _parse_html_jobs, _fetch_ats_from_html


def _location(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        keys = ("addressLocality", "addressRegion", "addressCountry", "city", "state", "country", "name")
        return ", ".join(str(value[k]) for k in keys if value.get(k))
    if isinstance(value, list):
        return "; ".join(x for x in (_location(v) for v in value) if x)
    return ""


def _json_jobs(value, base_url: str, out: list[dict], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(value, list):
        for item in value:
            _json_jobs(item, base_url, out, depth + 1)
        return
    if not isinstance(value, dict):
        return

    title = ""
    for key in ("title", "jobTitle", "positionTitle", "postingTitle", "job_posting_title", "name"):
        v = value.get(key)
        if isinstance(v, str) and 3 <= len(v.strip()) <= 220:
            title = v.strip()
            break

    url = ""
    for key in ("url", "jobUrl", "jobURL", "applyUrl", "applyURL", "externalPath", "jobDetailUrl", "detailUrl", "link"):
        v = value.get(key)
        if isinstance(v, str) and v.strip():
            url = urljoin(base_url, v.strip())
            break

    location = ""
    for key in ("locationsText", "location", "locations", "jobLocation", "workLocation", "address", "city"):
        if value.get(key):
            location = _location(value[key])
            if location:
                break

    # A record needs a title and either a usable URL or a location. This avoids
    # treating arbitrary JSON objects containing a field called "name" as jobs.
    if title and (url or location):
        job_id = value.get("jobPostingId") or value.get("jobId") or value.get("id") or url or title
        record = {"id": str(job_id), "title": title, "location": location, "url": url or base_url}
        if not any(x["id"] == record["id"] or x["url"] == record["url"] for x in out):
            out.append(record)

    for child in value.values():
        if isinstance(child, (dict, list)):
            _json_jobs(child, base_url, out, depth + 1)


def _parse_json(text: str, base_url: str) -> list[dict]:
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    _json_jobs(value, base_url, out)
    return out


def _links(html: str, base_url: str) -> list[str]:
    result = []
    for href, text in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(text))).strip()
        full = urljoin(base_url, unescape(href))
        hay = (label + " " + full).lower()
        if re.search(r"career|vacan|job|opening|position|opportunit|work-with-us|join-us", hay):
            if full not in result:
                result.append(full)
    return result


def _click_job_controls(page) -> None:
    """Trigger lazy-loaded job lists without depending on a site's CSS."""
    patterns = re.compile(r"load more|show more|view more|see more|search jobs|find jobs|all jobs|view all", re.I)
    for _ in range(3):
        try:
            buttons = page.get_by_role("button", name=patterns)
            count = min(buttons.count(), 5)
            if not count:
                break
            for i in range(count):
                try:
                    buttons.nth(i).click(timeout=1200)
                    page.wait_for_timeout(700)
                except Exception:
                    pass
        except Exception:
            break


def _dedupe(jobs: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for job in jobs:
        key = job.get("url") or job.get("id")
        if key and key not in seen:
            seen.add(key)
            out.append(job)
    return out


def fetch_enhanced(url: str) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    candidates = [url]
    seen_pages = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36", locale="en-GB")
            api_jobs: list[dict] = []
            response_urls = set()

            def on_response(response):
                try:
                    u = response.url
                    ct = (response.headers.get("content-type") or "").lower()
                    if "json" not in ct and not re.search(r"(?:/api/|graphql|jobs?|vacanc|posting|recruit|search)", u, re.I):
                        return
                    if len(response_urls) >= 100 or u in response_urls:
                        return
                    response_urls.add(u)
                    body = response.body()
                    if len(body) <= 4_000_000:
                        api_jobs.extend(_parse_json(body.decode("utf-8", "ignore"), u))
                except Exception:
                    pass

            page.on("response", on_response)

            for candidate in candidates[:8]:
                if candidate in seen_pages:
                    continue
                seen_pages.add(candidate)
                try:
                    response = page.goto(candidate, wait_until="domcontentloaded", timeout=20000)
                    if response and response.status >= 400:
                        continue
                    page.wait_for_timeout(2500)
                    _click_job_controls(page)
                    try:
                        page.mouse.wheel(0, 5000)
                        page.wait_for_timeout(1000)
                    except Exception:
                        pass
                    html = page.content()

                    jobs = _parse_html_jobs(html, candidate)
                    if not jobs:
                        jobs = _fetch_ats_from_html(html)
                    jobs.extend(api_jobs)
                    jobs = _dedupe(jobs)
                    if jobs:
                        return jobs

                    # Search every accessible iframe; ATSs are often embedded here.
                    for frame in page.frames:
                        if frame == page.main_frame:
                            continue
                        try:
                            frame_url = frame.url or candidate
                            frame_html = frame.content()
                            frame_jobs = _parse_html_jobs(frame_html, frame_url)
                            if not frame_jobs:
                                frame_jobs = _fetch_ats_from_html(frame_html)
                            if not frame_jobs:
                                for response in list(response_urls):
                                    pass
                            if frame_jobs:
                                return _dedupe(frame_jobs)
                            for link in _links(frame_html, frame_url)[:6]:
                                if link not in seen_pages:
                                    candidates.append(link)
                        except Exception:
                            continue

                    for link in _links(html, candidate)[:10]:
                        if link not in seen_pages:
                            candidates.append(link)
                except Exception:
                    continue
        finally:
            browser.close()
    return []
