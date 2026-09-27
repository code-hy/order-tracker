# Study Guide — Lesson 4: DevOps and Observability for AI-Built Apps

A beginner-friendly guide to every concept used in Homework 4
(Order Tracker). No prior DevOps experience assumed. Each section ends with
*where to see it* in this repository.

---

## 1. The big picture: what this lesson is really about

AI coding assistants can generate a working app in minutes, but "it runs on my
machine" is not the same as "it runs reliably for users." Lesson 4 teaches the
**operations half** of software:

- **DevOps**: the practices that keep an app running, deployable, and
  recoverable — containers, health checks, reproducible environments,
  automated rebuilds.
- **Observability**: the ability to answer "what is happening inside my app
  right now?" using data the app itself emits — not by guessing, and not by
  staring at a web page that "looks okay."
- **Incident response**: what happens when something breaks — detect
  automatically (alerts), investigate with evidence, fix, verify, and record
  what you learned.

The homework's storyline: a customer says "I cannot open an order," the site
looks fine, and you must prove what's wrong with data, get paged automatically
next time, and let an agent fix it. That storyline is the whole lesson.

---

## 2. DevOps foundations (Q1)

### 2.1 Containers and Docker Compose

A **container** is a lightweight, isolated package containing your app plus
everything it needs to run (Python version, libraries). It behaves the same on
any machine, which kills "works on my machine" bugs.

- `Dockerfile` — the recipe for one container image (see `Dockerfile`: start
  from `python:3.12-slim`, install deps with `uv`, copy `app/` and `static/`,
  run uvicorn).
- `compose.yaml` — runs **multiple containers as one system** (a "stack"): the
  app plus, later, the whole observability backend. One command starts
  everything: `docker compose up --build -d --wait`.

*Where to see it:* `Dockerfile`, `compose.yaml`.

### 2.2 Health checks

A **health check** is a tiny endpoint whose only job is to say "I'm alive and
my dependencies work." Ours is `GET /healthz`, which runs `SELECT 1` against
the database and returns `{"status":"ok"}`. Docker and Kubernetes poll it and
restart or hold back traffic from unhealthy containers.

Related but different: our API also has `/ready`-style thinking (can I serve
traffic?) vs `/healthz` (am I alive?). In this repo they are folded into one
check; in larger systems they are separate.

*Where to see it:* `app/main.py` (`health()`), `compose.yaml` (`healthcheck:`).

### 2.3 Volumes and rebuilds

A **volume** is persistent storage that survives container restarts
(`orders:/data` keeps the SQLite file). **Rebuild** (`--build`) bakes new code
into a new image; **restart** alone re-runs the old image. Rule of thumb: code
change → rebuild; config hiccup → restart. The incident responder in Q6 does
`docker compose up --build -d --wait app` precisely because the fix is a code
change.

---

## 3. The three pillars of observability (Q2)

When the site "looks okay" but a customer is stuck, you need data in three
complementary shapes:

| Pillar | Answers | Example in Order Tracker |
|---|---|---|
| **Metrics** | *How many? How slow? How many failing?* — numbers over time | `http.server.requests`, counted per route + status code |
| **Logs** | *What happened, exactly?* — timestamped event records | `order lookup method=GET route=/api/orders/{order_id} order_id=... status=404` |
| **Traces** | *Where did one request spend its time?* — a tree of spans | span `GET /api/orders/{order_id}` with `order.id`, status attributes |

Key insight: the three pillars become powerful when **correlated** — our log
record carries the trace ID, so from a metric spike you can jump to the exact
logs and the exact trace (see Q3 verification).

### 3.1 Metrics, labels, and status codes

A **counter** metric only goes up (e.g. total requests). Each increment carries
**attributes** (key/value pairs) such as `http.route=/api/orders/{order_id}`
and `http.response.status_code=500`. Attributes let you slice: "show me only
5xx on the order-lookup route." Using the **route template** (`{order_id}`)
instead of the raw path matters — otherwise every order ID creates a new
metric series (a "cardinality explosion").

