// Loads the repo-root .env for local dev. Note: Tier 2 reads ONLY GATE_URL and
// WORKERS_PORT from it - no credentials of any kind.
import { fileURLToPath } from "node:url";
import dotenv from "dotenv";

dotenv.config({ path: fileURLToPath(new URL("../../../.env", import.meta.url)), quiet: true });
