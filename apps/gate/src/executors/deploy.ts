// Deploy executor: pushes static files to Cloudflare Pages and independently
// verifies the live URL answers before reporting success (DESIGN.md sec. 5.7,
// failure catalogue #1 - never trust a self-report).
import { mkdtemp, writeFile, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { pool } from "../db.js";
import type { Executor } from "../pipeline.js";

const execFileP = promisify(execFile);

interface DeployPayload {
  projectName: string;
  files: Record<string, string>; // path -> content
}

function assertPayload(payload: unknown): DeployPayload {
  const p = payload as Partial<DeployPayload> | null;
  if (!p || typeof p.projectName !== "string" || !/^[a-z0-9][a-z0-9-]{0,56}$/.test(p.projectName)) {
    throw new Error("deploy payload needs projectName matching [a-z0-9][a-z0-9-]*");
  }
  if (!p.files || typeof p.files !== "object" || Object.keys(p.files).length === 0) {
    throw new Error("deploy payload needs a non-empty files map");
  }
  return p as DeployPayload;
}

async function ensureProject(projectName: string): Promise<void> {
  const account = process.env.CLOUDFLARE_ACCOUNT_ID;
  const token = process.env.CLOUDFLARE_API_TOKEN;
  if (!account || !token) throw new Error("Cloudflare credentials not configured on the gate");

  const res = await fetch(
    `https://api.cloudflare.com/client/v4/accounts/${account}/pages/projects`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ name: projectName, production_branch: "main" }),
    },
  );
  if (res.ok) return;
  const body = (await res.json().catch(() => null)) as {
    errors?: Array<{ code?: number; message?: string }>;
  } | null;
  const alreadyExists = body?.errors?.some(
    (e) => e.code === 8000007 || /already exists/i.test(e.message ?? ""),
  );
  if (!alreadyExists) {
    throw new Error(`Cloudflare project create failed: ${JSON.stringify(body?.errors ?? res.status)}`);
  }
}

async function verifyLive(url: string, timeoutMs = 90_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastStatus = 0;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { redirect: "follow" });
      lastStatus = res.status;
      if (res.status === 200) return;
    } catch {
      // propagation - keep polling
    }
    await new Promise((r) => setTimeout(r, 3000));
  }
  throw new Error(`deployed URL ${url} did not answer 200 within ${timeoutMs / 1000}s (last: ${lastStatus})`);
}

export const deployExecutor: Executor = async (payload, ctx) => {
  const { projectName, files } = assertPayload(payload);

  await ensureProject(projectName);

  const dir = await mkdtemp(join(tmpdir(), "epyhia-deploy-"));
  try {
    for (const [path, content] of Object.entries(files)) {
      const safe = path.replace(/^\/+/, "");
      if (safe.includes("..")) throw new Error(`unsafe file path: ${path}`);
      const target = join(dir, safe);
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, content, "utf8");
    }

    await execFileP(
      "npx",
      [
        "wrangler",
        "pages",
        "deploy",
        dir,
        "--project-name",
        projectName,
        "--branch",
        "main",
        "--commit-dirty=true",
      ],
      { env: process.env, timeout: 180_000 },
    );
  } finally {
    await rm(dir, { recursive: true, force: true });
  }

  // Independent verification: the production URL must actually answer.
  const liveUrl = `https://${projectName}.pages.dev`;
  await verifyLive(liveUrl);

  await pool.query(
    `INSERT INTO deployments (tenant_id, cloudflare_project_name, live_url, last_action_id, verified_at, updated_at)
     VALUES ($1, $2, $3, $4, now(), now())
     ON CONFLICT (tenant_id) DO UPDATE
       SET cloudflare_project_name = EXCLUDED.cloudflare_project_name,
           live_url = EXCLUDED.live_url,
           last_action_id = EXCLUDED.last_action_id,
           verified_at = now(),
           updated_at = now()`,
    [ctx.tenantId, projectName, liveUrl, ctx.actionId],
  );

  return { providerReference: liveUrl };
};
