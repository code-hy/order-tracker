## Summary

**Root cause** â€” `app/main.py:64` in `order_detail()`:

```python
estimated_at = placed_at.replace(day=placed_at.day + 2)
```

`datetime.replace()` validates its arguments rather than doing calendar arithmetic, so it raises `ValueError: day is out of range for month` whenever the +2 day estimate crosses a month boundary. The traceback matched exactly: `order_detail` â†’ `get_order` â†’ `ValueError: day is out of range for month`, on `GET /api/orders/{order_id}`.

**Why it looked like a spike but wasn't** â€” `init_db()` seeds `express-1002` with `created_at = previous_month_end` (line 44), always the *last* day of a month. For that date, `replace(day=last_day + 2)` can never be valid in the same month â€” the seeded express order 500'd on **every** lookup, not just near month end. The Prometheus 5xx ratio of ~0.045 tracks how often that one order was requested relative to all traffic, not a load-correlated fault. All other routes were unaffected because `order_detail()` only does this arithmetic for `priority == "express"`, which is why the alert isolated cleanly to the one route.

**Fix** â€” one line: `placed_at + timedelta(days=2)`. `timedelta` does real date arithmetic and rolls over month and year boundaries correctly. `timedelta` was already imported on line 4, so no import changes. Reproduced the failure as a literal `day 33 must be in range 1..31 for month 8`, and confirmed `GET /api/orders/express-1002` now returns 200 with `estimated_delivery: 2026-09-02`.

**Regression tests** â€” added to `tests/test_api.py`: an integration test on the seeding-dependent alert route, plus a parametrized unit test over the year-boundary (Jan 31), 31st, 30th, and short-month (Feb 28) cases. Confirmed they fail (5 failed) with the fix reverted and pass (8 passed) with it applied.

**No services restarted** â€” code files only, as instructed. Two notes worth flagging: the deploy is still running the old code until someone rolls it out, so the alert will keep firing until then; and this same bug was diagnosed under an earlier incident id (`20260927-130500`, per `incident-response/incidents/`), yet the fix is absent from the working tree â€” so that earlier remediation never landed in the branch, which is worth checking before treating this as closed.

FIX APPLIED

[exit_code=0]