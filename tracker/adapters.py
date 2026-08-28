# tracker/adapters.py
"""Read-only adapters for public job boards used by the tracker."""
from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urljoin

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobTracker/1.0; +https://github.com/neha-21297/job-tracker)",
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


def _client() -> httpx.Client:
    return httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True)


def _normalise_location(value) -> str:
    if isinstance(value, str): return value
    if isinstance(value, dict):
        bits = [value.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
        return ", ".join(str(x) for x in bits if x)
    if isinstance(value, list): return "; ".join(_normalise_location(x) for x in value if x)
    return ""


def fetch_greenhouse(token: str) -> list[dict]:
    with _client() as client:
        response = client.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
        response.raise_for_status(); payload = response.json()
    return [{"id": j["id"], "title": j["title"], "location": _normalise_location((j.get("location") or {}).get("name", "")), "url": j.get("absolute_url", "")}
            for j in payload.get("jobs", []) if isinstance(j, dict) and j.get("id") and j.get("title")]


def fetch_ashby(slug: str) -> list[dict]:
    with _client() as client:
        response = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "false"})
        response.raise_for_status(); payload = response.json()
    return [{"id": j.get("jobUrl") or j.get("id") or j["title"], "title": j["title"], "location": j.get("location", ""), "url": j.get("jobUrl") or j.get("applyUrl") or ""}
            for j in payload.get("jobs", []) if isinstance(j, dict) and j.get("title")]


def fetch_workable(slug: str) -> list[dict]:
    url = f"https://apply.workable.com/{slug}/"
    with _client() as client:
        response = client.get(url); response.raise_for_status(); html = response.text
    result = _jsonld_jobs(html, url)
    for m in re.finditer(r'"(?:title|name)"\s*:\s*"([^"]+)"[^{}]{0,1600}?"(?:url|shortlink)"\s*:\s*"([^"]+)"', html, re.I | re.S):
        title, job_url = m.groups(); full = urljoin(url, job_url)
        if "/j/" in job_url and not any(x["url"] == full for x in result):
            result.append({"id": job_url, "title": unescape(title), "location": "", "url": full})
    return result


def fetch_lever(slug: str) -> list[dict]:
    with _client() as client:
        response = client.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
        response.raise_for_status(); payload = response.json()
    result = []
    for j in payload if isinstance(payload, list) else []:
        if not isinstance(j, dict) or not j.get("text"): continue
        cat = j.get("categories") or {}
        result.append({"id": j.get("id") or j.get("hostedUrl") or j["text"], "title": j["text"], "location": cat.get("location", ""), "url": j.get("hostedUrl") or j.get("applyUrl") or ""})
    return result


def fetch_smartrecruiters(company: str) -> list[dict]:
    url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"; result, offset = [], 0
    with _client() as client:
        while True:
            response = client.get(url, params={"limit": 100, "offset": offset}); response.raise_for_status(); payload = response.json()
            rows = payload.get("content", [])
            for j in rows:
                loc = j.get("location") or {}; location = ", ".join(str(x) for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
                job_id = j.get("id") or j.get("refNumber")
                public_url = j.get("applyUrl") or (f"https://jobs.smartrecruiters.com/{company}/{job_id}" if job_id else "")
                result.append({"id": job_id, "title": j.get("name", ""), "location": location, "url": public_url})
            if not rows or len(result) >= int(payload.get("totalFound", len(result))): break
            offset += len(rows)
    return [x for x in result if x["id"] and x["title"]]


def _workday_candidates(host: str, path: str, source_url: str | None) -> list[tuple[str, str]]:
    candidates = [(host, path)] if host and path else []
    if not source_url: return candidates
    try:
        with _client() as client:
            response = client.get(source_url); response.raise_for_status(); html = response.text
    except Exception: return candidates
    for h, p in re.findall(r'https?://([A-Za-z0-9.-]+\.myworkdayjobs\.com)/(?:[^"\'<> ]*/)?([A-Za-z0-9_-]+)', html, re.I):
        if (h, p) not in candidates: candidates.append((h, p))
    return candidates


def _workday_site_url(host: str, site: str, source_url: str | None) -> str:
    if source_url and "myworkdayjobs.com" in source_url:
        m = re.search(r"myworkdayjobs\.com/([^/]+)/" + re.escape(site) + r"(?:/|$)", source_url, re.I)
        if m: return f"https://{host}/{m.group(1)}/{site}"
    return f"https://{host}/{site}"


def fetch_workday(host: str, path: str, source_url: str | None = None, tenant: str | None = None) -> list[dict]:
    """Fetch Workday CXS jobs using the supported 20-row page size."""
    last_error = None
    for actual_host, actual_path in _workday_candidates(host, path, source_url):
        actual_tenant = tenant or actual_host.split('.', 1)[0]
        endpoint = f"https://{actual_host}/wday/cxs/{actual_tenant}/{actual_path}/jobs"
        result, offset, total = [], 0, None
        try:
            with _client() as client:
                while True:
                    response = client.post(endpoint, headers={"Content-Type": "application/json", "Accept": "application/json"},
                                           json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""})
                    response.raise_for_status(); data = response.json()
                    if total is None: total = int(data.get("total") or 0)
                    postings = data.get("jobPostings", [])
                    for j in postings:
                        if not isinstance(j, dict): continue
                        title = j.get("title") or j.get("jobTitle")
                        if not title: continue
                        external = j.get("externalPath") or j.get("url") or ""
                        if external.startswith("/"): external = _workday_site_url(actual_host, actual_path, source_url) + external
                        result.append({"id": j.get("jobPostingId") or j.get("externalPath") or external or title,
                                       "title": title, "location": j.get("locationsText") or j.get("location") or "", "url": external})
                    if not postings or (total and offset + len(postings) >= total) or len(postings) < 20: break
                    offset += len(postings)
            return result
        except Exception as exc:
            last_error = exc
    if last_error: raise last_error
    return []


