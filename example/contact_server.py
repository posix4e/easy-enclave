#!/usr/bin/env python3
from __future__ import annotations

import base64
import hmac
import json
import os
import sqlite3
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DATA_DIR = Path(os.getenv("CONTACT_DATA_DIR", "/var/lib/easy-enclave/contact-server"))
DB_PATH = Path(os.getenv("CONTACT_DB_PATH", str(DATA_DIR / "contacts.db")))
KEY_PATH = Path(os.getenv("CONTACT_KEY_PATH", str(DATA_DIR / "hmac.key")))
API_TOKEN = os.getenv("CONTACT_API_TOKEN", "")
ADMIN_PATH = Path(__file__).with_name("contact_admin.html")


def ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_key() -> bytes:
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes()
    key = os.urandom(32)
    KEY_PATH.write_bytes(key)
    os.chmod(KEY_PATH, 0o600)
    return key


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, hmac TEXT UNIQUE)"
    )
    return conn


def compute_hmac(key: bytes, contact: str) -> str:
    digest = hmac.new(key, contact.encode("utf-8"), sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class Handler(BaseHTTPRequestHandler):
    server_version = "easy-enclave-contact/0.1"

    def _auth_failed(self) -> None:
        self.send_response(401)
        self.end_headers()

    def _require_auth(self) -> bool:
        if not API_TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {API_TOKEN}":
            return True
        self._auth_failed()
        return False

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))

    def _send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path) -> None:
        if not path.exists():
            self.send_response(404)
            self.end_headers()
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json({"status": "ok"})
            return
        if self.path == "/admin":
            self._send_file(ADMIN_PATH)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if not self._require_auth():
            return
        if self.path == "/register":
            payload = self._read_json()
            contacts = payload.get("contacts", [])
            if not isinstance(contacts, list):
                self._send_json({"error": "contacts must be list"}, status=400)
                return
            hashes = [compute_hmac(self.server.key, contact) for contact in contacts]
            with self.server.db:
                for h in hashes:
                    self.server.db.execute("INSERT OR IGNORE INTO contacts (hmac) VALUES (?)", (h,))
            self._send_json({"registered": len(hashes)})
            return

        if self.path == "/lookup":
            payload = self._read_json()
            contacts = payload.get("contacts", [])
            if not isinstance(contacts, list):
                self._send_json({"error": "contacts must be list"}, status=400)
                return
            hashes = [compute_hmac(self.server.key, contact) for contact in contacts]
            results = []
            cursor = self.server.db.cursor()
            for contact, h in zip(contacts, hashes):
                row = cursor.execute("SELECT 1 FROM contacts WHERE hmac = ?", (h,)).fetchone()
                results.append({"contact": contact, "registered": bool(row)})
            self._send_json({"results": results})
            return

        self.send_response(404)
        self.end_headers()


class ContactServer(HTTPServer):
    def __init__(self, addr: tuple[str, int], handler: type[BaseHTTPRequestHandler]):
        super().__init__(addr, handler)
        ensure_storage()
        self.key = load_key()
        self.db = connect_db()


def main() -> None:
    host = os.getenv("CONTACT_HOST", "0.0.0.0")
    port = int(os.getenv("CONTACT_PORT", "8080"))
    server = ContactServer((host, port), Handler)
    print(f"contact server listening on {host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
