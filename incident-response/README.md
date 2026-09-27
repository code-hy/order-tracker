# Incident responder (Homework 4, Q5/Q6)

Host-run FastAPI service that receives Grafana alert webhooks and starts the
coding assistant headless.

## Run it

```bash
uv run python incident-response/responder.py
```

Listens on `127.0.0.1:8001`. Grafana (in Docker) reaches it at
`http://host.docker.internal:8001/alerts`.

Requires the `claude` CLI authenticated on the host (`claude -p` must work).

## Endpoints

- `POST /alerts` — Grafana webhook payload (`{"alerts": [...]}`). Saves
  `incidents/<id>/alert.json`, gathers Prometheus 5xx metrics, Loki error
  logs, and Tempo traces into `evidence.json`, then runs `claude -p`
  headless in a background thread. The reply lands in `agent_response.md`.
  For real incidents the responder then rebuilds/restarts the app
  (`docker compose up --build -d --wait app`) and records `restart.log`.
- `GET /health` — liveness check.

## Test it (Q5)

```bash
curl -X POST http://localhost:8001/alerts \
  -H 'Content-Type: application/json' \
  -d '{"alerts":[{"status":"firing","labels":{"alertname":"ResponderTest","test":"true"},"annotations":{"summary":"Test notification; no incident to fix"}}]}'
```

Then read `incident-response/incidents/<id>/agent_response.md`.
