// Loads the repo-root .env for local dev. On Fly, secrets come from the
// environment directly and the file simply doesn't exist.
import { fileURLToPath } from "node:url";
import dotenv from "dotenv";

dotenv.config({ path: fileURLToPath(new URL("../../../.env", import.meta.url)), quiet: true });