def fetch_akerbp() -> list[dict]:
    url = "https://akerbp.com/en/career/"
    with _client() as client:
        response = client.get(url); response.raise_for_status(); html = response.text
    result = _jsonld_jobs(html, url)
    for href, title in re.findall(r'href=["\']([^"\']*(?:/job/|/go/)[^"\']*)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        clean = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', title)).strip(); full = urljoin(url, href)
        if clean and len(clean) >= 5 and not any(x["url"] == full for x in result): result.append({"id": full, "title": clean, "location": "Norway", "url": full})
    return result


def fetch_mott() -> list[dict]:
    url = "https://apply.mottmac.com/search/?q=graduate%2C+united+kingdom%2C+uk"
    with _client() as client:
        response = client.get(url); response.raise_for_status(); html = response.text
    result = _jsonld_jobs(html, url)
    for href, title in re.findall(r'href=["\']([^"\']*(?:/job/|/go/)[^"\']*)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        clean = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', title)).strip(); full = urljoin(url, href)
        if clean and len(clean) >= 5 and re.search(r'(?i)graduate|junior|geotech|geolog|gis|environment|energy|engineer', clean) and not any(x["url"] == full for x in result):
            result.append({"id": full, "title": clean, "location": "United Kingdom", "url": full})
    return result


def _jsonld_objects(html: str) -> list[dict]:
    objects = []
    for match in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, flags=re.I | re.S):
        try: value = json.loads(unescape(match).strip())
        except json.JSONDecodeError: continue
        if isinstance(value, dict):
            objects.append(value)
            if isinstance(value.get("@graph"), list): objects.extend(x for x in value["@graph"] if isinstance(x, dict))
        elif isinstance(value, list): objects.extend(x for x in value if isinstance(x, dict))
    return objects


def _jsonld_jobs(html: str, base_url: str) -> list[dict]:
    result = []
    for obj in _jsonld_objects(html):
        kind = obj.get("@type")
        if "JobPosting" not in (kind if isinstance(kind, list) else [kind]) or not obj.get("title"): continue
        ident = obj.get("identifier"); ident = ident.get("value") if isinstance(ident, dict) else ident
        job_url = obj.get("url") or base_url
        result.append({"id": str(ident or job_url), "title": str(obj["title"]), "location": _normalise_location(obj.get("jobLocation")), "url": str(urljoin(base_url, job_url))})
    return result


def _embedded_jobs(html: str, base_url: str) -> list[dict]:
    result = []
    pattern = r'"job_posting_title"\s*:\s*"((?:\\.|[^"\\])+)".*?"external_posting_url"\s*:\s*"((?:\\.|[^"\\])+)"'
    for title, job_url in re.findall(pattern, html, re.I | re.S):
        try: title, job_url = json.loads('"' + title + '"'), json.loads('"' + job_url + '"')
        except json.JSONDecodeError: title, job_url = unescape(title), unescape(job_url)
        if title and job_url:
            full = urljoin(base_url, job_url)
            if not any(x["url"] == full for x in result): result.append({"id": full, "title": title, "location": "", "url": full})
    return result


def _career_link_jobs(html: str, base_url: str) -> list[dict]:
    result = []
    for href, title in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        clean = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', unescape(title))).strip()
        if len(clean) < 5 or len(clean) > 180: continue
        full = urljoin(base_url, unescape(href)); path = full.lower()
        if not re.search(r'/(?:job|jobs|vacan|position|opening|opportunit|career)/', path): continue
        if re.search(r'/(?:search|filter|category|department|location|login|register|apply(?:-now)?)(?:[/?#]|$)', path): continue
        if not any(x["url"] == full for x in result): result.append({"id": full, "title": clean, "location": "", "url": full})
    return result


