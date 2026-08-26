from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.app.config.settings import Settings


MEMORY_TYPE_CONVENTION = "project_convention"
MEMORY_TYPE_SUCCESSFUL_FIX = "successful_fix"
MEMORY_TYPE_FAILED_APPROACH = "failed_approach"
MEMORY_TYPE_PREVIOUS_TASK = "previous_task"
MEMORY_TYPE_APPROVAL_DECISION = "approval_decision"
MEMORY_TYPE_REPO_STRUCTURE = "repository_structure"
MEMORY_TYPE_TESTING_CONVENTION = "testing_convention"
MEMORY_TYPE_DEPLOYMENT_CONVENTION = "deployment_convention"

VALID_MEMORY_TYPES = {
    MEMORY_TYPE_CONVENTION,
    MEMORY_TYPE_SUCCESSFUL_FIX,
    MEMORY_TYPE_FAILED_APPROACH,
    MEMORY_TYPE_PREVIOUS_TASK,
    MEMORY_TYPE_APPROVAL_DECISION,
    MEMORY_TYPE_REPO_STRUCTURE,
    MEMORY_TYPE_TESTING_CONVENTION,
    MEMORY_TYPE_DEPLOYMENT_CONVENTION,
}


@dataclass
class MemoryEntry:
    memory_id: str
    project_id: str
    type: str
    content: str
    source: str
    confidence: float
    metadata: dict[str, Any]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InMemoryStore:
    """In-memory Firestore-like document store for local/dev and demo mode.

    Swappable with a thin Firestore adapter without callers changing.
    """

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> dict[str, dict[str, Any]]:
        if name not in self._collections:
            self._collections[name] = {}
        return self._collections[name]

    def set(self, collection_name: str, doc_id: str, data: dict[str, Any]) -> None:
        self.collection(collection_name)[doc_id] = dict(data)

    def get(self, collection_name: str, doc_id: str) -> dict[str, Any] | None:
        return self.collection(collection_name).get(doc_id)

    def query(
        self,
        collection_name: str,
        filters: list[tuple[str, str, Any]] | None = None,
        order_by: tuple[str, str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        coll = self.collection(collection_name)
        items = list(coll.values())

        if filters:
            for field, op, value in filters:
                items = [i for i in items if self._match(i.get(field), op, value)]

        if order_by:
            field, direction = order_by
            reverse = direction.lower() == "desc"
            items.sort(key=lambda i: i.get(field), reverse=reverse)

        if limit is not None:
            items = items[:limit]

        return items

    def delete(self, collection_name: str, doc_id: str) -> bool:
        coll = self.collection(collection_name)
        if doc_id in coll:
            del coll[doc_id]
            return True
        return False

    def list_all(self, collection_name: str) -> list[dict[str, Any]]:
        return list(self.collection(collection_name).values())

    @staticmethod
    def _match(field_value: Any, op: str, compare_value: Any) -> bool:
        if op == "==":
            return field_value == compare_value
        if op == "!=":
            return field_value != compare_value
        if op == ">=":
            return field_value is not None and field_value >= compare_value
        if op == "<=":
            return field_value is not None and field_value <= compare_value
        if op == ">":
            return field_value is not None and field_value > compare_value
        if op == "<":
            return field_value is not None and field_value < compare_value
        if op == "in":
            return field_value in compare_value
        if op == "contains":
            return isinstance(field_value, str) and isinstance(compare_value, str) and compare_value.lower() in field_value.lower()
        return False


_STORE_SINGLETON: InMemoryStore | None = None


def get_store() -> InMemoryStore:
    global _STORE_SINGLETON
    if _STORE_SINGLETON is None:
        _STORE_SINGLETON = InMemoryStore()
    return _STORE_SINGLETON


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id(prefix: str) -> str:
    import secrets
    return f"{prefix}_{secrets.token_hex(8)}"


class MemoryService:
    """Project-specific persistent memory for engineering knowledge."""

    COLLECTION = "opspilot_memory"

    def __init__(self, settings: Settings, store: InMemoryStore | None = None) -> None:
        self._settings = settings
        self._store = store or get_store()

    @staticmethod
    def project_id_for(github_owner: str, github_repo: str) -> str:
        return f"github:{github_owner.lower()}/{github_repo.lower()}"

    def write(
        self,
        project_id: str,
        memory_type: str,
        content: str,
        source: str,
        confidence: float = 0.8,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        if memory_type not in VALID_MEMORY_TYPES:
            raise ValueError(f"Invalid memory type: {memory_type}")
        confidence = max(0.0, min(1.0, float(confidence)))
        now = _now_iso()
        entry = MemoryEntry(
            memory_id=_make_id("mem"),
            project_id=project_id,
            type=memory_type,
            content=content,
            source=source,
            confidence=confidence,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        self._store.set(self.COLLECTION, entry.memory_id, entry.to_dict())
        return entry

    def retrieve(
        self,
        project_id: str,
        memory_types: list[str] | None = None,
        query: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 20,
    ) -> list[MemoryEntry]:
        filters: list[tuple[str, str, Any]] = [("project_id", "==", project_id)]
        if memory_types:
            filters.append(("type", "in", memory_types))
        if min_confidence > 0:
            filters.append(("confidence", ">=", min_confidence))

        docs = self._store.query(
            self.COLLECTION,
            filters=filters,
            order_by=("confidence", "desc"),
            limit=limit * 5,
        )
        if query:
            q_words = query.lower().split()
            docs = [
                d for d in docs
                if any(w in str(d.get("content", "")).lower() for w in q_words)
                or any(w in str(d.get("type", "")).lower() for w in q_words)
                or any(any(w in str(v).lower() for w in q_words) for v in (d.get("metadata") or {}).values())
            ]
        docs = docs[:limit]
        return [
            MemoryEntry(
                memory_id=d["memory_id"],
                project_id=d["project_id"],
                type=d["type"],
                content=d["content"],
                source=d["source"],
                confidence=float(d["confidence"]),
                metadata=d.get("metadata") or {},
                created_at=d["created_at"],
                updated_at=d["updated_at"],
            )
            for d in docs
        ]

    def list_all(self, project_id: str) -> list[MemoryEntry]:
        docs = self._store.query(
            self.COLLECTION,
            filters=[("project_id", "==", project_id)],
            order_by=("updated_at", "desc"),
        )
        return [
            MemoryEntry(
                memory_id=d["memory_id"],
                project_id=d["project_id"],
                type=d["type"],
                content=d["content"],
                source=d["source"],
                confidence=float(d["confidence"]),
                metadata=d.get("metadata") or {},
                created_at=d["created_at"],
                updated_at=d["updated_at"],
            )
            for d in docs
        ]

    def get(self, memory_id: str) -> MemoryEntry | None:
        d = self._store.get(self.COLLECTION, memory_id)
        if not d:
            return None
        return MemoryEntry(
            memory_id=d["memory_id"],
            project_id=d["project_id"],
            type=d["type"],
            content=d["content"],
            source=d["source"],
            confidence=float(d["confidence"]),
            metadata=d.get("metadata") or {},
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )

    def delete(self, memory_id: str) -> bool:
        return self._store.delete(self.COLLECTION, memory_id)

    def write_context_snapshot(
        self,
        project_id: str,
        repo_language: str | None,
        testing_framework_hint: str | None,
        default_branch: str | None,
        source: str = "repository_scan",
    ) -> list[MemoryEntry]:
        written: list[MemoryEntry] = []
        if repo_language:
            written.append(self.write(
                project_id,
                MEMORY_TYPE_CONVENTION,
                f"Repository primary language is {repo_language}.",
                source=source,
                confidence=0.95,
            ))
        if testing_framework_hint:
            written.append(self.write(
                project_id,
                MEMORY_TYPE_TESTING_CONVENTION,
                f"Repository likely uses '{testing_framework_hint}' for tests.",
                source=source,
                confidence=0.8,
            ))
        if default_branch:
            written.append(self.write(
                project_id,
                MEMORY_TYPE_REPO_STRUCTURE,
                f"Default branch is '{default_branch}'.",
                source=source,
                confidence=0.98,
            ))
        return written

    def build_reasoning_context(self, project_id: str, goal: str) -> str:
        # Retrieve all project memories sorted by confidence; do not hard-filter
        # by query so that all seeded context entries (language, branch, framework)
        # are always included in the reasoning context.
        memories = self.retrieve(project_id, limit=10)
        if not memories:
            return "No project memory available yet."
        lines = ["Project memory retrieved:"]
        for m in memories:
            lines.append(f"- [{m.type}] (confidence {m.confidence:.2f}) {m.content}")
        return "\n".join(lines)

    def reap_older_than(self, project_id: str, days: int = 0, *, older_than_seconds: float | None = None) -> list[MemoryEntry]:
        now = datetime.now(timezone.utc)
        if older_than_seconds is not None and older_than_seconds > 0:
            cutoff = (now - timedelta(seconds=older_than_seconds)).isoformat()
        else:
            cutoff = (now - timedelta(days=days)).isoformat()
        docs = self._store.query(
            self.COLLECTION,
            filters=[("project_id", "==", project_id), ("updated_at", "<=", cutoff)],
        )
        removed: list[MemoryEntry] = []
        for d in docs:
            entry = MemoryEntry(
                memory_id=d["memory_id"],
                project_id=d["project_id"],
                type=d["type"],
                content=d["content"],
                source=d["source"],
                confidence=float(d["confidence"]),
                metadata=d.get("metadata") or {},
                created_at=d["created_at"],
                updated_at=d["updated_at"],
            )
            if self.delete(d["memory_id"]):
                removed.append(entry)
        return removed
