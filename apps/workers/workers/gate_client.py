"""The workers' only reach into the world: HTTP to the Action Gate.
No provider SDKs, no DB connection, no credentials - capability handles only."""

import os
from typing import Any

import httpx

from . import env as _env  # noqa: F401

GATE = os.environ.get("GATE_URL", "http://localhost:8082").rstrip("/")


class GateClientError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _call(path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    res = httpx.request(method, f"{GATE}{path}", json=body, timeout=300)
    try:
        data = res.json()
    except ValueError:
        data = {}
    if res.status_code >= 400:
        raise GateClientError(
            res.status_code, data.get("error", f"gate returned {res.status_code}")
        )
    return data


def request_action(
    *,
    tenant_id: str,
    agent_name: str,
    action_type: str,
    payload: Any,
    idempotency_key: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    return _call(
        "/actions",
        method="POST",
        body={
            "tenantId": tenant_id,
            "runId": run_id,
            "agentName": agent_name,
            "actionType": action_type,
            "payload": payload,
            "idempotencyKey": idempotency_key,
        },
    )


def model_call(
    *,
    run_id: str,
    agent_name: str,
    messages: list[dict[str, str]],
    tier: str | None = None,
    json_mode: bool = False,
    task_id: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    return _call(
        "/model_call",
        method="POST",
        body={
            "runId": run_id,
            "taskId": task_id,
            "agentName": agent_name,
            "tier": tier,
            "messages": messages,
            "json": json_mode,
            "maxTokens": max_tokens,
        },
    )


def get_run(run_id: str) -> dict[str, Any]:
    return _call(f"/runs/{run_id}")
