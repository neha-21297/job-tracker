# tracker/poll.py
from __future__ import annotations

import argparse
import re

import yaml

from tracker.adapters import fetch_source
from tracker.diff import diff, load_state, save_state
from tracker.notify import alert_batch
from tracker.render import render_dashboard


def _load_rules() -> dict:
    try:
        with open("config/rules.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _matches_any(patterns: list[str], value: str) -> bool:
    return any(re.search(pattern, value or "") for pattern in patterns)


def _is_relevant_job(job: dict, rules: dict) -> bool:
    """Keep UK graduate/early-career and relevant geoscience/risk roles only."""
    title = str(job.get("title", ""))
    location = str(job.get("location", ""))

    include_title = rules.get("include_title", [])
    exclude_title = rules.get("exclude_title", [])
    uk_locations = rules.get("locations", [])

    if exclude_title and _matches_any(exclude_title, title):
        return False
    if include_title and not _matches_any(include_title, title):
        return False

    # Do not treat a bare "Remote" label as UK. That was the reason US remote
    # vacancies from Planet/Beam leaked into the tracker.
    if uk_locations and not _matches_any(uk_locations, location):
        return False

    return True


def run_poll(tiers: list[str]):
    try:
        with open("config/sources.yaml", "r", encoding="utf-8") as f:
            sources = yaml.safe_load(f) or {}
    except FileNotFoundError:
        print("Warning: config/sources.yaml not found. Creating empty config.")
        sources = {}

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
        print(f"Polling {company} ({adapter}, tier {tier})...")

        try:
            fetched = fetch_source(item)
            jobs = [job for job in fetched if _is_relevant_job(job, rules)]
            print(f"-> {len(fetched)} fetched, {len(jobs)} relevant UK roles")

            new_jobs, reopened_jobs, _ = diff(source_key, jobs, state)

            if new_jobs:
                print(f"-> Found {len(new_jobs)} new roles for {company}")
                alert_batch(company, new_jobs, priority=1)

            if reopened_jobs:
                print(f"-> Found {len(reopened_jobs)} reopened roles for {company}")
                alert_batch(company, reopened_jobs, priority=2)

        except Exception as exc:
            # One broken careers page must not prevent every other company
            # from being checked.
            print(f"Error while polling {company}: {exc}")

    save_state(state)
    render_dashboard(state, sources)
    print("Poll run completed successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Poll configured company job sources")
    parser.add_argument(
        "--tier",
        type=str,
        default="1,2,3",
        help="Comma-separated source tiers to run",
    )
    args = parser.parse_args()
    selected_tiers = [t.strip() for t in args.tier.split(",") if t.strip()]
    run_poll(selected_tiers)
