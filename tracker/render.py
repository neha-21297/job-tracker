# tracker/render.py
import json
import pathlib


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
      })
  rows.sort(key=lambda r: r["first_seen"], reverse=True)
  out = pathlib.Path("docs/jobs.json")
  out.parent.mkdir(parents=True, exist_ok=True)
  out.write_text(json.dumps({"jobs": rows}, indent=1))