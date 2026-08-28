# tracker/adapters.py
"""Job-source adapters.

The tracker has three levels of source support:
- Greenhouse public boards
- Workday public CXS endpoints
- Generic career pages exposing schema.org JobPosting JSON-LD

All adapters return the same small dictionary shape expected by diff.py.
"""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urljoin

import httpx


HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobTracker/1.0; +https://github.com/neha-21297/job-tracker)"
}
GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def _client() -> httpx.Client:
    return httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True)


def fetch_greenhouse(token: str) -> list[dict]:
    url = GREENHOUSE_URL.format(token=token)
    with _client() as client:
        response = client.get(url, params={"content": "true"})
        response.raise_for_status()
        payload = response.json()

    jobs = payload.get("jobs", [])
    if not isinstance(jobs, list):
        raise ValueError(f"Unexpected Greenhouse response for board {token!r}")

    result = []
    for job in jobs:
        if not isinstance(job, dict) or "id" not in job or "title" not in job:
            continue
        location = (job.get("location") or {}).get("name", "")
        result.append({
            "id": job["id"],
            "title": job["title"],
            "location": location,
            "url": job.get("absolute_url", ""),
        })
    return result


def fetch_workday(host: str, path: str) -> list[dict]:
    """Fetch jobs from a public Workday CXS job-search endpoint."""
    tenant = host.split(".", 1)[0]
    endpoint = f"https://{host}/wday/cxs/{tenant}/{path}/jobs"
    payload = {"appliedFacets": {}, "limit": 100, "offset": 0, "searchText": ""}

    with _client() as client:
        response = client.post(endpoint, json=payload)
        response.raise_for_status()
        data = response.json()

    postings = data.get("jobPostings", [])
    if not isinstance(postings, list):
        return []

    result = []
    for job in postings:
        if not isinstance(job, dict):
            continue
        title = job.get("title") or job.get("jobTitle")
        if not title:
            continue
        job_id = job.get("bulletFields", [""])[0] if job.get("bulletFields") else None
        external = job.get("externalPath") or job.get("url") or ""
        if external and external.startswith("/"):
            external = f"https://{host}{external}"
        result.append({
            "id": job.get("jobPostingId") or job.get("id") or job_id or external or title,
            "title": title,
            "location": job.get("locationsText") or job.get("location") or "",
            "url": external,
        })
    return result


def _jsonld_objects(html: str) -> list[dict]:
    objects: list[dict] = []
    for match in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.I | re.S,
    ):
        raw = unescape(match).strip()
        try:
            value = json.loads(raw)
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


def fetch_career_page(url: str) -> list[dict]:
    """Extract structured JobPosting records from a public careers page.

    We deliberately only accept schema.org JobPosting records here. A generic
    scraper that treats every careers-page link as a job would fill the tracker
    with navigation links and unrelated content.
    """
    with _client() as client:
        response = client.get(url)
        response.raise_for_status()
        html = response.text

    result = []
    for obj in _jsonld_objects(html):
        kind = obj.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "JobPosting" not in kinds:
            continue

        title = obj.get("title") or obj.get("name")
        if not title:
            continue

        location_obj = obj.get("jobLocation")
        locations = []
        if isinstance(location_obj, list):
            location_items = location_obj
        else:
            location_items = [location_obj]
        for loc in location_items:
            if not isinstance(loc, dict):
                continue
            address = loc.get("address", loc)
            if isinstance(address, dict):
                bits = [address.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
                locations.append(", ".join(str(x) for x in bits if x))
        location = "; ".join(x for x in locations if x)

        job_url = obj.get("url") or url
        result.append({
            "id": str(obj.get("identifier", {}).get("value") if isinstance(obj.get("identifier"), dict) else obj.get("identifier") or job_url),
            "title": str(title),
            "location": location,
            "url": str(urljoin(url, job_url)),
        })
    return result


def fetch_source(item: dict) -> list[dict]:
    """Dispatch to the adapter declared by a source configuration entry."""
    adapter = item.get("adapter", "career_page")
    if adapter == "greenhouse":
        return fetch_greenhouse(item["token"])
    if adapter == "workday":
        return fetch_workday(item["host"], item["path"])
    if adapter == "career_page":
        return fetch_career_page(item["url"])
    raise ValueError(f"Unsupported adapter: {adapter}")
