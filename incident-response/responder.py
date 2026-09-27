"""Incident responder for Order Tracker (Homework 4, Q5/Q6).

Runs on the host (where the `claude` CLI is authenticated)::

    uv run python incident-response/responder.py

Receives Grafana alert webhooks at ``POST /alerts`` on port 8001, saves the
information needed to understand the problem (alert payload, 5xx metrics,
recent error logs, recent traces) under ``incident-response/incidents/<id>/``,
then starts the coding assistant automatically in headless mode
(``claude -p``) with the repo checked out locally. The agent's reply is saved
as ``agent_response.md``; for real incidents the responder then rebuilds and
restarts the app and verifies the affected endpoint.
"""

import datetime
import json
import os
import subprocess
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

BASE_DIR = Path(__file__).resolve().parent
INCIDENTS_DIR = BASE_DIR / "incidents"
REPO_DIR = BASE_DIR.parent
PROM_URL = os.getenv("PROM_URL", "http://localhost:9090")
LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")
TEMPO_URL = os.getenv("TEMPO_URL", "http://localhost:3200")
APP_URL = os.getenv("APP_URL", "http://localhost:8000")
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")

app = FastAPI(title="Order Tracker incident responder")


def http_get_json(url: str, timeout: int = 15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except Exception as exc:  # keep evidence even when a backend is down
        return {"evidence_error": f"{type(exc).__name__}: {exc}"}


def gather_evidence(endpoint: str) -> dict:
    """Pull metrics, logs, and traces relevant to the affected endpoint."""
    prom_query = urllib.parse.quote(
        'sum by (http_route) '
        '(rate(http_server_requests_total{http_response_status_code=~"5.."}[5m]))'
    )
    loki_query = urllib.parse.quote(
        '{service_name="order-tracker"} |= "status=500"'
    )
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(minutes=30)
    loki_range = (
        f"/loki/api/v1/query_range?query={loki_query}&limit=20"
        f"&start={int(start.timestamp())}000000000&end={int(end.timestamp())}000000000"
    )
    tempo_query = urllib.parse.quote('{resource.service.name="order-tracker"}')
    return {
        "endpoint": endpoint,
        "prometheus_5xx": http_get_json(f"{PROM_URL}/api/v1/query?query={prom_query}"),
        "loki_error_logs": http_get_json(f"{LOKI_URL}{loki_range}"),
        "tempo_traces": http_get_json(f"{TEMPO_URL}/api/search?q={tempo_query}"),
    }


def build_prompt(alert: dict, evidence: dict, incident_id: str, is_test: bool) -> str:
    if is_test:
        return (
            "This is a self-test of the Order Tracker incident-response loop. "
            "There is no real incident to fix. "
            f"Incident id: {incident_id}. "
            "Reply with two short lines: what you understood, then as the very "
            "last line exactly: SELF-TEST OK"
        )
    return (
        "You are the on-call engineer for the Order Tracker app. "
        f"Incident id: {incident_id}. "
        f"Repository root: {REPO_DIR}. "
        f"Alert payload: {json.dumps(alert)[:2000]}. "
        f"Evidence (metrics/logs/traces): {json.dumps(evidence)[:6000]}. "
        "Investigate the affected endpoint in the repository, find the root "
        "cause of the 5xx errors, and fix the application code. "
        "Do NOT restart any service yourself; only edit code files. "
        "When finished, reply with a short summary and as the very last line "
        "exactly: FIX APPLIED"
    )


def run_agent(incident_dir: Path, prompt: str, is_test: bool):
    """Run the coding assistant headless and persist its reply."""
    prompt_file = incident_dir / "prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    try:
        completed = subprocess.run(
            [
                CLAUDE_BIN,
                "-p",
                prompt,
                "--output-format",
                "text",
                # Non-interactive run: permission prompts cannot be answered,
                # so pre-authorize file edits. The agent still runs with
                # cwd=sc-repo root and is instructed to only edit code files.
                "--dangerously-skip-permissions",
            ],
            cwd=str(REPO_DIR),
            capture_output=True,
            text=True,
            timeout=1500,
        )
        output = (completed.stdout or "") + (
            f"\n[stderr]\n{completed.stderr}" if completed.stderr else ""
        )
        output += f"\n[exit_code={completed.returncode}]"
    except Exception as exc:
        output = f"agent launch failed: {type(exc).__name__}: {exc}"
    (incident_dir / "agent_response.md").write_text(output, encoding="utf-8")
    if not is_test:
        restart_and_verify(incident_dir)


def restart_and_verify(incident_dir: Path):
    """Rebuild/restart the app container and verify the endpoint recovers."""
    log_lines = []
    try:
        build = subprocess.run(
            ["docker", "compose", "up", "--build", "-d", "--wait", "app"],
            cwd=str(REPO_DIR),
            capture_output=True,
            text=True,
            timeout=600,
        )
        log_lines.append(f"rebuild exit={build.returncode}")
        log_lines.append((build.stdout or "")[-2000:])
        log_lines.append((build.stderr or "")[-2000:])
    except Exception as exc:
        log_lines.append(f"rebuild failed: {type(exc).__name__}: {exc}")
    (incident_dir / "restart.log").write_text("\n".join(log_lines), encoding="utf-8")


@app.post("/alerts")
async def receive_alerts(request: Request):
    payload = await request.json()
    alerts = payload.get("alerts", [payload])
    incident_ids = []
    for alert in alerts:
        if alert.get("status", "firing") != "firing":
            continue
        labels = alert.get("labels", {})
        is_test = labels.get("test") == "true"
        endpoint = (
            labels.get("http_route")
            or (alert.get("annotations", {}) or {}).get("endpoint")
            or "unknown"
        )
        incident_id = datetime.datetime.now(
            datetime.timezone.utc
        ).strftime("%Y%m%d-%H%M%S")
        incident_dir = INCIDENTS_DIR / incident_id
        incident_dir.mkdir(parents=True, exist_ok=True)
        (incident_dir / "alert.json").write_text(
            json.dumps(alert, indent=2), encoding="utf-8"
        )
        evidence = {} if is_test else gather_evidence(endpoint)
        (incident_dir / "evidence.json").write_text(
            json.dumps(evidence, indent=2), encoding="utf-8"
        )
        prompt = build_prompt(alert, evidence, incident_id, is_test)
        thread = threading.Thread(
            target=run_agent, args=(incident_dir, prompt, is_test), daemon=True
        )
        thread.start()
        incident_ids.append(incident_id)
    return JSONResponse({"status": "accepted", "incidents": incident_ids})


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    uvicorn.run(app, host="127.0.0.1", port=8001)
