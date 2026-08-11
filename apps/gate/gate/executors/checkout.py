"""Checkout (DESIGN.md Flow 2) and Stripe webhooks (Flows 2+3).

The invariants that carry the grade live here:
- The browser never supplies a price, total, currency, or tenant id: prices
  come from rental_items, the total is computed server-side in integer pence.
- Availability is enforced at the database: rental_items rows are locked
  (SELECT FOR UPDATE) and date-overlap is validated against PENDING+CONFIRMED
  reservations, so double-booking is impossible, not just unlikely.
- "One order, never two" is the DB's unique constraints; the webhook is
  deduped by stripe_event_id; amount/currency are compared against the
  persisted reservation, never trusted from the event.
"""

import logging
import os
import uuid
from datetime import UTC, date, datetime
from typing import Any

import stripe

from ..db import pool
from ..pipeline import ExecutorContext, ExecutorResult, GateError

log = logging.getLogger("gate.checkout")

CURRENCY = "gbp"


def _stripe_key() -> str:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key.startswith("sk_test_"):
        # Safety default (DESIGN.md sec. 1): test-mode keys only, ever.
        raise GateError(500, "STRIPE_SECRET_KEY must be a test-mode key (sk_test_...)")
    return key


def _rental_days(start: date, end: date) -> int:
    return (end - start).days + 1  # inclusive of both days


def create_stripe_session(
    *,
    reservation_id: str,
    line_items: list[dict[str, Any]],
    success_url: str,
    cancel_url: str,
) -> stripe.checkout.Session:
    """Isolated so tests can stub it; the executor never bypasses it."""
    client = stripe.StripeClient(_stripe_key())
    return client.checkout.sessions.create(
        params={
            "mode": "payment",
            "line_items": line_items,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "expires_at": int(datetime.now(UTC).timestamp()) + 3600,  # Flow 3: 1 hour
            "metadata": {"reservation_id": reservation_id},
        },
        options={"idempotency_key": f"resv-{reservation_id}"},
    )


def checkout_session_executor(payload: Any, ctx: ExecutorContext) -> ExecutorResult:
    return run_checkout(payload, tenant_id=ctx.tenant_id, seed_id=ctx.action_id)


def run_checkout(
    payload: Any, *, tenant_id: str, seed_id: str, synthetic: bool = False
) -> ExecutorResult:
    """Core checkout: also used by the synthetic go-live purchase check with
    synthetic=True, which flags the reservation (and therefore the order) so
    verification evidence never pollutes business reporting."""
    p = payload if isinstance(payload, dict) else {}
    items = p.get("items")
    customer = p.get("customer") or {}
    try:
        start = date.fromisoformat(p.get("startDate", ""))
        end = date.fromisoformat(p.get("endDate", ""))
    except ValueError as err:
        raise GateError(400, "startDate and endDate must be ISO dates") from err
    if end < start:
        raise GateError(400, "endDate must not be before startDate")
    if not isinstance(items, list) or not items:
        raise GateError(400, "items required")
    if not customer.get("name") or not customer.get("email"):
        raise GateError(400, "customer name and email required")

    # Deterministic reservation id from the action id: a crash-retry of the
    # same gated action re-finds its own reservation instead of double-holding
    # availability.
    reservation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"epyhia-reservation-{seed_id}"))
    days = _rental_days(start, end)

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM reservations WHERE id = %s",
            (reservation_id,),
        )
        if cur.fetchone() is None:
            normalized = customer["email"].strip().lower()
            cur.execute(
                """INSERT INTO customers (tenant_id, name, email, normalized_email)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (tenant_id, normalized_email)
                     DO UPDATE SET name = EXCLUDED.name
                   RETURNING id""",
                (tenant_id, customer["name"], customer["email"], normalized),
            )
            customer_id = cur.fetchone()["id"]

            # Lock the catalog rows, then check availability against
            # overlapping PENDING + CONFIRMED reservations (Flow 2 step 2).
            item_ids = [i.get("rentalItemId") for i in items]
            cur.execute(
                """SELECT id, name, available_qty, day_rate FROM rental_items
                    WHERE tenant_id = %s AND id = ANY(%s) FOR UPDATE""",
                (tenant_id, item_ids),
            )
            catalog = {str(r["id"]): r for r in cur.fetchall()}
            if len(catalog) != len(set(item_ids)):
                raise GateError(400, "one or more items do not exist for this business")

            total = 0
            for i in items:
                qty = i.get("qty")
                if not isinstance(qty, int) or qty <= 0:
                    raise GateError(400, "each item needs a positive integer qty")
                row = catalog[str(i["rentalItemId"])]
                cur.execute(
                    """SELECT COALESCE(SUM(ri.qty), 0) AS reserved
                         FROM reservation_items ri
                         JOIN reservations res ON ri.reservation_id = res.id
                        WHERE ri.rental_item_id = %s
                          AND res.status IN ('PENDING', 'CONFIRMED')
                          AND res.start_date <= %s AND res.end_date >= %s""",
                    (i["rentalItemId"], end, start),
                )
                reserved = cur.fetchone()["reserved"]
                if row["available_qty"] - reserved < qty:
                    raise GateError(
                        409,
                        f"not enough '{row['name']}' available for those dates "
                        f"(requested {qty}, free {row['available_qty'] - reserved})",
                    )
                total += qty * row["day_rate"] * days

            cur.execute(
                """INSERT INTO reservations
                     (id, tenant_id, customer_id, start_date, end_date, status, total, is_synthetic)
                   VALUES (%s, %s, %s, %s, %s, 'PENDING', %s, %s)""",
                (reservation_id, tenant_id, customer_id, start, end, total, synthetic),
            )
            for i in items:
                row = catalog[str(i["rentalItemId"])]
                cur.execute(
                    """INSERT INTO reservation_items
                         (reservation_id, rental_item_id, qty, day_rate)
                       VALUES (%s, %s, %s, %s)""",
                    (reservation_id, i["rentalItemId"], i["qty"], row["day_rate"]),
                )

    # Reservation is committed (PENDING holds availability); now the Stripe
    # session. Stripe's own idempotency key derives from the reservation id,
    # so a crash-retry returns the same session rather than a second charge
    # path (Flow 2 step 3).
    with pool.connection() as conn:
        resv = conn.execute(
            "SELECT total, stripe_checkout_session_id FROM reservations WHERE id = %s",
            (reservation_id,),
        ).fetchone()
        items_rows = conn.execute(
            """SELECT ri.qty, ri.day_rate, r.name
                 FROM reservation_items ri JOIN rental_items r ON ri.rental_item_id = r.id
                WHERE ri.reservation_id = %s""",
            (reservation_id,),
        ).fetchall()

    site = p.get("siteUrl") or "https://example.invalid"
    line_items = [
        {
            "price_data": {
                "currency": CURRENCY,
                "product_data": {"name": f"{r['name']} x{r['qty']} ({days} day(s))"},
                "unit_amount": r["day_rate"] * days,
            },
            "quantity": r["qty"],
        }
        for r in items_rows
    ]
    session = create_stripe_session(
        reservation_id=reservation_id,
        line_items=line_items,
        success_url=f"{site}/?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{site}/?checkout=cancelled",
    )

    with pool.connection() as conn:
        conn.execute(
            "UPDATE reservations SET stripe_checkout_session_id = %s WHERE id = %s",
            (session.id, reservation_id),
        )

    return ExecutorResult(
        provider_reference=f"{session.id}|{reservation_id}|{resv['total']}|{session.url}"
    )