HTTP status families you must know: `2xx` success, `4xx` client error (e.g.
`404` not found — the app is fine, the thing asked for doesn't exist), `5xx`
server error (e.g. `500` — the app itself crashed; **these page people**).

### 3.2 Logs

Logs are discrete facts ("lookup X failed with 500 at 13:04"). Ours are
**structured** (consistent fields: method, route, order id, status) so machines
can filter them, not just humans reading text.

### 3.3 Traces and spans

A **trace** follows one request end-to-end; a **span** is one unit of work
inside it (here: the whole request handler). Spans carry attributes
(`order.id`, status code) and a status (`OK`/`ERROR`). If the app later calls a
database or another service, each gets a child span and you see *where* time
went.

---

## 4. OpenTelemetry: the standard way to emit signals (Q2–Q3)

**OpenTelemetry (OTel)** is the vendor-neutral standard for generating and
shipping metrics, logs, and traces, so you can swap backends without rewriting
app code. Vocabulary:

- **API + SDK**: your code calls the API; the SDK (configured once in
  `app/telemetry.py:configure()`) decides where data goes.
- **Tracer / Meter / Logger providers**: factories for spans, instruments, log
  records. Set them once at startup.
- **Instruments**: e.g. a `Counter` (`http.server.requests`).
- **Processors / readers**: batch and forward data (our metric reader exports
  every 5s to console, 15s to OTLP).
- **Exporters**: the destination adapter — `Console*` (stdout, for learning)
  vs `OTLP*` (network protocol for the pipeline).
- **Resource attributes**: metadata attached to everything, notably
  `service.name=order-tracker`, which is how backends know whose data this is.
- **Middleware**: code that wraps every request (`TelemetryMiddleware` in
  `app/main.py`) — the ideal place to start spans, count requests, and log
  outcomes, including catching unhandled exceptions as `500`s.

*Where to see it:* `app/telemetry.py` (providers, exporters, counter, route
template helper), `app/main.py` (`TelemetryMiddleware`, `lifespan` shutdown so
background export threads stop cleanly under tests).

---

## 5. The telemetry pipeline (Q3): Collector → Prometheus / Loki / Tempo → Grafana

Console output doesn't scale, so signals go to purpose-built storage:

```
app --OTLP--> [Collector] --+--> Prometheus (metrics)
                             +--> Loki       (logs)
                             +--> Tempo      (traces)
Grafana reads all three.
```

### 5.1 OpenTelemetry Collector

A single agent that **receives** (OTLP gRPC `:4317`/HTTP `:4318`), **processes**
(batch), and **exports**. Config is three lists plus **pipelines** that wire
them: traces→Tempo, metrics→Prometheus, logs→Loki. Lesson: backends change in
config, never in app code.

*Where to see it:* `otel-collector/config.yaml`. Gotcha we hit: retry storms in
collector logs mean a backend is unreachable — read them first when data is
missing.

### 5.2 Prometheus (metrics)

Prometheus **pulls** numbers from the collector's `:8889/metrics` endpoint
every 5s (`prometheus/prometheus.yml`) and stores time series. You query with
**PromQL**, e.g. error rate per route:

```promql
sum by (http_route) (rate(http_server_requests_total{http_response_status_code=~"5.."}[2m]))
```

`rate(...[2m])` = per-second average over 2 minutes. Dots in OTel attribute
names become underscores (`http.response.status_code` → label
`http_response_status_code`).

### 5.3 Loki (logs)

Loki stores logs indexed by **labels** (`service_name="order-tracker"`), queried
with **LogQL**: `{service_name="order-tracker"} |= "status=500"`. Labels are
for filtering; the message body holds details (including the trace ID that
links a log line to its trace).

### 5.4 Tempo (traces)

Tempo stores traces, looked up by trace ID or searched with **TraceQL**
(`{resource.service.name="order-tracker"}`). Gotcha we hit: Tempo 2.7 binds its
OTLP receivers to `localhost` by default, unreachable from other containers —
`tempo/tempo.yaml` sets `endpoint: 0.0.0.0:4317/4318`, and the container runs
as root so it can write its data volume.

### 5.5 Grafana (dashboards + Explore)

Grafana is the glass pane: **datasources** (one entry per backend,
`grafana/provisioning/datasources/`), a provisioned **dashboard**
(`Order Tracker requests`: request rate, 5xx rate, totals, logs), and
**Explore** for ad-hoc LogQL/TraceQL/PromQL. Everything is a file in the repo,
so the whole setup is reproducible with `docker compose up`.

---

## 6. Alerts: from graphs to notifications (Q4, Q6)

A dashboard never taps you on the shoulder. An **alert rule** continuously
evaluates a query and has a **state machine**:

- `inactive`/`Normal` — condition false (or no data, when configured so).
- `pending` — condition true but not yet for the whole `for:` duration (avoids
  paging on blips).
- `firing` — condition held for `for:` → notify.

Our rule (`grafana/provisioning/alerting/rules.yaml`): the 5xx-rate query `> 0`
`for: 1m`, evaluated every 30s, `noDataState: OK` (empty windows = healthy, not
"unknown"), with annotations naming the endpoint (`{{ $labels.http_route }}`),
the 2-minute window, and the dashboard link.

**Contact points** define *how* notification leaves Grafana; ours is a
**webhook** — an HTTP POST to the responder
(`grafana/provisioning/alerting/contactpoints.yaml`,
`http://host.docker.internal:8001/alerts`). A **notification policy** routes
which alerts go to which contact point. Key Q4 lesson: a `404` lookup leaves
the 5xx alert `Normal` — alerts fire on *user impact* (server errors), not on
every error-shaped response.

---

## 7. The incident-response loop (Q5–Q6)

```
5xx spike → alert fires → webhook → responder saves evidence
→ headless agent investigates + fixes → rebuild/restart → verify → alert Normal
```

### 7.1 The responder service

`incident-response/responder.py` (host-run on `:8001` so it can use the
engineer's own `claude` login): on `POST /alerts` it stores `alert.json`,
gathers an **evidence bundle** (`evidence.json`: current 5xx metrics from
Prometheus, recent `status=500` logs from Loki, recent traces from Tempo),
then launches the coding assistant **headless** (`claude -p`, background
thread so the webhook gets an instant `accepted`). The reply is saved as
`agent_response.md`; for real incidents it then rebuilds/restarts the app and
writes `restart.log`. It ignores `resolved` notifications so one incident =
one investigation.

### 7.2 Guardrails for autonomous agents

Two lessons from doing this for real:

1. A headless agent defaults to **read-only caution** — our first run
   correctly diagnosed the bug but refused to edit files. Non-interactive runs
   need pre-authorized edit scope (`--dangerously-skip-permissions`, repo-root
   working directory, "code files only" instruction).
2. Constrain the blast radius explicitly: fix code, don't restart services
   (the responder owns rollout + verification), and always verify with tests
   and the real endpoint before calling it done.

### 7.3 Evidence and memory

Every incident folder (`incident-response/incidents/<timestamp>/`) holds the
alert, evidence, prompt, agent reply, and restart log — committed to git. This
is the difference between "the model reasoned" and "the system remembers":
future engineers (and agents) can see what broke, what was observed, and what
fixed it.

---

## 8. Case study: the `express-1002` incident (Q6)

- **Symptom**: `GET /api/orders/express-1002` → `500` on every lookup.
- **Signal**: 5xx rate alert isolated to route `/api/orders/{order_id}`.
- **Root cause**: `order_detail()` computed the estimate with
  `placed_at.replace(day=placed_at.day + 2)`. The seeded express order sits on
  the **last day of the previous month**, so `day + 2` names a day that cannot
  exist (e.g. day 33) → `ValueError` → 500. Calendar-naive date math is the
  classic version of this bug.
- **Fix** (one line, `app/main.py`): `placed_at + timedelta(days=2)` — real
  date arithmetic that rolls over months/years. Verified: Aug 31 → Sep 2.
- **Regression tests** (`tests/test_api.py`): the seeded month-boundary order
  plus parameterized year-boundary/31st/30th/February cases (fail without the
  fix, pass with it: 8 passed).
- **Recovery proof**: endpoint returns 200, alert returns to `Normal`.

---

## 9. Commands cheat sheet

```bash
docker compose up --build -d --wait          # start everything
curl http://localhost:8000/healthz           # app health
curl -i http://localhost:8000/api/orders/standard-1001   # happy path (200)
curl -i http://localhost:8000/api/orders/standard-1002   # missing (404)
curl -i http://localhost:8000/api/orders/express-1002    # the incident (500→200)
docker compose logs app                      # console telemetry (Q2 stage)
uv run --frozen pytest -q                    # tests
uv run python incident-response/responder.py # responder on :8001
```

Grafana: `:3000` by default (`ORDER_TRACKER_GRAFANA_PORT=3001` if 3000 is busy
locally), admin/admin. Prometheus `:9090`, Loki `:3100`, Tempo `:3200`.

---

## 10. Glossary

**DevOps, container, image, volume, health check, rebuild vs restart,
observability, metric, counter, attribute/label, cardinality, log (structured),
trace, span, OpenTelemetry, OTel Collector (receiver/processor/exporter,
pipeline), OTLP, Prometheus/PromQL/rate, Loki/LogQL, Tempo/TraceQL,
Grafana/datasource/dashboard/Explore, alert rule, for/pending/firing,
noDataState, annotation, contact point, webhook, notification policy,
incident, evidence bundle, headless agent, regression test.**
