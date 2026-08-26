# tracker/poll.py
import re
import httpx
import yaml
from tracker.diff import diff, load_state, save_state
from tracker.notify import alert_batch
from tracker.render import render_dashboard


def matches_rules(title: str, rules: dict) -> bool:
  for inc in rules.get("include_title", []):
    if re.search(inc, title):
      for exc in rules.get("exclude_title", []):
        if re.search(exc, title):
          return False
      return True
  return False


def fetch_greenhouse(token: str, rules: dict) -> list:
  url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
  res = httpx.get(url, timeout=15)
  if res.status_code != 200:
    return []
  out = []
  for j in res.json().get("jobs", []):
    title = j.get("title", "")
    if matches_rules(title, rules):
      loc = (j.get("location") or {}).get("name", "")
      out.append({
          "id": j.get("id"),
          "title": title,
          "location": loc,
          "url": j.get("absolute_url"),
      })
  return out


def fetch_lever(token: str, rules: dict) -> list:
  url = f"https://api.lever.co/v0/postings/{token}?mode=json"
  res = httpx.get(url, timeout=15)
  if res.status_code != 200:
    return []
  out = []
  for j in res.json():
    title = j.get("text", "")
    if matches_rules(title, rules):
      loc = (j.get("categories") or {}).get("location", "")
      out.append({
          "id": j.get("id"),
          "title": title,
          "location": loc,
          "url": j.get("hostedUrl"),
      })
  return out


def fetch_smartrecruiters(token: str, rules: dict) -> list:
  url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
  res = httpx.get(url, timeout=15)
  if res.status_code != 200:
    return []
  out = []
  for j in res.json().get("content", []):
    title = j.get("name", "")
    if matches_rules(title, rules):
      loc = (j.get("location") or {}).get("city", "")
      out.append({
          "id": j.get("id"),
          "title": title,
          "location": loc,
          "url": f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
      })
  return out


def main():
  with open("config/sources.yaml", "r") as f:
    sources = yaml.safe_load(f) or {}
  with open("config/rules.yaml", "r") as f:
    rules = yaml.safe_load(f) or {}

  state = load_state()

  for src_key, meta in sources.items():
    adapter = meta.get("adapter")
    token = meta.get("token")
    fetched = []

    try:
      if adapter == "greenhouse" and token:
        fetched = fetch_greenhouse(token, rules)
      elif adapter == "lever" and token:
        fetched = fetch_lever(token, rules)
      elif adapter == "smartrecruiters" and token:
        fetched = fetch_smartrecruiters(token, rules)
    except Exception as e:
      print(f"Error polling {src_key}: {e}")
      continue

    if fetched:
      new_jobs, reopened = diff(src_key, fetched, state)
      if new_jobs:
        alert_batch(meta.get("name", src_key), new_jobs)

  save_state(state)
  render_dashboard(state, sources)


if __name__ == "__main__":
  main()