"""Hash-chained append-only trust ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    sequence: int
    event_id: str
    event_type: str
    occurred_at: str
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str


class TrustLedger(Protocol):
    """Infrastructure boundary shared by memory, SQLite, and Firestore adapters."""

    @property
    def events(self) -> tuple[LedgerEvent, ...]: ...

    def append(self, event_type: str, payload: dict[str, Any]) -> LedgerEvent: ...

    def find(
        self, event_type: str | None = None, **payload_match: Any
    ) -> tuple[LedgerEvent, ...]: ...

    def verify_chain(self) -> bool: ...

    def export(self) -> Iterable[dict[str, Any]]: ...


class AppendOnlyLedger:
    """In-memory adapter whose semantics match the future Firestore adapter."""

    GENESIS_HASH = "0" * 64

    def __init__(self) -> None:
        self._events: list[LedgerEvent] = []
        self._lock = RLock()

    @property
    def events(self) -> tuple[LedgerEvent, ...]:
        with self._lock:
            return deepcopy(tuple(self._events))

    def append(self, event_type: str, payload: dict[str, Any]) -> LedgerEvent:
        with self._lock:
            return self._append_unlocked(event_type, deepcopy(payload))

    def _append_unlocked(self, event_type: str, payload: dict[str, Any]) -> LedgerEvent:
        sequence = len(self._events) + 1
        event_id = f"evt_{uuid4().hex[:16]}"
        occurred_at = datetime.now(UTC).isoformat()
        previous_hash = self._events[-1].event_hash if self._events else self.GENESIS_HASH
        canonical = self._canonical(
            sequence, event_id, event_type, occurred_at, payload, previous_hash
        )
        event = LedgerEvent(
            sequence=sequence,
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
            previous_hash=previous_hash,
            event_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        )
        self._events.append(event)
        return deepcopy(event)

    def find(self, event_type: str | None = None, **payload_match: Any) -> tuple[LedgerEvent, ...]:
        def matches(event: LedgerEvent) -> bool:
            if event_type is not None and event.event_type != event_type:
                return False
            return all(event.payload.get(key) == value for key, value in payload_match.items())

        return tuple(event for event in self.events if matches(event))

    def verify_chain(self) -> bool:
        previous_hash = self.GENESIS_HASH
        for event in self.events:
            canonical = self._canonical(
                event.sequence,
                event.event_id,
                event.event_type,
                event.occurred_at,
                event.payload,
                previous_hash,
            )
            if event.previous_hash != previous_hash:
                return False
            if hashlib.sha256(canonical.encode()).hexdigest() != event.event_hash:
                return False
            previous_hash = event.event_hash
        return True

    @staticmethod
    def _canonical(
        sequence: int,
        event_id: str,
        event_type: str,
        occurred_at: str,
        payload: dict[str, Any],
        previous_hash: str,
    ) -> str:
        body = {
            "sequence": sequence,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "payload": payload,
            "previous_hash": previous_hash,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)

    def export(self) -> Iterable[dict[str, Any]]:
        for event in self.events:
            yield {
                "sequence": event.sequence,
                "event_id": event.event_id,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
                "payload": event.payload,
                "previous_hash": event.previous_hash,
                "event_hash": event.event_hash,
            }


class SqliteLedger(AppendOnlyLedger):
    """Persistent local adapter with the same append-only hash-chain semantics."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trust_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @property
    def events(self) -> tuple[LedgerEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM trust_events ORDER BY sequence"
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def append(self, event_type: str, payload: dict[str, Any]) -> LedgerEvent:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT sequence, event_hash FROM trust_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = int(row["sequence"]) + 1 if row else 1
            previous_hash = str(row["event_hash"]) if row else self.GENESIS_HASH
            event_id = f"evt_{uuid4().hex[:16]}"
            occurred_at = datetime.now(UTC).isoformat()
            canonical = self._canonical(
                sequence, event_id, event_type, occurred_at, payload, previous_hash
            )
            event = LedgerEvent(
                sequence=sequence,
                event_id=event_id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
                previous_hash=previous_hash,
                event_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            )
            connection.execute(
                """
                INSERT INTO trust_events (
                    sequence, event_id, event_type, occurred_at, payload_json,
                    previous_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.sequence,
                    event.event_id,
                    event.event_type,
                    event.occurred_at,
                    json.dumps(event.payload, sort_keys=True, default=str),
                    event.previous_hash,
                    event.event_hash,
                ),
            )
        return event

    def find(
        self, event_type: str | None = None, **payload_match: Any
    ) -> tuple[LedgerEvent, ...]:
        return tuple(
            event
            for event in self.events
            if (event_type is None or event.event_type == event_type)
            and all(event.payload.get(key) == value for key, value in payload_match.items())
        )

    def verify_chain(self) -> bool:
        previous_hash = self.GENESIS_HASH
        for event in self.events:
            canonical = self._canonical(
                event.sequence,
                event.event_id,
                event.event_type,
                event.occurred_at,
                event.payload,
                previous_hash,
            )
            if event.previous_hash != previous_hash:
                return False
            if hashlib.sha256(canonical.encode()).hexdigest() != event.event_hash:
                return False
            previous_hash = event.event_hash
        return True

    @staticmethod
    def _from_row(row: sqlite3.Row) -> LedgerEvent:
        return LedgerEvent(
            sequence=int(row["sequence"]),
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            occurred_at=str(row["occurred_at"]),
            payload=json.loads(str(row["payload_json"])),
            previous_hash=str(row["previous_hash"]),
            event_hash=str(row["event_hash"]),
        )
