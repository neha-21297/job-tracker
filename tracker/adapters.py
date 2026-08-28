# tracker/adapters.py
"""Read-only adapters for public job boards used by the tracker."""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urljoin

import httpx

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JobTracker/1.0; +https://github.com/neha-21297/job-tracker)"}


def _client() -> httpx.Client:
    return httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True)


def _normalise_location(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        bits = [value.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
        return ", ".join(str(x) for x in bits if x)
    if isinstance(value, list):
        return "; ".join(_normalise_location(x) for x in value if x)
    return ""


def fetch_greenhouse(token: str) -> list[dict]:
    with _client() as client:
        response = client.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
        response.raise_for_status()
        payload = response.json()
    return [{"id": j["id"], "title": j["title"], "location": _normalise_location((j.get("location") or {}).get("name", "")), "url": j.get("absolute_url", "")}
            for j in payload.get("jobs", []) if isinstance(j, dict) and j.get("id") and j.get("title")]


def fetch_ashby(slug: str) -> list[dict]:
    with _client() as client:
        response = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "false"})
        response.raise_for_status()
        payload = response.json()
    return [{"id": j.get("jobUrl") or j.get("id") or j["title"], "title": j["title"], "location": j.get("location", ""), "url": j.get("jobUrl") or j.get("applyUrl") or ""}
            for j in payload.get("jobs", []) if isinstance(j, dict) and j.get("title")]


def fetch_workable(slug: str) -> list[dict]:
    url = f"https://apply.workable.com/{slug}/"
    with _client() as client:
        response = client.get(url)
        response.raise_for_status()
        html = response.text
    result = _jsonld_jobs(html, url)
    for m in re.finditer(r'"(?:title|name)"\s*:\s*"([^"]+)"[^{}]{0,1600}?"(?:url|shortlink)"\s*:\s*"([^"]+)"', html, re.I | re.S):
        title, job_url = m.groups()
        if "/j/" in job_url and not any(x["url"] == job_url for x in result):
            result.append({"id": job_url, "title": unescape(title), "location": "", "url": urljoin(url, job_url)})
    return result


def fetch_lever(slug: str) -> list[dict]:
    with _client() as client:
        response = client.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
        response.raise_for_status()
        payload = response.json()
    result = []
    for j in payload if isinstance(payload, list) else []:
        if not isinstance(j, dict) or not j.get("text"):
            continue
        cat = j.get("categories") or {}
        result.append({"id": j.get("id") or j.get("hostedUrl") or j["text"], "title": j["text"], "location": cat.get("location", ""), "url": j.get("hostedUrl") or j.get("applyUrl") or ""})
    return result


def fetch_smartrecruiters(company: str) -> list[dict]:
    url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
    result, offset = [], 0
    with _client() as client:
        while True:
            response = client.get(url, params={"limit": 100, "offset": offset})
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("content", [])
            for j in rows:
                loc = j.get("location") or {}
                location = ", ".join(str(x) for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
                result.append({"id": j.get("id") or j.get("refNumber"), "title": j.get("name", ""), "location": location, "url": j.get("ref", "") or j.get("applyUrl", "")})
            if not rows or len(result) >= int(payload.get("totalFound", len(result))):
                break
            offset += len(rows)
    return [x for x in result if x["id"] and x["title"]]


def _workday_candidates(host: str, path: str, source_url: str | None) -> list[tuple[str, str]]:
    candidates = [(host, path)] if host and path else []
    if not source_url:
        return candidates
    try:
        with _client() as client:
            html = client.get(source_url).text
    except Exception:
        return candidates
    for h, p in re.findall(r'https?://([A-Za-z0-9.-]+\.myworkdayjobs\.com)/(?:[^"\'<> ]*/)?([A-Za-z0-9_-]+)', html, re.I):
        pair = (h, p)
        if pair not in candidates:
            candidates.append(pair)
    return candidates


def fetch_workday(host: str, path: str, source_url: str | None = None) -> list[dict]:
    last_error = None
    for actual_host, actual_path in _workday_candidates(host, path, source_url):
        endpoint = f"https://{actual_host}/wday/cxs/{actual_host.split('.', 1)[0]}/{actual_path}/jobs"
        try:
            with _client() as client:
                response = client.post(endpoint, json={"appliedFacets": {}, "limit": 100, "offset": 0, "searchText": ""})
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            last_error = exc
            continue
        result = []
        for j in data.get("jobPostings", []):
            title = j.get("title") or j.get("jobTitle") if isinstance(j, dict) else None
            if not title:
                continue
            external = (j.get("externalPath") or j.get("url") or "") if isinstance(j, dict) else ""
            if external.startswith("/"):
                external = f"https://{actual_host}{external}"
            result.append({"id": j.get("jobPostingId") or external or title, "title": title, "location": j.get("locationsText") or j.get("location") or "", "url": external})
        return result
    if last_error:
        raise last_error
    return []


def _jsonld_objects(html: str) -> list[dict]:
    objects = []
    for match in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, flags=re.I | re.S):
        try:
            value = json.loads(unescape(match).strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
            if isinstance(value.get("@graph"), list):
                objects.extend(x for x in value["@graph"] if isinstance(x, dict))
        elif isinstance(value, list):
            objects.extend(x for x in value if isinstance(x, dict))
    return objects


def _jsonld_jobs(html: str, base_url: str) -> list[dict]:
    result = []
    for obj in _jsonld_objects(html):
        kind = obj.get("@type")
        if "JobPosting" not in (kind if isinstance(kind, list) else [kind]) or not obj.get("title"):
            continue
        ident = obj.get("identifier")
        ident = ident.get("value") if isinstance(ident, dict) else ident
        job_url = obj.get("url") or base_url
        result.append({"id": str(ident or job_url), "title": str(obj["title"]), "location": _normalise_location(obj.get("jobLocation")), "url": str(urljoin(base_url, job_url))})
    return result


def fetch_career_page(url: str) -> list[dict]:
    with _client() as client:
        response = client.get(url)
        response.raise_for_status()
        return _jsonld_jobs(response.text, url)


def fetch_source(item: dict) -> list[dict]:
    """Use the configured adapter, with known ATS migrations overriding stale config."""
    key = item.get("name", "").lower()
    if "sylvera" in key:
        return fetch_ashby("sylvera")
    if "iceye" in key:
        return fetch_workable("iceye")
    if "spire global" in key:
        return fetch_greenhouse("spire")
    if "planet" in key:
        return fetch_greenhouse("planetlabs")
    if "maxar" in key:
        return fetch_career_page("https://vantor.com/careers")
    if key == "beam":
        return fetch_career_page("https://careers.beam.global/en-GB/jobs")
    if "rovco" in key:
        return fetch_career_page("https://rovco.com/careers")
    if "aurora energy research" in key:
        return fetch_career_page("https://auroraer.com/careers/join-us")
    if "storegga" in key:
        return fetch_career_page("https://storegga.earth/careers")
    if "reach subsea" in key:
        return fetch_career_page("https://www.reachsubsea.com/careers/")
    if "viridien" in key:
        return fetch_workday("cgg.wd103.myworkdayjobs.com", "viridienfairs", item.get("url"))

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
