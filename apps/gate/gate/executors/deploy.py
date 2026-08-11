"""Deploy executor: pushes static files to Cloudflare Pages and independently
verifies the live URL answers before reporting success (DESIGN.md sec. 5.7,
failure catalogue #1 - never trust a self-report)."""

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from ..db import pool
from ..env import REPO_ROOT
from ..pipeline import ExecutorContext, ExecutorResult

_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,56}$")


def _assert_payload(payload: Any) -> tuple[str, dict[str, str]]:
    if not isinstance(payload, dict):
        raise ValueError("deploy payload must be an object")
    project = payload.get("projectName")
    files = payload.get("files")
    if not isinstance(project, str) or not _PROJECT_RE.match(project):
        raise ValueError("deploy payload needs projectName matching [a-z0-9][a-z0-9-]*")
    if not isinstance(files, dict) or not files:
        raise ValueError("deploy payload needs a non-empty files map")
    return project, files


def _ensure_project(project_name: str) -> None:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not account or not token:
        raise RuntimeError("Cloudflare credentials not configured on the gate")

    res = httpx.post(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": project_name, "production_branch": "main"},
        timeout=30,
    )
    if res.status_code < 300:
        return
    errors = (res.json() or {}).get("errors", [])
    already = any(
        e.get("code") == 8000007 or "already exists" in (e.get("message") or "").lower()
        for e in errors
    )
    if not already:
        raise RuntimeError(f"Cloudflare project create failed: {errors or res.status_code}")


def _verify_live(url: str, timeout_s: int = 90) -> None:
    deadline = time.monotonic() + timeout_s
    last_status = 0
    while time.monotonic() < deadline:
        try:
            res = httpx.get(url, follow_redirects=True, timeout=15)
            last_status = res.status_code
            if res.status_code == 200:
                return
        except httpx.HTTPError:
            pass  # propagation - keep polling
        time.sleep(3)
    raise RuntimeError(
        f"deployed URL {url} did not answer 200 within {timeout_s}s (last: {last_status})"
    )


def deploy_executor(payload: Any, ctx: ExecutorContext) -> ExecutorResult:
    project_name, files = _assert_payload(payload)

    _ensure_project(project_name)

    with tempfile.TemporaryDirectory(prefix="epyhia-deploy-") as tmp:
        base = Path(tmp)
        for path, content in files.items():
            safe = path.lstrip("/")
            if ".." in safe:
                raise ValueError(f"unsafe file path: {path}")
            target = base / safe
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        subprocess.run(
            [
                "npx",
                "wrangler",
                "pages",
                "deploy",
                str(base),
                "--project-name",
                project_name,
                "--branch",
                "main",
                "--commit-dirty=true",
            ],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            timeout=180,
            check=True,
            capture_output=True,
        )

    # Independent verification: the production URL must actually answer.
    live_url = f"https://{project_name}.pages.dev"
    _verify_live(live_url)

    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO deployments
                 (tenant_id, cloudflare_project_name, live_url, last_action_id,
                  verified_at, updated_at)
               VALUES (%s, %s, %s, %s, now(), now())
               ON CONFLICT (tenant_id) DO UPDATE
                 SET cloudflare_project_name = EXCLUDED.cloudflare_project_name,
                     live_url = EXCLUDED.live_url,
                     last_action_id = EXCLUDED.last_action_id,
                     verified_at = now(),
                     updated_at = now()""",
            (ctx.tenant_id, project_name, live_url, ctx.action_id),
        )
        # The WEBSITE task is complete only once reality is verified above.
        if ctx.run_id:
            conn.execute(
                """UPDATE tasks SET status = 'DONE', updated_at = now()
                    WHERE run_id = %s AND task_type = 'WEBSITE'""",
                (ctx.run_id,),
            )

    return ExecutorResult(provider_reference=live_url)
