"""Loads the repo-root .env for local dev. Note: Tier 2 reads ONLY GATE_URL and
WORKERS_PORT from it - no credentials of any kind."""

from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPO_ROOT / ".env")
