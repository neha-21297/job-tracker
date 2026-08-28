# tracker/poll.py
import argparse
import sys
import yaml
from tracker.adapters import fetch_greenhouse
from tracker.diff import diff, load_state, save_state
from tracker.notify import alert_batch
from tracker.render import render_dashboard


def run_poll(tiers: list):
    try:
        with open("config/sources.yaml", "r") as f:
            sources = yaml.safe_load(f) or {}
    except FileNotFoundError:
        print("Warning: config/sources.yaml not found. Creating empty config.")
        sources = {}

    state = load_state()

    # Tier 1 & 2: Public ATS Endpoints (e.g., Greenhouse)
    if "1" in tiers or "2" in tiers:
        greenhouse_boards = sources.get("greenhouse", [])
        for item in greenhouse_boards:
            company = item.get("name")
            token = item.get("token")
            if not company or not token:
                continue

            print(f"Polling {company} (Greenhouse)...")
            try:
                jobs = fetch_greenhouse(token)
                new_jobs, reopened_jobs, _ = diff(company, jobs, state)

                # Send email notifications for newly detected postings
                if new_jobs:
                    print(f"-> Found {len(new_jobs)} new roles for {company}")
                    alert_batch(company, new_jobs, priority=1)
                
                if reopened_jobs:
                    print(f"-> Found {len(reopened_jobs)} reopened roles for {company}")
                    alert_batch(company, reopened_jobs, priority=2)

            except Exception as exc:
                print(f"Error while polling {company}: {exc}")

    # Persist updated state to data/state.json and rebuild docs/jobs.json
    save_state(state)
    render_dashboard(state)
    print("Poll run completed successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Poll ATS feeds for new job postings")
    parser.add_argument(
        "--tier",
        type=str,
        default="1,2",
        help="Comma-separated tiers to run (e.g., --tier 1,2 or --tier 3)",
    )
    args = parser.parse_args()

    selected_tiers = [t.strip() for t in args.tier.split(",")]
    run_poll(selected_tiers)