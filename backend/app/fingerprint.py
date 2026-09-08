"""Deterministic source-record fingerprinting.

Used by the reconciliation service (app/services/reconciliation.py) to
detect whether a source record actually changed between two fetches,
since most sources (confirmed for Denver — see
docs/sources/denver-ingestion-design.md §4) expose no per-row
"last updated" field.

Design notes:

- Fingerprints are computed over a *canonical field set* the caller
  chooses (``canonicalize_fields``), not over an entire raw payload.
  This is what keeps volatile ingestion metadata (e.g. ``observed_at``,
  a database row id) out of the hash: simply don't include those keys
  in the field list you pass in.
- Canonicalization always emits every requested field, using ``None``
  for anything missing from the source payload — so "key absent" and
  "key present with a null value" produce identical fingerprints. This
  is a deliberate choice: a source that starts omitting a field
  entirely vs. sending it as null should not, by itself, look like a
  changed record.
- Comparison is by *exact* JSON-serialized value. Two values that are
  "equal" under a looser notion (e.g. the integer ``1`` and the string
  ``"1"``) are NOT considered identical here — callers are responsible
  for consistent typing when they build the canonical payload.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def canonicalize_fields(raw: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    """Project ``raw`` onto exactly ``fields``, defaulting missing keys to None."""
    return {field: raw.get(field) for field in fields}


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Serialize ``payload`` with deterministic key ordering (recursive)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fingerprint_payload(payload: Mapping[str, Any]) -> str:
    """SHA-256 hex digest of ``payload``'s canonical JSON form."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def fingerprint_record(raw: Mapping[str, Any], fields: Sequence[str]) -> str:
    """Convenience: canonicalize ``raw`` onto ``fields``, then fingerprint it."""
    return fingerprint_payload(canonicalize_fields(raw, fields))
