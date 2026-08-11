"""Pipeline invariant tests, run against the real Neon dev database.
Each test asserts persisted state, not mock call counts."""

import pytest
from gate.db import pool
from gate.hashing import payload_hash
from gate.pipeline import (
    ExecutorResult,
    GateError,
    PipelineConfig,
    approve_action,
    request_action,
)

CAPS = {"web_builder": ("deploy",), "ops": ("noop",)}


@pytest.fixture(scope="module")
def tenant_id():
    with pool.connection() as conn:
        row = conn.execute(
            """INSERT INTO tenants (name, email, business_name, business_slug)
               VALUES ('Test Tenant', 'test@example.com', 'Test Biz',
                       'test-' || substr(md5(random()::text), 1, 12))
               RETURNING id"""
        ).fetchone()
    tid = str(row["id"])
    yield tid
    with pool.connection() as conn:
        conn.execute("DELETE FROM deployments WHERE tenant_id = %s", (tid,))
        conn.execute("DELETE FROM actions WHERE tenant_id = %s", (tid,))
        conn.execute("DELETE FROM tenants WHERE id = %s", (tid,))


class Counter:
    def __init__(self) -> None:
        self.count = 0

    def executor(self, _payload, _ctx) -> ExecutorResult:
        self.count += 1
        return ExecutorResult(provider_reference=f"exec-{self.count}")


def make_config() -> tuple[PipelineConfig, Counter]:
    counter = Counter()
    config = PipelineConfig(
        executors={"noop": counter.executor, "deploy": counter.executor},
        capabilities=CAPS,
        requires_approval=frozenset({"deploy"}),
    )
    return config, counter


def test_capability_rejected(tenant_id):
    config, counter = make_config()
    with pytest.raises(GateError) as exc:
        request_action(
            tenant_id=tenant_id,
            agent_name="web_builder",
            action_type="noop",  # ops-only in CAPS
            payload={},
            idempotency_key="cap-1",
            config=config,
        )
    assert exc.value.http_status == 403
    assert counter.count == 0


def test_idempotent_replay(tenant_id):
    config, counter = make_config()
    kwargs = dict(
        tenant_id=tenant_id,
        agent_name="ops",
        action_type="noop",
        payload={"n": 1},
        idempotency_key="idem-1",
        config=config,
    )
    row1, replayed1 = request_action(**kwargs)
    assert row1["status"] == "executed"
    assert replayed1 is False

    row2, replayed2 = request_action(**kwargs)
    assert replayed2 is True
    assert row2["id"] == row1["id"]
    assert row2["provider_reference"] == row1["provider_reference"]
    assert counter.count == 1  # the executor never ran twice

    with pool.connection() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM actions WHERE tenant_id = %s AND idempotency_key = 'idem-1'",
            (tenant_id,),
        ).fetchone()["n"]
    assert n == 1  # exactly one audit row


def test_same_key_different_payload_rejected(tenant_id):
    config, _ = make_config()
    base = dict(
        tenant_id=tenant_id,
        agent_name="ops",
        action_type="noop",
        idempotency_key="idem-2",
        config=config,
    )
    request_action(payload={"v": "a"}, **base)
    with pytest.raises(GateError) as exc:
        request_action(payload={"v": "b"}, **base)
    assert exc.value.http_status == 409


def test_approval_binding(tenant_id):
    config, counter = make_config()
    payload = {"projectName": "test-site", "files": {"/index.html": "<h1>hi</h1>"}}
    row, _ = request_action(
        tenant_id=tenant_id,
        agent_name="web_builder",
        action_type="deploy",
        payload=payload,
        idempotency_key="appr-1",
        config=config,
    )
    assert row["status"] == "pending_approval"
    assert counter.count == 0  # nothing executed before a human approves

    # Approval carrying the WRONG hash (superseded payload) is rejected.
    with pytest.raises(GateError) as exc:
        approve_action(row["id"], "admin@test", payload_hash({"tampered": True}), payload, config)
    assert exc.value.http_status == 409

    # Approval with the exact reviewed hash executes.
    approved = approve_action(row["id"], "admin@test", row["payload_hash"], payload, config)
    assert approved["status"] == "executed"
    assert approved["approved_by"] == "admin@test"
    assert counter.count == 1


def test_approval_payload_mismatch_rejected(tenant_id):
    config, counter = make_config()
    payload = {"projectName": "test-2", "files": {"/index.html": "x"}}
    row, _ = request_action(
        tenant_id=tenant_id,
        agent_name="web_builder",
        action_type="deploy",
        payload=payload,
        idempotency_key="appr-2",
        config=config,
    )
    with pytest.raises(GateError) as exc:
        approve_action(row["id"], "admin@test", row["payload_hash"], {"swapped": "payload"}, config)
    assert exc.value.http_status == 409
    assert counter.count == 0


def test_budget_exhaustion(tenant_id):
    config, _ = make_config()
    with pool.connection() as conn:
        run_id = str(
            conn.execute(
                """INSERT INTO runs (tenant_id, original_brief, brief_hash,
                                     approved_budget_microdollars, budget_approved_by)
                   VALUES (%s, 'brief', 'hash', 1000, 'admin@test') RETURNING id""",
                (tenant_id,),
            ).fetchone()["id"]
        )
        conn.execute(
            """INSERT INTO agent_calls (run_id, agent_name, model_id, model_tier,
                                        cost_microdollars, status)
               VALUES (%s, 'ops', 'test-model', 'luna', 1000, 'completed')""",
            (run_id,),
        )

    with pytest.raises(GateError) as exc:
        request_action(
            tenant_id=tenant_id,
            run_id=run_id,
            agent_name="ops",
            action_type="noop",
            payload={},
            idempotency_key="budget-1",
            config=config,
        )
    assert exc.value.http_status == 402

    with pool.connection() as conn:
        conn.execute("DELETE FROM agent_calls WHERE run_id = %s", (run_id,))
        conn.execute("DELETE FROM runs WHERE id = %s", (run_id,))


def test_gate_error_carries_status():
    assert GateError(403, "x").http_status == 403
