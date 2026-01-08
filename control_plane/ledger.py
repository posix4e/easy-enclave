from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional


class LedgerError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_cents(value: object) -> int:
    if value is None:
        raise LedgerError("missing_amount")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise LedgerError("invalid_amount")
    if amount < 0:
        raise LedgerError("invalid_amount")
    cents = (amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def parse_vcpu_hours(value: object) -> Decimal:
    if value is None:
        raise LedgerError("missing_vcpu_hours")
    try:
        hours = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise LedgerError("invalid_vcpu_hours")
    if hours <= 0:
        raise LedgerError("invalid_vcpu_hours")
    return hours


@dataclass
class NodeEligibility:
    eligible: bool
    reasons: list[str]


class LedgerStore:
    def __init__(self, db_path: str) -> None:
        _ensure_parent(db_path)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._db:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    price_cents_per_vcpu_hour INTEGER,
                    stake_tier TEXT,
                    stake_amount_cents INTEGER,
                    attestation_status TEXT NOT NULL,
                    health_status TEXT NOT NULL,
                    last_attested_at TEXT,
                    last_health_at TEXT,
                    node_token_hash TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    balance_cents INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger (
                    entry_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    delta_cents INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    ref_type TEXT,
                    ref_id TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS credit_locks (
                    lock_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    usage_id TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS usage (
                    usage_id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    vcpu_hours TEXT NOT NULL,
                    price_cents_per_vcpu_hour INTEGER NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lock_id TEXT NOT NULL,
                    reported_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS abuse_reports (
                    report_id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    period_start TEXT,
                    period_end TEXT,
                    status TEXT NOT NULL,
                    reported_by TEXT,
                    authorized_by TEXT,
                    created_at TEXT NOT NULL,
                    authorized_at TEXT,
                    reason TEXT
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS node_events (
                    event_id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    detail TEXT
                )
                """
            )

    def _fetch_one(self, query: str, params: tuple) -> Optional[sqlite3.Row]:
        return self._db.execute(query, params).fetchone()

    def _fetch_all(self, query: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._db.execute(query, params).fetchall()

    def _ensure_account(self, account_id: str) -> None:
        now = _utcnow()
        row = self._fetch_one("SELECT account_id FROM accounts WHERE account_id = ?", (account_id,))
        if row:
            return
        self._db.execute(
            "INSERT INTO accounts (account_id, balance_cents, created_at, updated_at) VALUES (?, 0, ?, ?)",
            (account_id, now, now),
        )

    def _apply_balance_delta(self, account_id: str, delta_cents: int) -> None:
        row = self._fetch_one("SELECT balance_cents FROM accounts WHERE account_id = ?", (account_id,))
        if not row:
            self._ensure_account(account_id)
            row = self._fetch_one("SELECT balance_cents FROM accounts WHERE account_id = ?", (account_id,))
        current = int(row["balance_cents"])
        updated = current + delta_cents
        if updated < 0:
            raise LedgerError("insufficient_funds")
        now = _utcnow()
        self._db.execute(
            "UPDATE accounts SET balance_cents = ?, updated_at = ? WHERE account_id = ?",
            (updated, now, account_id),
        )

    def _insert_ledger_entry(
        self,
        account_id: str,
        delta_cents: int,
        reason: str,
        ref_type: Optional[str],
        ref_id: Optional[str],
    ) -> str:
        entry_id = uuid.uuid4().hex
        self._db.execute(
            """
            INSERT INTO ledger (entry_id, account_id, delta_cents, reason, ref_type, ref_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (entry_id, account_id, delta_cents, reason, ref_type, ref_id, _utcnow()),
        )
        return entry_id

    def ensure_node(self, node_id: str) -> None:
        now = _utcnow()
        with self._lock, self._db:
            row = self._fetch_one("SELECT node_id FROM nodes WHERE node_id = ?", (node_id,))
            if row:
                return
            self._db.execute(
                """
                INSERT INTO nodes (
                    node_id, status, price_cents_per_vcpu_hour, stake_tier, stake_amount_cents,
                    attestation_status, health_status, created_at, updated_at
                ) VALUES (?, 'active', NULL, NULL, NULL, 'unknown', 'unknown', ?, ?)
                """,
                (node_id, now, now),
            )
            self._ensure_account(node_id)

    def register_node(
        self,
        node_id: str,
        price_cents_per_vcpu_hour: Optional[int],
        stake_tier: Optional[str],
        stake_amount_cents: Optional[int],
        *,
        allow_update: bool,
        rotate_token: bool,
    ) -> dict:
        now = _utcnow()
        token_value: Optional[str] = None
        with self._lock, self._db:
            row = self._fetch_one("SELECT node_id, node_token_hash FROM nodes WHERE node_id = ?", (node_id,))
            if row and not allow_update:
                raise LedgerError("node_exists")
            if row:
                token_hash = row["node_token_hash"]
                if rotate_token or not token_hash:
                    token_value = uuid.uuid4().hex
                    token_hash = hash_token(token_value)
                self._db.execute(
                    """
                    UPDATE nodes
                    SET price_cents_per_vcpu_hour = ?, stake_tier = ?, stake_amount_cents = ?,
                        node_token_hash = ?, updated_at = ?
                    WHERE node_id = ?
                    """,
                    (
                        price_cents_per_vcpu_hour,
                        stake_tier,
                        stake_amount_cents,
                        token_hash,
                        now,
                        node_id,
                    ),
                )
            else:
                token_value = uuid.uuid4().hex
                self._db.execute(
                    """
                    INSERT INTO nodes (
                        node_id, status, price_cents_per_vcpu_hour, stake_tier, stake_amount_cents,
                        attestation_status, health_status, node_token_hash, created_at, updated_at
                    ) VALUES (?, 'active', ?, ?, ?, 'unknown', 'unknown', ?, ?, ?)
                    """,
                    (
                        node_id,
                        price_cents_per_vcpu_hour,
                        stake_tier,
                        stake_amount_cents,
                        hash_token(token_value),
                        now,
                        now,
                    ),
                )
                self._ensure_account(node_id)
        node = self.get_node(node_id)
        return {"node": node, "node_token": token_value}

    def verify_node_token(self, node_id: str, token: str) -> bool:
        if not token:
            return False
        with self._lock:
            row = self._fetch_one("SELECT node_token_hash FROM nodes WHERE node_id = ?", (node_id,))
            if not row or not row["node_token_hash"]:
                return False
            return hash_token(token) == row["node_token_hash"]

    def get_node(self, node_id: str) -> Optional[dict]:
        with self._lock:
            row = self._fetch_one("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
            if not row:
                return None
            data = dict(row)
            data.pop("node_token_hash", None)
            return data

    def update_node_pricing(self, node_id: str, price_cents_per_vcpu_hour: int) -> None:
        now = _utcnow()
        with self._lock, self._db:
            self.ensure_node(node_id)
            self._db.execute(
                "UPDATE nodes SET price_cents_per_vcpu_hour = ?, updated_at = ? WHERE node_id = ?",
                (price_cents_per_vcpu_hour, now, node_id),
            )

    def update_node_stake(self, node_id: str, stake_tier: Optional[str], stake_amount_cents: Optional[int]) -> None:
        now = _utcnow()
        with self._lock, self._db:
            self.ensure_node(node_id)
            self._db.execute(
                "UPDATE nodes SET stake_tier = ?, stake_amount_cents = ?, updated_at = ? WHERE node_id = ?",
                (stake_tier, stake_amount_cents, now, node_id),
            )

    def mark_attestation(self, node_id: str, status: str) -> None:
        now = _utcnow()
        with self._lock, self._db:
            self.ensure_node(node_id)
            self._db.execute(
                "UPDATE nodes SET attestation_status = ?, last_attested_at = ?, updated_at = ? WHERE node_id = ?",
                (status, now, now, node_id),
            )

    def mark_health(self, node_id: str, status: str) -> None:
        now = _utcnow()
        with self._lock, self._db:
            self.ensure_node(node_id)
            self._db.execute(
                "UPDATE nodes SET health_status = ?, last_health_at = ?, updated_at = ? WHERE node_id = ?",
                (status, now, now, node_id),
            )

    def record_node_event(self, node_id: str, event_type: str, detail: Optional[str]) -> None:
        with self._lock, self._db:
            self.ensure_node(node_id)
            self._db.execute(
                """
                INSERT INTO node_events (event_id, node_id, event_type, occurred_at, detail)
                VALUES (?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, node_id, event_type, _utcnow(), detail),
            )

    def purchase_credits(self, account_id: str, amount_cents: int) -> dict:
        if amount_cents <= 0:
            raise LedgerError("invalid_amount")
        with self._lock, self._db:
            self._ensure_account(account_id)
            self._apply_balance_delta(account_id, amount_cents)
            self._insert_ledger_entry(account_id, amount_cents, "purchase", "purchase", None)
        return self.get_balance(account_id)

    def transfer_credits(self, from_account: str, to_account: str, amount_cents: int) -> dict:
        if amount_cents <= 0:
            raise LedgerError("invalid_amount")
        transfer_id = uuid.uuid4().hex
        with self._lock, self._db:
            self._apply_balance_delta(from_account, -amount_cents)
            self._insert_ledger_entry(from_account, -amount_cents, "transfer_out", "transfer", transfer_id)
            self._apply_balance_delta(to_account, amount_cents)
            self._insert_ledger_entry(to_account, amount_cents, "transfer_in", "transfer", transfer_id)
        return {"transfer_id": transfer_id}

    def lock_credits(
        self,
        account_id: str,
        usage_id: str,
        amount_cents: int,
        period_start: str,
        period_end: str,
    ) -> str:
        lock_id = uuid.uuid4().hex
        self._apply_balance_delta(account_id, -amount_cents)
        self._insert_ledger_entry(account_id, -amount_cents, "lock", "usage", usage_id)
        self._db.execute(
            """
            INSERT INTO credit_locks (lock_id, account_id, usage_id, amount_cents, period_start, period_end, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'locked', ?)
            """,
            (lock_id, account_id, usage_id, amount_cents, period_start, period_end, _utcnow()),
        )
        return lock_id

    def _release_lock(self, lock_id: str, account_id: str, amount_cents: int, usage_id: str) -> None:
        self._apply_balance_delta(account_id, amount_cents)
        self._insert_ledger_entry(account_id, amount_cents, "unlock", "usage", usage_id)
        self._db.execute(
            "UPDATE credit_locks SET status = 'released' WHERE lock_id = ?",
            (lock_id,),
        )

    def _settle_lock(self, lock_id: str, provider_id: str, amount_cents: int, usage_id: str) -> None:
        self._apply_balance_delta(provider_id, amount_cents)
        self._insert_ledger_entry(provider_id, amount_cents, "settlement", "usage", usage_id)
        self._db.execute(
            "UPDATE credit_locks SET status = 'settled' WHERE lock_id = ?",
            (lock_id,),
        )

    def report_usage(
        self,
        account_id: str,
        node_id: str,
        vcpu_hours: Decimal,
        period_start: str,
        period_end: str,
    ) -> dict:
        usage_id = uuid.uuid4().hex
        with self._lock, self._db:
            node = self._fetch_one("SELECT price_cents_per_vcpu_hour FROM nodes WHERE node_id = ?", (node_id,))
            if not node or node["price_cents_per_vcpu_hour"] is None:
                raise LedgerError("node_price_missing")
            price_cents = int(node["price_cents_per_vcpu_hour"])
            amount_cents = int((vcpu_hours * Decimal(price_cents)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            if amount_cents <= 0:
                raise LedgerError("invalid_amount")
            self._ensure_account(account_id)
            lock_id = self.lock_credits(account_id, usage_id, amount_cents, period_start, period_end)
            self._db.execute(
                """
                INSERT INTO usage (
                    usage_id, node_id, account_id, vcpu_hours, price_cents_per_vcpu_hour,
                    amount_cents, period_start, period_end, status, lock_id, reported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'locked', ?, ?)
                """,
                (
                    usage_id,
                    node_id,
                    account_id,
                    str(vcpu_hours),
                    price_cents,
                    amount_cents,
                    period_start,
                    period_end,
                    lock_id,
                    _utcnow(),
                ),
            )
        return {"usage_id": usage_id, "lock_id": lock_id, "amount_cents": amount_cents}

    def _eligible_for_settlement(self, node_id: str, period_start: str, period_end: str) -> NodeEligibility:
        reasons: list[str] = []
        node = self._fetch_one("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
        if not node:
            return NodeEligibility(False, ["node_not_found"])
        if node["status"] != "active":
            reasons.append("node_inactive")
        if node["attestation_status"] != "valid":
            reasons.append("attestation_invalid")
        if node["health_status"] != "pass":
            reasons.append("health_fail")
        if not node["stake_amount_cents"] or int(node["stake_amount_cents"]) <= 0:
            reasons.append("stake_missing")

        events = self._fetch_all(
            """
            SELECT event_type FROM node_events
            WHERE node_id = ? AND occurred_at >= ? AND occurred_at <= ?
            """,
            (node_id, period_start, period_end),
        )
        for event in events:
            if event["event_type"] == "health_miss":
                reasons.append("health_miss")
            if event["event_type"] == "attest_miss":
                reasons.append("attest_miss")

        abuse = self._fetch_one(
            """
            SELECT report_id FROM abuse_reports
            WHERE node_id = ? AND status = 'authorized'
              AND (period_start IS NULL OR period_start <= ?)
              AND (period_end IS NULL OR period_end >= ?)
            LIMIT 1
            """,
            (node_id, period_end, period_start),
        )
        if abuse:
            reasons.append("abuse_authorized")
        return NodeEligibility(len(reasons) == 0, reasons)

    def settle_period(self, node_id: str, period_start: str, period_end: str) -> dict:
        with self._lock, self._db:
            eligibility = self._eligible_for_settlement(node_id, period_start, period_end)
            usages = self._fetch_all(
                """
                SELECT usage_id, account_id, amount_cents, lock_id FROM usage
                WHERE node_id = ? AND period_start = ? AND period_end = ? AND status = 'locked'
                """,
                (node_id, period_start, period_end),
            )
            settled = 0
            failed = 0
            for usage in usages:
                usage_id = usage["usage_id"]
                account_id = usage["account_id"]
                amount_cents = int(usage["amount_cents"])
                lock_id = usage["lock_id"]
                if eligibility.eligible:
                    self._settle_lock(lock_id, node_id, amount_cents, usage_id)
                    self._db.execute("UPDATE usage SET status = 'settled' WHERE usage_id = ?", (usage_id,))
                    settled += 1
                else:
                    self._release_lock(lock_id, account_id, amount_cents, usage_id)
                    self._db.execute("UPDATE usage SET status = 'failed' WHERE usage_id = ?", (usage_id,))
                    failed += 1
        return {
            "node_id": node_id,
            "period_start": period_start,
            "period_end": period_end,
            "eligible": eligibility.eligible,
            "reasons": eligibility.reasons,
            "settled": settled,
            "failed": failed,
        }

    def file_abuse_report(
        self,
        node_id: str,
        period_start: Optional[str],
        period_end: Optional[str],
        reported_by: Optional[str],
        reason: Optional[str],
    ) -> dict:
        report_id = uuid.uuid4().hex
        with self._lock, self._db:
            self.ensure_node(node_id)
            self._db.execute(
                """
                INSERT INTO abuse_reports (
                    report_id, node_id, period_start, period_end, status, reported_by, created_at, reason
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (report_id, node_id, period_start, period_end, reported_by, _utcnow(), reason),
            )
        return {"report_id": report_id, "status": "pending"}

    def authorize_abuse_report(self, report_id: str, authorized_by: str, action: str) -> dict:
        if action not in {"authorize", "deny"}:
            raise LedgerError("invalid_action")
        status = "authorized" if action == "authorize" else "denied"
        with self._lock, self._db:
            row = self._fetch_one("SELECT report_id FROM abuse_reports WHERE report_id = ?", (report_id,))
            if not row:
                raise LedgerError("report_not_found")
            self._db.execute(
                """
                UPDATE abuse_reports
                SET status = ?, authorized_by = ?, authorized_at = ?
                WHERE report_id = ?
                """,
                (status, authorized_by, _utcnow(), report_id),
            )
        return {"report_id": report_id, "status": status}

    def get_balance(self, account_id: str) -> dict:
        with self._lock:
            row = self._fetch_one("SELECT balance_cents FROM accounts WHERE account_id = ?", (account_id,))
            balance_cents = int(row["balance_cents"]) if row else 0
        return {"account_id": account_id, "balance_cents": balance_cents}