def process_stripe_webhook(raw_body: bytes, signature: str) -> dict[str, Any]:
    """Verify the signature on the RAW body before any processing, dedupe by
    event id, and apply Flow 2 step 5 / Flow 3 in one transaction each."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise GateError(500, "STRIPE_WEBHOOK_SECRET not configured on the gate")
    try:
        stripe.Webhook.construct_event(raw_body, signature, secret)
    except (ValueError, stripe.error.SignatureVerificationError) as err:
        raise GateError(400, f"webhook signature verification failed: {err}") from err

    # Signature verified on the raw bytes; from here, work with the plain JSON.
    import json

    event = json.loads(raw_body)
    event_type = event["type"]
    if event_type not in ("checkout.session.completed", "checkout.session.expired"):
        return {"received": True, "ignored": event_type}

    session = event["data"]["object"]
    reservation_id = (session.get("metadata") or {}).get("reservation_id")
    if not reservation_id:
        return {"received": True, "ignored": "no reservation metadata"}

    with pool.connection() as conn, conn.cursor() as cur:
        # Dedupe: Stripe delivers at least once. A replayed event changes nothing.
        cur.execute(
            """INSERT INTO webhook_events (stripe_event_id) VALUES (%s)
               ON CONFLICT (stripe_event_id) DO NOTHING""",
            (event["id"],),
        )
        if cur.rowcount == 0:
            return {"received": True, "duplicate": True}

        cur.execute(
            "SELECT * FROM reservations WHERE id = %s FOR UPDATE",
            (reservation_id,),
        )
        resv = cur.fetchone()
        if resv is None:
            log.error("webhook %s references unknown reservation %s", event["id"], reservation_id)
            return {"received": True, "error": "unknown reservation"}

        if event_type == "checkout.session.expired":
            if resv["status"] == "PENDING":
                cur.execute(
                    "UPDATE reservations SET status = 'CANCELLED' WHERE id = %s",
                    (reservation_id,),
                )
            return {"received": True, "reservation": reservation_id, "status": "CANCELLED"}

        # checkout.session.completed
        if resv["status"] == "CONFIRMED":
            # A later event (new event id) for an already-confirmed reservation:
            # the order exists; nothing more to write.
            return {"received": True, "reservation": reservation_id, "status": "already CONFIRMED"}
        if session.get("payment_status") != "paid":
            log.warning("completed session %s not paid; no order written", session.get("id"))
            return {"received": True, "reservation": reservation_id, "status": "not paid"}

        # Verify money against OUR persisted reservation, not the event.
        if session.get("amount_total") != resv["total"] or session.get("currency") != CURRENCY:
            log.error(
                "AMOUNT MISMATCH reservation %s: ours %s %s, stripe %s %s - no order written",
                reservation_id,
                resv["total"],
                CURRENCY,
                session.get("amount_total"),
                session.get("currency"),
            )
            return {"received": True, "reservation": reservation_id, "status": "amount mismatch"}

        cur.execute(
            """INSERT INTO orders
                 (tenant_id, reservation_id, stripe_checkout_session_id, amount, currency,
                  status, payment_timestamp, is_synthetic)
               VALUES (%s, %s, %s, %s, %s, 'PAID', %s, %s)""",
            (
                resv["tenant_id"],
                reservation_id,
                session["id"],
                resv["total"],
                CURRENCY,
                datetime.fromtimestamp(event["created"], tz=UTC),
                resv["is_synthetic"],
            ),
        )
        cur.execute(
            "UPDATE reservations SET status = 'CONFIRMED' WHERE id = %s",
            (reservation_id,),
        )
        order_status = "CONFIRMED"

    return {"received": True, "reservation": reservation_id, "status": order_status}
