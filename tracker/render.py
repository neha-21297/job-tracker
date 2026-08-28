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
            })

    future = []
    for key, rec in _load_future_openings().items():
        if not isinstance(rec, dict):
            continue
        future.append({
            "company": rec.get("company", key),
            "programme": rec.get("programme", "Graduate programme"),
            "opening": rec.get("opening", ""),
            "status": rec.get("status", "Expected to open"),
            "url": rec.get("url", ""),
            "source": rec.get("source", ""),
        })

    rows.sort(key=lambda r: r["first_seen"], reverse=True)
    future.sort(key=lambda r: r["opening"])

    out = pathlib.Path("docs/jobs.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"jobs": rows, "future_openings": future}, indent=1), encoding="utf-8")
