"""Checkout + webhook invariant tests against the real Neon dev database.
These are the two decisive rubric rows: a completed purchase persists exactly
one order, and retries/replays never double anything. Stripe's API is stubbed
(create_stripe_session); Stripe's SIGNATURE SCHEME is not - events are signed
and verified for real."""

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass

import pytest
from gate.db import pool
from gate.executors import checkout as checkout_mod
from gate.executors.checkout import checkout_session_executor, process_stripe_webhook
from gate.pipeline import GateError, PipelineConfig, request_action

SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]


@dataclass
class FakeSession:
    id: str
    url: str = "https://checkout.stripe.com/test/fake"


@pytest.fixture(autouse=True)
def stub_stripe(monkeypatch):
    def fake_create(*, reservation_id, line_items, success_url, cancel_url):
        return FakeSession(id=f"cs_test_{reservation_id[:18]}")

    monkeypatch.setattr(checkout_mod, "create_stripe_session", fake_create)


@pytest.fixture(scope="module")
def biz():
    """Tenant with a small catalog: 2 chairs and 1 tent."""
    with pool.connection() as conn:
        tenant = conn.execute(
            """INSERT INTO tenants (name, email, business_name, business_slug)
               VALUES ('Checkout Test', 'ct@example.com', 'Checkout Biz',
                       'ct-' || substr(md5(random()::text), 1, 12))
               RETURNING id"""
        ).fetchone()
        tid = str(tenant["id"])
        chair = conn.execute(
            """INSERT INTO rental_items (tenant_id, name, available_qty, day_rate)
               VALUES (%s, 'Chair', 2, 150) RETURNING id""",
            (tid,),
        ).fetchone()
        tent = conn.execute(
            """INSERT INTO rental_items (tenant_id, name, available_qty, day_rate)
               VALUES (%s, 'Tent', 1, 14000) RETURNING id""",
            (tid,),
        ).fetchone()
    yield {"tenant_id": tid, "chair": str(chair["id"]), "tent": str(tent["id"])}
    with pool.connection() as conn:
        conn.execute(
            "DELETE FROM webhook_events WHERE stripe_event_id LIKE 'evt_test_ct_%'"
        )
        conn.execute("DELETE FROM orders WHERE tenant_id = %s", (tid,))
        conn.execute(
            """DELETE FROM reservation_items ri USING reservations r
                WHERE ri.reservation_id = r.id AND r.tenant_id = %s""",
            (tid,),
        )
        conn.execute("DELETE FROM reservations WHERE tenant_id = %s", (tid,))
        conn.execute("DELETE FROM customers WHERE tenant_id = %s", (tid,))
        conn.execute("DELETE FROM actions WHERE tenant_id = %s", (tid,))
        conn.execute("DELETE FROM rental_items WHERE tenant_id = %s", (tid,))
        conn.execute("DELETE FROM tenants WHERE id = %s", (tid,))


CONFIG = PipelineConfig(
    executors={"checkout_session": checkout_session_executor},
    capabilities={"ops": ("checkout_session",)},
    requires_approval=frozenset(),
)


def do_checkout(biz, *, items, start="2026-09-01", end="2026-09-03", key=None, email=None):
    row, replayed = request_action(
        tenant_id=biz["tenant_id"],
        agent_name="ops",
        action_type="checkout_session",
        payload={
            "items": items,
            "startDate": start,
            "endDate": end,
            "customer": {"name": "Test Customer", "email": email or "cust@example.com"},
            "siteUrl": "https://example.test",
        },
        idempotency_key=key or f"checkout-{uuid.uuid4().hex}",
        config=CONFIG,
    )
    session_id, _, rest = (row["provider_reference"] or "").partition("|")
    reservation_id, _, rest = rest.partition("|")
    total, _, _url = rest.partition("|")
    return row, session_id, reservation_id, int(total), replayed


