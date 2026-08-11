import type { Executor } from "../pipeline.js";
import { deployExecutor } from "./deploy.js";

// Executor registry. Action types without an executor are capability-checked
// and audited but return 501 until their build-order step lands.
export const EXECUTORS: Record<string, Executor> = {
  deploy: deployExecutor,
};
