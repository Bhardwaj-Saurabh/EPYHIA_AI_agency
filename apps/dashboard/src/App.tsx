import { useEffect, useState } from "react";
import { Badge, Card, CardContent, CardHeader, CardTitle, Table, Td, Th, statusTone } from "./components/ui";
import { usd, when } from "./lib/utils";

type Run = {
  id: string;
  status: string;
  created_at: string;
  approved_budget_microdollars: number;
  spent_microdollars: number;
  business_name: string;
  business_slug: string;
};

const api = (p: string) => fetch(`/admin/api/${p}`).then((r) => r.json());

function useLive<T>(path: string | null, ms = 8000): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const load = () => api(path).then((d) => alive && setData(d)).catch(() => {});
    load();
    const t = setInterval(load, ms);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [path, ms]);
  return data;
}

export default function App() {
  const cfg = useLive<{ writesEnabled: boolean }>("config", 60000);
  const runsRes = useLive<{ runs: Run[] }>("runs");
  const [sel, setSel] = useState<string | null>(null);
  const runs = runsRes?.runs ?? [];
  const runId = sel ?? runs[0]?.id ?? null;

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold tracking-tight">
            EPYHIA <span className="font-normal text-neutral-400">·</span> Action Gate console
          </h1>
          <p className="text-sm text-neutral-500">
            Every side effect below passed capability, approval, budget and idempotency checks.
          </p>
        </div>
        <Badge tone={cfg?.writesEnabled ? "gold" : "brand"}>
          {cfg?.writesEnabled ? "admin mode — approvals enabled" : "read-only (public deployment)"}
        </Badge>
      </header>

      <div className="grid gap-4 md:grid-cols-[280px_1fr]">
        <Card className="self-start">
          <CardHeader>
            <CardTitle>Runs</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {runs.map((r) => {
              const pct = Math.min(100, (r.spent_microdollars / r.approved_budget_microdollars) * 100);
              return (
                <button
                  key={r.id}
                  onClick={() => setSel(r.id)}
                  className={`rounded-lg border p-3 text-left transition-colors ${
                    r.id === runId ? "border-brand bg-brand-soft" : "border-neutral-200 hover:bg-neutral-50"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold">{r.business_name}</span>
                    <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                  </div>
                  <div className="mt-2 h-1.5 w-full rounded bg-neutral-200">
                    <div className="h-1.5 rounded bg-gold" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="mt-1 text-xs text-neutral-500">
                    {usd(r.spent_microdollars)} of {usd(r.approved_budget_microdollars)} · {when(r.created_at)}
                  </div>
                </button>
              );
            })}
            {!runs.length && <p className="text-sm text-neutral-400">No runs yet.</p>}
          </CardContent>
        </Card>

        {runId ? <RunDetail runId={runId} writes={!!cfg?.writesEnabled} /> : null}
      </div>
    </div>
  );
}

function RunDetail({ runId, writes }: { runId: string; writes: boolean }) {
  const state = useLive<any>(`runs/${runId}/state`);
  const calls = useLive<{ calls: any[] }>(`runs/${runId}/calls`);
  const actions = useLive<{ actions: any[] }>(`runs/${runId}/actions`);
  if (!state) return <Card className="p-6 text-sm text-neutral-400">Loading run…</Card>;

  const spent = Number(state.run.spent_microdollars);
  const budget = Number(state.run.approved_budget_microdollars);
  const byAgent: Record<string, { tier: string; model: string; calls: number; cost: number; tokens: number }> = {};
  for (const c of calls?.calls ?? []) {
    if (c.status !== "completed") continue;
    const k = c.agent_name;
    byAgent[k] ??= { tier: c.model_tier, model: c.model_id, calls: 0, cost: 0, tokens: 0 };
    byAgent[k].calls += 1;
    byAgent[k].cost += Number(c.cost_microdollars);
    byAgent[k].tokens += c.input_tokens + c.output_tokens;
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Model spend (of admin-approved budget)" value={`${usd(spent)} / ${usd(budget)}`} />
        <Stat label="Model calls, every one cost-logged" value={String(calls?.calls?.length ?? "…")} />
        <Stat label="Audited gate actions" value={String(actions?.actions?.length ?? "…")} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Tasks</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {state.tasks.map((t: any) => (
            <Badge key={t.id} tone={statusTone(t.status)}>
              {t.task_type}: {t.status}
            </Badge>
          ))}
          {state.deployment?.live_url && (
            <a
              className="ml-auto text-sm font-medium text-brand underline underline-offset-2"
              href={state.deployment.live_url}
              target="_blank"
              rel="noreferrer"
            >
              live site ↗
            </a>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Cost ledger — model tier per agent (cheap models draft, Sol reasons)</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <thead>
              <tr>
                <Th>Agent</Th>
                <Th>Tier</Th>
                <Th>Model</Th>
                <Th>Calls</Th>
                <Th>Tokens</Th>
                <Th>Cost</Th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(byAgent)
                .sort((a, b) => b[1].cost - a[1].cost)
                .map(([agent, s]) => (
                  <tr key={agent}>
                    <Td className="font-medium">{agent}</Td>
                    <Td>
                      <Badge tone={s.tier === "sol" ? "gold" : s.tier === "terra" ? "blue" : "green"}>
                        {s.tier}
                      </Badge>
                    </Td>
                    <Td className="text-neutral-500">{s.model}</Td>
                    <Td>{s.calls}</Td>
                    <Td>{s.tokens.toLocaleString()}</Td>
                    <Td className="font-medium">{usd(s.cost)}</Td>
                  </tr>
                ))}
            </tbody>
          </Table>
        </CardContent>
      </Card>

      <Approvals writes={writes} />

      <Card>
        <CardHeader>
          <CardTitle>Audit log — one row per gated action</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <thead>
              <tr>
                <Th>Action</Th>
                <Th>Agent</Th>
                <Th>Mode</Th>
                <Th>Status</Th>
                <Th>Approved by</Th>
                <Th>Payload hash</Th>
                <Th>Executed</Th>
              </tr>
            </thead>
            <tbody>
              {(actions?.actions ?? []).map((a) => (
                <tr key={a.id}>
                  <Td className="font-medium">{a.action_type}</Td>
                  <Td>{a.agent_name}</Td>
                  <Td>
                    <Badge tone={a.mode === "TEST" ? "green" : "red"}>{a.mode}</Badge>
                  </Td>
                  <Td>
                    <Badge tone={statusTone(a.status)}>{a.status}</Badge>
                  </Td>
                  <Td>{a.approved_by ?? "—"}</Td>
                  <Td className="font-mono text-xs text-neutral-500">{a.payload_hash.slice(0, 12)}…</Td>
                  <Td className="text-neutral-500">{when(a.executed_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

function Approvals({ writes }: { writes: boolean }) {
  const pending = useLive<{ pending: any[] }>("approvals", 5000);
  if (!pending?.pending?.length) return null;
  return (
    <Card className="border-amber-300">
      <CardHeader>
        <CardTitle>Pending approvals — nothing irreversible runs without a human</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {pending.pending.map((p) => (
          <div key={p.id} className="flex flex-wrap items-center gap-2 rounded-lg bg-amber-50 p-3">
            <Badge tone="gold">{p.action_type}</Badge>
            <span className="text-sm">requested by {p.agent_name}</span>
            <code className="text-xs text-neutral-500">{p.payload_hash.slice(0, 16)}…</code>
            <span className="ml-auto text-xs text-neutral-500">
              {writes
                ? "approve via the run's CLI flow (hash-bound: the approver must present the exact payload)"
                : "read-only deployment — approvals happen on the admin's machine"}
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="text-2xl font-bold tracking-tight">{value}</div>
        <div className="mt-1 text-xs text-neutral-500">{label}</div>
      </CardContent>
    </Card>
  );
}
