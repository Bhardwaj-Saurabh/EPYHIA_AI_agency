"""Canonical JSON hashing. Approvals bind to this hash (DESIGN.md section 4).

Matches the canonical form used since the first gate implementation: object
keys sorted recursively, compact separators, unicode NOT ascii-escaped.
"""

import hashlib
import json


def payload_hash(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_reject
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reject(obj: object) -> None:
    raise TypeError(f"payload contains non-JSON value of type {type(obj).__name__}")
