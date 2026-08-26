import hashlib
import json
import pathlib
from datetime import datetime, timezone

STATE = pathlib.Path("data/state.json")
LOG = pathlib.Path("data/jobs.ndjson")


def load_state() -> dict:
  return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict) -> None:
  STATE.parent.mkdir(parents=True, exist_ok=True)
  STATE.write_text(json.dumps(state, indent=1, sort_keys=True))


def now() -> str:
  return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_hash(job) -> str:
  blob = f"{job['title']}|{job.get('location', '')}"
  return hashlib.sha256(blob.encode()).hexdigest()[:16]


def diff(source_key: str, fetched: list, state: dict):
  prev = state.setdefault(source_key, {})
  cold_start = not prev
  new, reopened = [], []

  for j in fetched:
    jid = str(j["id"])
    rec = prev.get(jid)
    h = content_hash(j)
    if rec is None:
      prev[jid] = {
          "h": h,
          "first_seen": now(),
          "title": j["title"],
          "url": j.get("url", ""),
          "location": j.get("location", ""),
      }
      if not cold_start:
        new.append(j)
    elif rec.get("closed"):
      rec.pop("closed")
      rec["h"] = h
      rec["reopened"] = now()
      reopened.append(j)
  return new, reopened