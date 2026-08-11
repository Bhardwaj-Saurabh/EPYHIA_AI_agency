import { createHash } from "node:crypto";

// Canonical JSON: object keys sorted recursively, so the same logical payload
// always produces the same hash. Approvals bind to this hash (DESIGN.md sec. 4).
function canonicalize(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(",")}]`;
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, v]) => v !== undefined)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${JSON.stringify(k)}:${canonicalize(v)}`);
  return `{${entries.join(",")}}`;
}

export function payloadHash(payload: unknown): string {
  return createHash("sha256").update(canonicalize(payload)).digest("hex");
}
