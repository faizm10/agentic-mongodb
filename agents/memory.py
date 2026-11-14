"""Incident memory: embeddings + Atlas Vector Search.

- `embed(text)`            -> vector (Voyage AI if VOYAGE_API_KEY is set, else a
                              deterministic local hashing embedding for offline dev)
- `remember_fix(incident)` -> stores a verified fix as a memory
- `find_similar(run)`      -> top-k similar past incidents for Faiz's investigator
- `forget(memory_id)`      -> marks a wrong memory inactive so it is never retrieved again

Memories are only retrieved if they were embedded with the *same* model as the
query (mixing vector spaces gives garbage), and only for the same app.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import timedelta
from typing import Any

from bson import ObjectId
from pymongo.database import Database
from pymongo.errors import OperationFailure

from agents import mongodb as m

VOYAGE_MODEL = os.getenv("VOYAGE_MODEL", "voyage-3.5")
VOYAGE_URL = os.getenv("VOYAGE_URL", "https://api.voyageai.com/v1/embeddings")
LOCAL_MODEL = "local-hash-v1"
MAX_LOG_CHARS = 6000


def embedding_model() -> str:
    return VOYAGE_MODEL if os.getenv("VOYAGE_API_KEY") else LOCAL_MODEL


# --------------------------------------------------------------------------- #
# Embeddings                                                                  #
# --------------------------------------------------------------------------- #
def embed(text: str, input_type: str = "document") -> list[float]:
    if os.getenv("VOYAGE_API_KEY"):
        return _voyage_embed(text, input_type)
    return _local_embed(text)


def _voyage_embed(text: str, input_type: str) -> list[float]:
    import requests

    resp = requests.post(
        VOYAGE_URL,
        headers={"Authorization": f"Bearer {os.environ['VOYAGE_API_KEY']}"},
        json={"input": [text[:MAX_LOG_CHARS * 2]], "model": VOYAGE_MODEL,
              "input_type": input_type, "output_dimension": m.EMBEDDING_DIMS},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def _local_embed(text: str) -> list[float]:
    """Feature-hashing bag of words + bigrams. Good enough to match the same
    test names / error types; lets the whole pipeline run without an API key."""
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]+|\d+", text.lower())
    feats = tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]
    vec = [0.0] * m.EMBEDDING_DIMS
    for f in feats:
        h = int(hashlib.md5(f.encode()).hexdigest(), 16)
        vec[h % m.EMBEDDING_DIMS] += 1.0 if (h >> 64) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def failure_text(run: dict[str, Any]) -> str:
    """Canonical text we embed for a failed run (query side and memory side match)."""
    logs = run.get("failed_logs") or run.get("logs") or _live_text(run.get("live"))
    # Keep the tail: pytest puts assertion errors and the summary at the end.
    logs = logs[-MAX_LOG_CHARS:]
    tests = ", ".join(run.get("failed_tests") or [])
    return f"app: {run.get('app')}\nfailed tests: {tests}\nlogs:\n{logs}"


def _live_text(live: dict[str, Any] | None) -> str:
    """Live incidents have no CI log: embed the failing probe checks instead."""
    if not live:
        return ""
    checks = "\n".join(f"{f.get('check') or f.get('name')} {f.get('source') or ''} {f.get('reason') or ''}"
                       for f in live.get("probe_failures") or [])
    return f"{live.get('failure_type') or ''}: {live.get('summary') or ''}\n{checks}"


# --------------------------------------------------------------------------- #
# Write                                                                       #
# --------------------------------------------------------------------------- #
def remember_fix(incident: dict[str, Any], db: Database | None = None,
                 ttl_days: int | None = None) -> str:
    """Store a verified fix so future incidents can learn from it."""
    patch = incident.get("patch") or {}
    doc = {
        "app": incident["app"],
        "source_incident_id": incident["_id"],
        "summary": patch.get("summary") or patch.get("explanation") or "",
        "root_cause": patch.get("root_cause", ""),
        "failed_tests": incident.get("failed_tests", []),
        "changed_files": patch.get("changed_files", []),
        "diff": patch.get("diff", "")[:8000],
        "pr_url": (incident.get("pr") or {}).get("url"),
        "failure_text": failure_text(incident),
        "embedding": embed(failure_text(incident)),
        "embedding_model": embedding_model(),
        "active": True,
        "created_at": m.utcnow(),
    }
    if ttl_days:
        doc["expires_at"] = m.utcnow() + timedelta(days=ttl_days)
    # Idempotent: re-running after a crash updates rather than duplicates.
    res = m.memories(db).find_one_and_update(
        {"source_incident_id": incident["_id"]},
        {"$set": doc},
        upsert=True,
        return_document=True,
    )
    return str(res["_id"])


def seed_memory(app: str, failure_logs: str, summary: str, root_cause: str = "",
                changed_files: list[str] | None = None, diff: str = "",
                failed_tests: list[str] | None = None, db: Database | None = None) -> str:
    """Insert a hand-written past incident (Taha's seed data)."""
    fake = {
        "_id": f"seed:{hashlib.sha1((app + summary).encode()).hexdigest()[:12]}",
        "app": app, "failed_logs": failure_logs, "failed_tests": failed_tests or [],
        "patch": {"summary": summary, "root_cause": root_cause,
                  "changed_files": changed_files or [], "diff": diff},
    }
    return remember_fix(fake, db=db)


def forget(memory_id: str, reason: str, db: Database | None = None) -> bool:
    """Mark a memory as wrong. It stays for audit but is never retrieved again."""
    res = m.memories(db).update_one(
        {"_id": ObjectId(memory_id)},
        {"$set": {"active": False, "forgotten_reason": reason, "forgotten_at": m.utcnow()}},
    )
    return res.modified_count == 1


# --------------------------------------------------------------------------- #
# Read                                                                        #
# --------------------------------------------------------------------------- #
_PROJECTION = {"_id": 1, "app": 1, "summary": 1, "root_cause": 1, "changed_files": 1,
               "diff": 1, "failed_tests": 1, "pr_url": 1, "source_incident_id": 1}


def find_similar(run: dict[str, Any], k: int = 3, min_score: float = 0.0,
                 db: Database | None = None) -> list[dict[str, Any]]:
    """Return up to k similar past fixes for the same app, best first, with `score`."""
    query_vec = embed(failure_text(run), input_type="query")
    flt = {"app": run["app"], "embedding_model": embedding_model(), "active": True}
    try:
        results = _vector_search(query_vec, flt, k, db)
        # Atlas returns [] (no error) while the index is missing/building.
        if not results and m.memories(db).count_documents(flt, limit=1):
            print("[memory] vector index returned nothing; using local cosine")
            results = _brute_force(query_vec, flt, k, db)
    except (OperationFailure, NotImplementedError, AttributeError, TypeError) as e:
        # Local mongod / mongomock / index still building -> brute-force cosine.
        print(f"[memory] vector search unavailable ({type(e).__name__}); using local cosine")
        results = _brute_force(query_vec, flt, k, db)
    out = []
    for r in results:
        if r.get("score", 0) < min_score:
            continue
        r["memory_id"] = str(r.pop("_id"))
        out.append(r)
    return out


def _vector_search(vec, flt, k, db):
    pipeline = [
        {"$vectorSearch": {
            "index": m.VECTOR_INDEX_NAME, "path": "embedding", "queryVector": vec,
            "numCandidates": max(50, k * 20), "limit": k,
            "filter": {key: {"$eq": val} for key, val in flt.items()},
        }},
        {"$project": {**_PROJECTION, "score": {"$meta": "vectorSearchScore"}}},
    ]
    return list(m.memories(db).aggregate(pipeline))


def _brute_force(vec, flt, k, db):
    scored = []
    for doc in m.memories(db).find(flt, {**_PROJECTION, "embedding": 1}):
        emb = doc.pop("embedding")
        dot = sum(a * b for a, b in zip(vec, emb))
        # map cosine [-1,1] -> [0,1] like Atlas does for cosine similarity
        doc["score"] = (1 + dot) / 2
        scored.append(doc)
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:k]
