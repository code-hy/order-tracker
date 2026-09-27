# Order Tracker

A small order tracking app for the AI Dev Tools Zoomcamp observability homework. It includes a web page, API, tests, and a Docker Compose setup. You add telemetry, alerts, and an incident responder in Homework 4.

The main user flow is creating an order and checking its status. Three sample orders are created on first startup.

## Run it

You need Docker with Compose. To run the tests, you also need Python 3.11+ and `uv`.

```bash
docker compose up --build -d --wait
```

Open <http://127.0.0.1:8000>. The API is at `/api/orders`, and the health check is at `/healthz`. Data is stored in a Docker volume and survives container recreation.

If port 8000 is occupied, set `ORDER_TRACKER_PORT`, for example:

```bash
ORDER_TRACKER_PORT=18080 docker compose up --build -d --wait
```

Run tests with `uv run --frozen pytest -q`. Stop the app with `docker compose down`. Add `-v` only if you also want to delete the order data.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Web page |
| GET | `/healthz` | Database health check |
| GET | `/api/orders` | List orders |
| POST | `/api/orders` | Create an order |
| GET | `/api/orders/{id}` | Check an order |
| PATCH | `/api/orders/{id}` | Change an order status |

The app uses SQLite to keep setup small. Run one app container at a time. The course exercise is about detecting and handling an incident, not scaling the database.

## Homework 4: DevOps and observability (step-by-step)

This section documents the exact steps performed for HW4 in this repo
(`https://github.com/code-hy/order-tracker`).

Note: Grafana's compose port is overridable because port 3000 is taken by
another local app: `ORDER_TRACKER_GRAFANA_PORT=3001 docker compose up ...`.

### Q1: Run the app — `{"status":"ok"}`

```bash
docker compose up --build -d --wait
curl http://localhost:8000/healthz   # {"status":"ok"}
```

### Q2: Instrument order lookups — status `200`

`app/telemetry.py` configures OpenTelemetry metrics, logs, and traces with
console exporters; `app/main.py:TelemetryMiddleware` records a span, a log
record, and the `http.server.requests` counter carrying `http.route`
(`/api/orders/{order_id}`) and `http.response.status_code` for every request.

```bash
docker compose up --build -d --wait
curl -i http://localhost:8000/api/orders/standard-1001   # 200
docker compose logs app   # metric shows http.response.status_code: 200,
                          # plus the log record and GET /api/orders/{order_id} span
```

### Q3: Telemetry pipeline — status `404`

Added OTel Collector, Prometheus, Loki, Tempo, and Grafana to `compose.yaml`
(app exports OTLP to the collector; `OTEL_*_EXPORTER=otlp`). Configs:
`otel-collector/config.yaml`, `prometheus/prometheus.yml`, `loki/loki.yaml`
(single-binary TSDB), `tempo/tempo.yaml` (OTLP bound to `0.0.0.0`, container
run as root so it can use its data volume), Grafana datasources + the
`Order Tracker requests` dashboard (`grafana/provisioning/...`) with request
rate, 5xx errors, totals, and logs panels.

```bash
curl -i http://localhost:8000/api/orders/standard-1002   # 404, id does not exist
```

Verified: Prometheus `http_server_requests_total{http_route="/api/orders/{order_id}"}`
shows `404`; the matching log (with trace id) is in Loki; the trace is in
Tempo; the dashboard is provisioned in Grafana.

### Q4: Alert — `Normal`

`grafana/provisioning/alerting/rules.yaml` defines `Order Tracker 5xx errors`:
`sum by (http_route) (rate(http_server_requests_total{...=~"5.."}[2m])) > 0`
for 1m, `noDataState: OK`, with endpoint, window, and dashboard annotations.
After the Q3 lookup (a 404, not a 5xx) the rule state is `inactive`, i.e.
**Normal** — 404s don't fire a 5xx alert and empty windows resolve to OK.

### Q5: Automatic responder — last line `SELF-TEST OK`

`incident-response/responder.py` (host-run, `uv run python
incident-response/responder.py`, port 8001) accepts Grafana webhooks at
`POST /alerts`, saves `incidents/<id>/alert.json`, gathers 5xx metrics, error
logs, and traces into `evidence.json`, then launches `claude -p` headless
(non-interactive runs use `--dangerously-skip-permissions`, repo-root cwd)
and stores the reply in `agent_response.md`.

```bash
curl -X POST http://localhost:8001/alerts -H 'Content-Type: application/json' \
  -d '{"alerts":[{"status":"firing","labels":{"alertname":"ResponderTest","test":"true"},"annotations":{"summary":"Test notification; no incident to fix"}}]}'
```

The agent replied (incident `20260927-130107`), last line: **SELF-TEST OK**.

### Q6: Agent fixes the incident

Connected the alert via `grafana/provisioning/alerting/contactpoints.yaml`
(webhook `http://host.docker.internal:8001/alerts`, default policy routes to
it), then `curl -i http://localhost:8000/api/orders/express-1002` repeatedly
→ 500s → alert `pending` → `firing` → webhook → responder → agent.

Root cause: **the express delivery date calculation tried to use a day that
does not exist in that month** — `placed_at.replace(day=placed_at.day + 2)`
in `order_detail()`; the seeded express order sits on the last day of the
previous month, so every lookup 500'd. Fix (one line):
`placed_at + timedelta(days=2)`, plus regression tests in `tests/test_api.py`.
The responder rebuilt/restarted the app (`restart.log`, exit 0); now
`GET /api/orders/express-1002` → 200 with `estimated_delivery: 2026-09-02`,
`pytest` 8 passed, and the alert returned to `inactive` (Normal).

### Homework answers (summary)

1. `{"status":"ok"}`
2. `200`
3. `404`
4. `Normal`
5. Last line: `SELF-TEST OK`
6. The express delivery date calculation tried to use a day that does not exist in that month.
