"""Loads the repo-root .env for local dev. On Fly, secrets come from the
environment directly and the file simply doesn't exist."""

from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPO_ROOT / ".env")
