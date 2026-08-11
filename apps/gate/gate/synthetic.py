"""Synthetic go-live purchase (DESIGN.md sec. 5.7, failure catalogue #8).

A deployment is not "verified live" until an end-to-end purchase has actually
persisted an order row through the real checkout, webhook, and DB path. The
Polsia teardown found "launched" businesses with no working payment at all -
this check makes that failure impossible to ship silently.

Mechanism (the build-time decision DESIGN.md allows): a REAL Stripe test-mode
Checkout Session is created for a synthetic-flagged reservation, then the
completed event is signed with the configured webhook secret and driven
through process_stripe_webhook - the identical verification, dedupe,
amount-comparison, and one-transaction persistence path production events
take. What it does not exercise is Stripe's hosted payment page itself.
"""

import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from .db import pool
from .executors.checkout import process_stripe_webhook, run_checkout

log = logging.getLogger("gate.synthetic")


def run_synthetic_purchase(tenant_id: str, seed: str, site_url: str) -> dict[str, Any]:
    """Returns the synthetic order row; raises if any step fails to persist."""
    import os

    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET not configured - cannot run synthetic purchase")

    with pool.connection() as conn:
        item = conn.execute(
            """SELECT id, day_rate FROM rental_items
                WHERE tenant_id = %s ORDER BY day_rate ASC LIMIT 1""",
            (tenant_id,),
        ).fetchone()
    if item is None:
        raise RuntimeError("tenant has no catalog - synthetic purchase impossible")

    today = datetime.now(UTC).date().isoformat()
    result = run_checkout(
        {
            "items": [{"rentalItemId": str(item["id"]), "qty": 1}],
            "startDate": today,
            "endDate": today,
            "customer": {"name": "EPYHIA go-live check", "email": "synthetic@epyhia.internal"},
            "siteUrl": site_url,
        },
        tenant_id=tenant_id,
        seed_id=f"synthetic-{seed}",
        synthetic=True,
    )
    session_id, _, rest = result.provider_reference.partition("|")
    reservation_id, _, rest = rest.partition("|")
    total = int(rest.partition("|")[0])

    event = {
        "id": f"evt_synthetic_{uuid.uuid4().hex[:16]}",
        "object": "event",
        "type": "checkout.session.completed",
        "created": int(time.time()),
        "data": {
            "object": {
                "id": session_id,
                "object": "checkout.session",
                "payment_status": "paid",
                "amount_total": total,
                "currency": "gbp",
                "metadata": {"reservation_id": reservation_id},
            }
        },
    }
    payload = json.dumps(event).encode()
    t = int(time.time())
    mac = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    process_stripe_webhook(payload, f"t={t},v1={mac}")

    # Trust the row, not the return value.
    with pool.connection() as conn:
        order = conn.execute(
            """SELECT id, amount, currency, status, is_synthetic
                 FROM orders WHERE reservation_id = %s""",
            (reservation_id,),
        ).fetchone()
    if order is None or not order["is_synthetic"] or order["status"] != "PAID":
        raise RuntimeError(f"synthetic purchase did not persist a PAID synthetic order: {order}")

    log.info("synthetic purchase verified: order %s (%sp)", order["id"], order["amount"])
    return dict(order)
