"""Firestore indexes and collections configuration for OpsPilot.

Collections used:
  - opspilot_jobs            JobRecord documents         (key=job_id)
  - opspilot_approvals       ApprovalRequest documents  (key=approval_id)
  - opspilot_memory          MemoryEntry documents      (auto id, indexed by project_id+type)
  - opspilot_projects        Project metadata           (key=project_id)
  - opspilot_events          append-only job events     (auto id)

This module exposes `ensure_collections()` and `build_firestore_store()`
to swap the backend InMemoryStore for a real Firestore client.
"""
from __future__ import annotations

import os
from typing import Any


def _fs_client() -> Any | None:
    try:
        from google.cloud import firestore  # type: ignore

        return firestore
    except Exception:
        return None


def ensure_collections(project_id: str | None = None) -> None:
    fs = _fs_client()
    if fs is None:
        return
    pid = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not pid:
        return
    _ = fs.Client(project=pid)


class FirestoreStore:
    """Firestore-backed document store compatible with InMemoryStore API.

    Supports the same `set`, `get`, `delete`, `query`, `list_all` methods
    so MemoryService / JobStore / ApprovalStore can swap stores without
    code changes.
    """

    def __init__(self, collection: str, project_id: str | None = None) -> None:
        fs = _fs_client()
        if fs is None:
            raise RuntimeError("google-cloud-firestore is not installed")
        pid = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self._client = fs.Client(project=pid)
        self._collection = self._client.collection(collection)

    def set(self, doc_id: str, data: dict[str, Any]) -> None:
        self._collection.document(doc_id).set(data)

    def get(self, doc_id: str) -> dict[str, Any] | None:
        snap = self._collection.document(doc_id).get()
        if not snap.exists:
            return None
        return snap.to_dict() or {}

    def delete(self, doc_id: str) -> None:
        self._collection.document(doc_id).delete()

    def query(
        self,
        filters: list[tuple[str, str, Any]] | None = None,
        order_by: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        q = self._collection
        if filters:
            for field, op, value in filters:
                q = q.where(field, op, value)
        if order_by:
            q = q.order_by(order_by)
        if limit:
            q = q.limit(limit)
        return [snap.to_dict() or {} for snap in q.stream()]

    def list_all(self) -> list[dict[str, Any]]:
        return [snap.to_dict() or {} for snap in self._collection.stream()]