def signed(event: dict) -> tuple[bytes, str]:
    payload = json.dumps(event).encode()
    t = int(time.time())
    mac = hmac.new(SECRET.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return payload, f"t={t},v1={mac}"


def completed_event(session_id, reservation_id, amount, *, event_id=None, paid=True):
    return {
        "id": event_id or f"evt_test_ct_{uuid.uuid4().hex[:16]}",
        "object": "event",
        "type": "checkout.session.completed",
        "created": int(time.time()),
        "data": {
            "object": {
                "id": session_id,
                "object": "checkout.session",
                "payment_status": "paid" if paid else "unpaid",
                "amount_total": amount,
                "currency": "gbp",
                "metadata": {"reservation_id": reservation_id},
            }
        },
    }


def test_total_is_computed_server_side(biz):
    # 2 chairs x 150 x 3 days + 1 tent x 14000 x 3 days = 900 + 42000
    _, _, reservation_id, total, _ = do_checkout(
        biz,
        items=[{"rentalItemId": biz["chair"], "qty": 2}, {"rentalItemId": biz["tent"], "qty": 1}],
        end="2026-09-03",
    )
    assert total == 2 * 150 * 3 + 1 * 14000 * 3
    with pool.connection() as conn:
        resv = conn.execute(
            "SELECT status, total FROM reservations WHERE id = %s", (reservation_id,)
        ).fetchone()
    assert resv["status"] == "PENDING"
    assert resv["total"] == total


def test_double_booking_prevented(biz):
    # The single tent is already PENDING for 09-01..09-03 (test above).
    with pytest.raises(GateError) as exc:
        do_checkout(
            biz, items=[{"rentalItemId": biz["tent"], "qty": 1}],
            start="2026-09-03", end="2026-09-04",
        )
    assert exc.value.http_status == 409

    # Non-overlapping dates are fine.
    _, _, rid, _, _ = do_checkout(
        biz, items=[{"rentalItemId": biz["tent"], "qty": 1}], start="2026-09-10", end="2026-09-11"
    )
    assert rid


def test_checkout_replay_returns_same_reservation(biz):
    key = f"checkout-replay-{uuid.uuid4().hex[:8]}"
    _, s1, r1, _, rep1 = do_checkout(
        biz, items=[{"rentalItemId": biz["chair"], "qty": 1}],
        start="2026-10-01", end="2026-10-01", key=key,
    )
    _, s2, r2, _, rep2 = do_checkout(
        biz, items=[{"rentalItemId": biz["chair"], "qty": 1}],
        start="2026-10-01", end="2026-10-01", key=key,
    )
    assert (r1, s1) == (r2, s2)
    assert rep2 is True
    with pool.connection() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM reservations WHERE id = %s", (r1,)
        ).fetchone()["n"]
    assert n == 1


def test_paid_webhook_writes_exactly_one_order(biz):
    _, session_id, reservation_id, total, _ = do_checkout(
        biz, items=[{"rentalItemId": biz["chair"], "qty": 1}], start="2026-11-01", end="2026-11-02"
    )
    event = completed_event(session_id, reservation_id, total)
    payload, sig = signed(event)

    out = process_stripe_webhook(payload, sig)
    assert out["status"] == "CONFIRMED"

    # Same event redelivered: deduped by event id.
    out2 = process_stripe_webhook(payload, sig)
    assert out2.get("duplicate") is True

    # Same session, NEW event id (Stripe does this too): already confirmed.
    payload3, sig3 = signed(completed_event(session_id, reservation_id, total))
    out3 = process_stripe_webhook(payload3, sig3)
    assert out3["status"] == "already CONFIRMED"

    with pool.connection() as conn:
        orders = conn.execute(
            "SELECT count(*) AS n FROM orders WHERE reservation_id = %s", (reservation_id,)
        ).fetchone()["n"]
        resv = conn.execute(
            "SELECT status FROM reservations WHERE id = %s", (reservation_id,)
        ).fetchone()
    assert orders == 1  # one order, never two
    assert resv["status"] == "CONFIRMED"


def test_amount_mismatch_writes_no_order(biz):
    _, session_id, reservation_id, total, _ = do_checkout(
        biz, items=[{"rentalItemId": biz["chair"], "qty": 1}], start="2026-12-01", end="2026-12-01"
    )
    payload, sig = signed(completed_event(session_id, reservation_id, total + 1))
    out = process_stripe_webhook(payload, sig)
    assert out["status"] == "amount mismatch"
    with pool.connection() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM orders WHERE reservation_id = %s", (reservation_id,)
        ).fetchone()["n"]
        status = conn.execute(
            "SELECT status FROM reservations WHERE id = %s", (reservation_id,)
        ).fetchone()["status"]
    assert n == 0
    assert status == "PENDING"


def test_expired_session_releases_availability(biz):
    _, session_id, reservation_id, total, _ = do_checkout(
        biz, items=[{"rentalItemId": biz["tent"], "qty": 1}], start="2027-01-01", end="2027-01-02"
    )
    event = {
        "id": f"evt_test_ct_{uuid.uuid4().hex[:16]}",
        "object": "event",
        "type": "checkout.session.expired",
        "created": int(time.time()),
        "data": {
            "object": {
                "id": session_id,
                "object": "checkout.session",
                "metadata": {"reservation_id": reservation_id},
            }
        },
    }
    payload, sig = signed(event)
    out = process_stripe_webhook(payload, sig)
    assert out["status"] == "CANCELLED"

    # The tent is bookable again for those dates.
    _, _, rid2, _, _ = do_checkout(
        biz, items=[{"rentalItemId": biz["tent"], "qty": 1}], start="2027-01-01", end="2027-01-02"
    )
    assert rid2


def test_bad_signature_rejected(biz):
    payload = json.dumps({"id": "evt_x", "type": "checkout.session.completed"}).encode()
    with pytest.raises(GateError) as exc:
        process_stripe_webhook(payload, "t=1,v1=deadbeef")
    assert exc.value.http_status == 400
