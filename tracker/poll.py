# tracker/poll.py
import argparse
import yaml
from tracker.adapters import fetch_greenhouse
from tracker.diff import diff, load_state, save_state
from tracker.notify import alert_batch
from tracker.render import render_dashboard


def run_poll(tiers: list[str]):
    try:
        with open("config/sources.yaml", "r", encoding="utf-8") as f:
            sources = yaml.safe_load(f) or {}
    except FileNotFoundError:
        print("Warning: config/sources.yaml not found. Creating empty config.")
        sources = {}

    state = load_state()

    # Greenhouse sources are represented as top-level mappings in
    # config/sources.yaml. Only poll entries explicitly configured for
    # Greenhouse; other ATS adapters can be added without breaking this run.
    if "1" in tiers or "2" in tiers:
        for source_key, item in sources.items():
            if not isinstance(item, dict) or item.get("adapter") != "greenhouse":
                continue

            company = item.get("name", source_key)
            token = item.get("token")
            if not token:
                print(f"Skipping {company}: Greenhouse token is missing.")
                continue

            print(f"Polling {company} (Greenhouse)...")
            try:
                jobs = fetch_greenhouse(token)
                new_jobs, reopened_jobs, _ = diff(company, jobs, state)

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
    parser = argparse.ArgumentParser(description="Poll ATS feeds for new job postings")
    parser.add_argument(
        "--tier",
        type=str,
        default="1,2",
        help="Comma-separated tiers to run (e.g., --tier 1,2 or --tier 3)",
    )
    args = parser.parse_args()

    selected_tiers = [t.strip() for t in args.tier.split(",") if t.strip()]
    run_poll(selected_tiers)
