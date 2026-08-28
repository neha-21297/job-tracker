# tracker/poll.py
from __future__ import annotations

import argparse
import re

import yaml

from tracker.adapters import fetch_source
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


def run_poll(tiers: list[str]):
    sources = _load_yaml("config/sources.yaml")
    rules = _load_rules()
    state = load_state()

    for source_key, item in sources.items():
        if not isinstance(item, dict):
            continue
        tier = str(item.get("tier", ""))
        if tier not in tiers:
            continue

        company = item.get("name", source_key)
        adapter = item.get("adapter", "career_page")
        source_for_filter = dict(item)
        if str(company).lower() == "aker bp":
            source_for_filter["allow_non_uk"] = True

        print(f"Polling {company} ({adapter}, tier {tier})...")

        try:
            fetched = fetch_source(item)
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

        except Exception as exc:
            print(f"Error while polling {company}: {exc}")

    save_state(state)
    render_dashboard(state, sources)
    print("Poll run completed successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Poll configured company job sources")
    parser.add_argument("--tier", type=str, default="1,2,3", help="Comma-separated source tiers to run")
    args = parser.parse_args()
    selected_tiers = [t.strip() for t in args.tier.split(",") if t.strip()]
    run_poll(selected_tiers)
