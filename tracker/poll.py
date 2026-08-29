from __future__ import annotations

import argparse
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

from tracker.adapters import fetch_source
from tracker.enhanced_scraper import fetch_enhanced
from tracker.diff import diff, load_state, save_state
from tracker.notify import alert_batch
from tracker.render import render_dashboard


def _load_yaml(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _load_rules() -> dict:
    return _load_yaml("config/rules.yaml")


def _matches_any(patterns: list[str], value: str) -> bool:
    return any(re.search(pattern, value or "") for pattern in patterns)


def _is_relevant_job(job: dict, rules: dict, source: dict) -> bool:
    title = str(job.get("title", ""))
    location = str(job.get("location", ""))
    include_title = rules.get("include_title", [])
    exclude_title = rules.get("exclude_title", [])
    uk_locations = rules.get("locations", [])

    if exclude_title and _matches_any(exclude_title, title):
        return False
    if include_title and not _matches_any(include_title, title):
        return False
    allow_non_uk = bool(source.get("allow_non_uk"))
    if not allow_non_uk and uk_locations and not _matches_any(uk_locations, location):
        return False
    return True


def _fetch_one(source_key: str, item: dict) -> tuple[str, str, dict, list[dict] | None, str | None]:
    """Fetch one source, using the enhanced browser fallback when needed."""
    company = str(item.get("name", source_key))
    try:
        fetched = fetch_source(item)
        # A zero-result career page is ambiguous: the site may be JS-rendered,
        # iframe-based, or expose jobs only through a browser network API.
        if not fetched and str(item.get("adapter", "career_page")) == "career_page":
            fetched = fetch_enhanced(str(item.get("url", "")))
        return source_key, company, item, fetched, None
    except Exception as exc:
        return source_key, company, item, None, str(exc)


def run_poll(tiers: list[str]):
    sources = _load_yaml("config/sources.yaml")
    rules = _load_rules()
    state = load_state()
    failures = []
    empty_sources = []

    selected = []
    for source_key, item in sources.items():
        if not isinstance(item, dict):
            continue
        tier = str(item.get("tier", ""))
        if tier in tiers:
            selected.append((source_key, item))

    max_workers = min(8, max(1, len(selected)))
    print(f"Fetching {len(selected)} sources with {max_workers} concurrent workers...")

    fetched_results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_one, source_key, item): source_key
            for source_key, item in selected
        }
        for future in as_completed(futures):
            source_key = futures[future]
            fetched_results[source_key] = future.result()

    for source_key, item in selected:
        company = str(item.get("name", source_key))
        adapter = str(item.get("adapter", "career_page"))
        _, _, _, fetched, error = fetched_results[source_key]

        print(f"Polling {company} ({adapter}, tier {item.get('tier', '')})...")

        if error is not None:
            failures.append((company, error))
            print(f"Error while polling {company}: {error}")
            continue

        if fetched is None:
            failures.append((company, "source returned no result"))
            print(f"Error while polling {company}: source returned no result")
            continue

        if not fetched:
            empty_sources.append(company)
            print("-> WARNING: source returned 0 jobs; this is not treated as proof that the company has no vacancies")

        source_for_filter = dict(item)
        if company.lower() == "aker bp":
            source_for_filter["allow_non_uk"] = True

        jobs = [job for job in fetched if _is_relevant_job(job, rules, source_for_filter)]
        print(f"-> {len(fetched)} fetched, {len(jobs)} relevant roles")

        result = diff(source_key, jobs, state)
        new_jobs, reopened_jobs = result[0], result[1]

        if new_jobs:
            print(f"-> Found {len(new_jobs)} new roles for {company}")
            alert_batch(company, new_jobs, priority=1)
        if reopened_jobs:
            print(f"-> Found {len(reopened_jobs)} reopened roles for {company}")
            alert_batch(company, reopened_jobs, priority=2)

    save_state(state)
    render_dashboard(state, sources)

    print("Poll run completed.")
    if failures:
        print(f"Source failures: {len(failures)}")
        for company, error in failures:
            print(f"  - {company}: {error}")
    if empty_sources:
        print(f"Empty sources needing review: {len(empty_sources)}")
        print("  - " + ", ".join(empty_sources))
    if not failures and not empty_sources:
        print("All configured sources returned data successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Poll configured company job sources")
    parser.add_argument("--tier", type=str, default="1,2,3", help="Comma-separated source tiers to run")
    args = parser.parse_args()
    selected_tiers = [t.strip() for t in args.tier.split(",") if t.strip()]
    run_poll(selected_tiers)