def _discover_ats(html: str) -> list[tuple[str, str]]:
    """Discover public ATS boards embedded in otherwise-JS career pages.

    Returns (adapter, identifier) pairs. This is deliberately conservative: it
    only follows URLs that identify a public job-board endpoint, not arbitrary
    links on the page.
    """
    found: list[tuple[str, str]] = []

    def add(adapter: str, value: str):
        value = unescape(value).strip().strip('/')
        pair = (adapter, value)
        if value and pair not in found:
            found.append(pair)

    for value in re.findall(r'https?://(?:boards-api\.greenhouse\.io/v1/boards/|boards\.greenhouse\.io/)([A-Za-z0-9_-]+)', html, re.I):
        add("greenhouse", value)
    for value in re.findall(r'https?://jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)', html, re.I):
        add("ashby", value)
    for value in re.findall(r'https?://(?:api\.lever\.co/v0/postings/|jobs\.lever\.co/)([A-Za-z0-9_-]+)', html, re.I):
        add("lever", value)
    for value in re.findall(r'https?://(?:api\.smartrecruiters\.com/v1/companies/|jobs\.smartrecruiters\.com/)([A-Za-z0-9_-]+)', html, re.I):
        add("smartrecruiters", value)
    for value in re.findall(r'https?://apply\.workable\.com/([A-Za-z0-9_-]+)', html, re.I):
        add("workable", value)
    return found


def fetch_career_page(url: str) -> list[dict]:
    with _client() as client:
        response = client.get(url); response.raise_for_status(); html = response.text
    result = _jsonld_jobs(html, url)
    for job in _embedded_jobs(html, url) + _career_link_jobs(html, url):
        if not any(x["url"] == job["url"] for x in result): result.append(job)

    # Many modern career pages are only a shell around an ATS. If the shell
    # contains no directly parseable jobs, query the embedded public board.
    if not result:
        for adapter, identifier in _discover_ats(html):
            try:
                if adapter == "greenhouse": result = fetch_greenhouse(identifier)
                elif adapter == "ashby": result = fetch_ashby(identifier)
                elif adapter == "lever": result = fetch_lever(identifier)
                elif adapter == "smartrecruiters": result = fetch_smartrecruiters(identifier)
                elif adapter == "workable": result = fetch_workable(identifier)
            except Exception:
                continue
            if result:
                break
    return result


def fetch_source(item: dict) -> list[dict]:
    """Use configured adapters, with overrides for known ATS migrations."""
    key = item.get("name", "").lower()
    if "sylvera" in key: return fetch_ashby("sylvera")
    if "iceye" in key: return fetch_workable("iceye")
    if "spire global" in key: return fetch_greenhouse("spire")
    if "planet" in key: return fetch_greenhouse("planetlabs")
    if "maxar" in key: return fetch_career_page("https://vantor.com/careers")
    if key == "beam": return fetch_career_page("https://careers.beam.global/en-GB/jobs")
    if "rovco" in key: return fetch_career_page("https://rovco.com/careers")
    if "aurora energy research" in key: return fetch_career_page("https://auroraer.com/careers/join-us")
    if "storegga" in key: return fetch_career_page("https://storegga.earth/careers")
    if "reach subsea" in key: return fetch_career_page("https://www.reachsubsea.com/careers/")
    if key == "bp": return fetch_workday("bpinternational.wd3.myworkdayjobs.com", "bpCareers", item.get("url"), tenant="bpinternational")
    if key == "shell": return fetch_workday("shell.wd3.myworkdayjobs.com", "ShellCareers", item.get("url"), tenant="shell")
    if "viridien" in key: return fetch_workday("cgg.wd103.myworkdayjobs.com", "viridiencareers", item.get("url"), tenant="cgg")
    if key == "aker bp": return fetch_akerbp()
    if "dalcour maclaren" in key: return fetch_workday("dalcourmaclaren.wd3.myworkdayjobs.com", "Dalcour-Maclaren-Careers", item.get("url"), tenant="dalcourmaclaren")
    if key == "aecom": return fetch_smartrecruiters("AECOM2")
    if key == "mott macdonald": return fetch_mott()
    if key == "arup": return fetch_career_page("https://jobs.arup.com/")
    if "atkinsréalis" in key or "atkinsrealis" in key: return fetch_career_page("https://www.careers.atkinsrealis.com/en/jobs")
    if key == "wsp": return fetch_career_page("https://www.wsp.com/en-gb/careers/job-opportunities?country=GB")
    if key == "jacobs": return fetch_career_page("https://www.jacobs.com/careers-jacobs")
    if "moody's" in key or "moodys" in key: return fetch_career_page("https://careers.moodys.com/en/search_jobs")
    if key == "totalenergies": return fetch_career_page("https://careers.totalenergies.com/en/search/grid/Jobs%20at%20TotalEnergies")
    if key == "ramboll": return fetch_smartrecruiters("Ramboll3")
    if key == "verisk": return fetch_smartrecruiters("Verisk")

    adapter = item.get("adapter", "career_page")
    if adapter == "greenhouse": return fetch_greenhouse(item["token"])
    if adapter == "ashby": return fetch_ashby(item["token"])
    if adapter == "workable": return fetch_workable(item["token"])
    if adapter == "lever": return fetch_lever(item["token"])
    if adapter == "smartrecruiters": return fetch_smartrecruiters(item["token"])
    if adapter == "workday": return fetch_workday(item.get("host", ""), item.get("path", ""), item.get("url"), tenant=item.get("tenant"))
    if adapter == "career_page": return fetch_career_page(item["url"])
    raise ValueError(f"Unsupported adapter: {adapter}")
