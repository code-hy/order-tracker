import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from app import telemetry

telemetry.configure()


DB_PATH = Path(os.getenv("ORDER_DB_PATH", "data/orders.db"))
STATUSES = {"received", "preparing", "shipped", "delivered"}


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with connect() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                customer TEXT NOT NULL,
                item TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        if db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0:
            now = datetime.now(timezone.utc)
            previous_month_end = now.replace(day=1) - timedelta(days=1)
            for order in (
                ("standard-1001", "Avery", "Notebook", "standard", "received", now),
                ("express-1002", "Sam", "Headphones", "express", "preparing", previous_month_end),
                ("standard-1003", "Riley", "Water bottle", "standard", "shipped", now),
            ):
                db.execute(
                    "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
                    (*order[:5], order[5].isoformat()),
                )


def as_dict(row):
    return dict(row) if row else None


def order_detail(row):
    order = as_dict(row)
    if order["priority"] == "express":
        placed_at = datetime.fromisoformat(order["created_at"])
        estimated_at = placed_at.replace(day=placed_at.day + 2)
        order["estimated_delivery"] = estimated_at.date().isoformat()
    return order


class NewOrder(BaseModel):
    customer: str = Field(min_length=1, max_length=80)
    item: str = Field(min_length=1, max_length=120)
    priority: str = "standard"


class StatusUpdate(BaseModel):
    status: str


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield
    telemetry.shutdown()


app = FastAPI(title="Order Tracker", lifespan=lifespan)


class TelemetryMiddleware(BaseHTTPMiddleware):
    """Record an OTel span, log record, and request counter for each request.

    The counter carries ``http.route`` (route template) and
    ``http.response.status_code`` so metrics can be sliced per endpoint and
    outcome, e.g. order lookups on ``/api/orders/{order_id}``.
    """

    async def dispatch(self, request: Request, call_next):
        route = telemetry.lookup_route_template(request.method, request.url.path)
        order_id = None
        if route == telemetry.ORDER_LOOKUP_ROUTE:
            order_id = request.url.path.rsplit("/", 1)[-1]
        attributes = {"http.method": request.method, "http.route": route}
        with telemetry.tracer.start_as_current_span(
            f"{request.method} {route}", attributes=attributes
        ) as span:
            if order_id:
                span.set_attribute("order.id", order_id)
            try:
                response = await call_next(request)
                status = response.status_code
            except Exception as exc:
                status = 500
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.set_attribute("http.response.status_code", status)
                telemetry.request_counter.add(
                    1,
                    {"http.route": route, "http.response.status_code": status},
                )
                telemetry.logger.exception(
                    "order lookup failed method=%s route=%s order_id=%s status=%s",
                    request.method,
                    route,
                    order_id,
                    status,
                )
                raise
            span.set_attribute("http.response.status_code", status)
            if status >= 500:
                span.set_status(Status(StatusCode.ERROR, f"HTTP {status}"))
            telemetry.request_counter.add(
                1, {"http.route": route, "http.response.status_code": status}
            )
            telemetry.logger.info(
                "order lookup method=%s route=%s order_id=%s status=%s",
                request.method,
                route,
                order_id,
                status,
            )
            return response


app.add_middleware(TelemetryMiddleware)


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent.parent / "static" / "index.html")


@app.get("/healthz")
def health():
    with connect() as db:
        db.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/orders")
def list_orders():
    with connect() as db:
        rows = db.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    return [as_dict(row) for row in rows]


@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Order not found")
    return order_detail(row)


@app.post("/api/orders", status_code=201)
def create_order(order: NewOrder):
    if order.priority not in {"standard", "express"}:
        raise HTTPException(422, "Priority must be standard or express")
    order_id = str(uuid4())
    with connect() as db:
        db.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, order.customer, order.item, order.priority, "received",
             datetime.now(timezone.utc).isoformat()),
        )
    return get_order(order_id)


@app.patch("/api/orders/{order_id}")
def update_status(order_id: str, update: StatusUpdate):
    if update.status not in STATUSES:
        raise HTTPException(422, "Invalid status")
    with connect() as db:
        cursor = db.execute(
            "UPDATE orders SET status = ? WHERE id = ?",
            (update.status, order_id),
        )
    if cursor.rowcount == 0:
        raise HTTPException(404, "Order not found")
    return get_order(order_id)
