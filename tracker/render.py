# tracker/render.py
import json
import pathlib

import yaml


def _load_future_openings() -> dict:
    path = pathlib.Path("config/future_openings.yaml")
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def render_dashboard(state: dict, sources: dict) -> None:
    rows = []
    for src, jobs in state.items():
        company_name = sources.get(src, {}).get("name", src)
        for jid, rec in jobs.items():
            if rec.get("closed"):
                continue
            rows.append({
                "company": company_name,
                "title": rec.get("title", "Graduate Role"),
                "location": rec.get("location", "UK / Hybrid"),
                "first_seen": rec.get("first_seen", ""),
                "url": rec.get("url", ""),
                "status": "Open",
                "opening": "",
                "source_type": "live",
            })

    future = []
    for key, rec in _load_future_openings().items():
        if not isinstance(rec, dict):
            continue
        item = {
            "company": rec.get("company", key),
            "title": rec.get("programme", "Graduate programme"),
            "location": rec.get("location", "UK"),
            "first_seen": "",
            "url": rec.get("url", ""),
            "status": rec.get("status", "Expected to open"),
            "opening": rec.get("opening", ""),
            "source_type": "future",
            "source": rec.get("source", ""),
        }
        future.append({
            "company": item["company"],
            "programme": item["title"],
            "opening": item["opening"],
            "status": item["status"],
            "url": item["url"],
            "source": item["source"],
        })
        # Put future programmes into the same feed as live roles so the existing
        # dashboard can display them without a second data source.
        rows.append(item)

    rows.sort(key=lambda r: (r.get("source_type") != "future", r.get("opening", ""), r.get("first_seen", "")), reverse=False)

    out = pathlib.Path("docs/jobs.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"jobs": rows, "future_openings": future}, indent=1), encoding="utf-8")
