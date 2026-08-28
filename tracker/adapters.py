# tracker/adapters.py
"""Read-only adapters for public job boards used by the tracker."""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urljoin, urlparse

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobTracker/1.0; +https://github.com/neha-21297/job-tracker)"
}


def _client() -> httpx.Client:
    return httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True)


def _normalise_location(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        bits = [value.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
        return ", ".join(str(x) for x in bits if x)
    return ""


def fetch_greenhouse(token: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    with _client() as client:
        response = client.get(url, params={"content": "true"})
        response.raise_for_status()
        payload = response.json()
    return [
        {
            "id": job["id"],
            "title": job["title"],
            "location": _normalise_location(job.get("location", {}).get("name", "")),
            "url": job.get("absolute_url", ""),
        }
        for job in payload.get("jobs", [])
        if isinstance(job, dict) and job.get("id") and job.get("title")
    ]


def fetch_ashby(slug: str) -> list[dict]:
    """Fetch an Ashby public job board."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    with _client() as client:
        response = client.get(url, params={"includeCompensation": "false"})
        response.raise_for_status()
        payload = response.json()
    result = []
    for job in payload.get("jobs", []):
        if not isinstance(job, dict) or not job.get("title"):
            continue
        result.append({
            "id": job.get("jobUrl") or job.get("id") or job["title"],
            "title": job["title"],
            "location": job.get("location", ""),
            "url": job.get("jobUrl") or job.get("applyUrl") or "",
        })
    return result


def fetch_workable(slug: str) -> list[dict]:
    """Fetch jobs from a public Workable board using its public HTML/JSON-LD."""
    url = f"https://apply.workable.com/{slug}/"
    with _client() as client:
        response = client.get(url)
        response.raise_for_status()
        html = response.text
    result = _jsonld_jobs(html, url)
    if result:
        return result

    # Workable embeds job data in page scripts on some boards. Extract the
    # common job URL/title/location fields without treating arbitrary links as jobs.
    result = []
    for match in re.finditer(r'"(?:title|name)"\s*:\s*"([^"]+)"[^{}]{0,1200}?"(?:url|shortlink)"\s*:\s*"([^"]+)"', html, re.I | re.S):
        title, job_url = match.groups()
        if "/j/" not in job_url:
            continue
        result.append({"id": job_url, "title": unescape(title), "location": "", "url": urljoin(url, job_url)})
    return result


def fetch_lever(slug: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}"
    with _client() as client:
        response = client.get(url, params={"mode": "json"})
        response.raise_for_status()
        payload = response.json()
    result = []
    for job in payload if isinstance(payload, list) else []:
        if not isinstance(job, dict) or not job.get("text"):
            continue
        categories = job.get("categories") or {}
        locations = categories.get("location") or ""
        result.append({
            "id": job.get("id") or job.get("hostedUrl") or job["text"],
            "title": job["text"],
            "location": locations,
            "url": job.get("hostedUrl") or job.get("applyUrl") or "",
        })
    return result


def fetch_smartrecruiters(company: str) -> list[dict]:
    url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
    result = []
    offset = 0
    with _client() as client:
        while True:
            response = client.get(url, params={"limit": 100, "offset": offset})
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("content", [])
            for job in rows:
                loc = job.get("location") or {}
                location = ", ".join(str(x) for x in (loc.get("city"), loc.get("region"), loc.get("country") ) if x)
                result.append({
                    "id": job.get("id") or job.get("refNumber"),
                    "title": job.get("name", ""),
                    "location": location,
                    "url": job.get("ref", "") or job.get("applyUrl", ""),
                })
            total = int(payload.get("totalFound", len(result)))
            if not rows or len(result) >= total:
                break
            offset += len(rows)
    return [x for x in result if x["id"] and x["title"]]


def _workday_candidates(host: str, path: str, source_url: str | None = None) -> list[tuple[str, str]]:
    candidates = []
    if host and path:
        candidates.append((host, path))
    if not source_url:
        return candidates
    try:
        with _client() as client:
            html = client.get(source_url).text
    except Exception:
        return candidates
    # Career pages frequently contain the actual Workday board URL even when
    # their public marketing URL has changed.
    for match in re.findall(r'https?://([A-Za-z0-9.-]+\.myworkdayjobs\.com)/(?:[^"\'<> ]*/)?([A-Za-z0-9_-]+)', html, re.I):
        pair = (match[0], match[1])
        if pair not in candidates:
            candidates.append(pair)
    return candidates


def fetch_workday(host: str, path: str, source_url: str | None = None) -> list[dict]:
    """Fetch public Workday CXS postings, discovering the live board if needed."""
    last_error = None
    for actual_host, actual_path in _workday_candidates(host, path, source_url):
        endpoint = f"https://{actual_host}/wday/cxs/{actual_host.split('.', 1)[0]}/{actual_path}/jobs"
        payload = {"appliedFacets": {}, "limit": 100, "offset": 0, "searchText": ""}
        try:
            with _client() as client:
                response = client.post(endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            last_error = exc
            continue

        result = []
        for job in data.get("jobPostings", []):
            if not isinstance(job, dict):
                continue
            title = job.get("title") or job.get("jobTitle")
            if not title:
                continue
            external = job.get("externalPath") or job.get("url") or ""
            if external.startswith("/"):
                external = f"https://{actual_host}{external}"
            result.append({
                "id": job.get("jobPostingId") or external or title,
                "title": title,
                "location": job.get("locationsText") or job.get("location") or "",
                "url": external,
            })
        return result
    if last_error:
        raise last_error
    return []


def _jsonld_objects(html: str) -> list[dict]:
    objects: list[dict] = []
    for match in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, flags=re.I | re.S):
        try:
            value = json.loads(unescape(match).strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
            graph = value.get("@graph")
            if isinstance(graph, list):
                objects.extend(x for x in graph if isinstance(x, dict))
        elif isinstance(value, list):
            objects.extend(x for x in value if isinstance(x, dict))
    return objects


def _jsonld_jobs(html: str, base_url: str) -> list[dict]:
    result = []
    for obj in _jsonld_objects(html):
        kind = obj.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "JobPosting" not in kinds or not obj.get("title"):
            continue
        identifier = obj.get("identifier")
        if isinstance(identifier, dict):
            identifier = identifier.get("value")
        job_url = obj.get("url") or base_url
        result.append({
            "id": str(identifier or job_url),
            "title": str(obj["title"]),
            "location": _normalise_location(obj.get("jobLocation")),
            "url": str(urljoin(base_url, job_url)),
        })
    return result


def fetch_career_page(url: str) -> list[dict]:
    with _client() as client:
        response = client.get(url)
        response.raise_for_status()
        return _jsonld_jobs(response.text, url)


def fetch_source(item: dict) -> list[dict]:
    adapter = item.get("adapter", "career_page")
    if adapter == "greenhouse":
        return fetch_greenhouse(item["token"])
    if adapter == "ashby":
        return fetch_ashby(item["token"])
    if adapter == "workable":
        return fetch_workable(item["token"])
    if adapter == "lever":
        return fetch_lever(item["token"])
    if adapter == "smartrecruiters":
        return fetch_smartrecruiters(item["token"])
    if adapter == "workday":
        return fetch_workday(item.get("host", ""), item.get("path", ""), item.get("url"))
    if adapter == "career_page":
        return fetch_career_page(item["url"])
    raise ValueError(f"Unsupported adapter: {adapter}")
