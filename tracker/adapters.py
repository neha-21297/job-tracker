# tracker/adapters.py
"""Adapters for public job-board APIs used by the tracker."""

from __future__ import annotations

import httpx


GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def fetch_greenhouse(token: str) -> list[dict]:
    """Fetch all currently published jobs from a Greenhouse board.

    Greenhouse's public boards API does not require authentication. The
    returned records are normal dictionaries so they can be consumed by the
    diff/state layer without any adapter-specific objects.
    """
    url = GREENHOUSE_URL.format(token=token)
    response = httpx.get(
        url,
        params={"content": "true"},
        timeout=30.0,
        follow_redirects=True,
    )
    response.raise_for_status()

    payload = response.json()
    jobs = payload.get("jobs", [])
    if not isinstance(jobs, list):
        raise ValueError(f"Unexpected Greenhouse response for board {token!r}")

    result = []
    for job in jobs:
        if not isinstance(job, dict) or "id" not in job or "title" not in job:
            continue

        location = (job.get("location") or {}).get("name", "")
        result.append(
            {
                "id": job["id"],
                "title": job["title"],
                "location": location,
                "url": job.get("absolute_url", ""),
            }
        )

    return result
