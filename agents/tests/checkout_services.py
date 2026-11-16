"""Run the real NovaMart and NovaConnect backends on free ports for cross-service tests.

Each pair gets its own data directories, so faults (``opsrelay-outage.json``) and records
never leak between tests. NovaMart is pointed at this NovaConnect with a short
confirmation timeout, so NovaConnect's controlled delay produces a real client timeout.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
CONFIRMATION_TIMEOUT = 0.5   # NovaMart -> NovaConnect
FAULT_DELAY = 1.0            # NovaConnect's controlled delay, longer than the timeout

CUSTOMER = {"full_name": "Ada Lovelace", "email": "ada@example.com", "phone": "+1 555 0100"}
ADDRESS = {"line1": "500 Market Street", "line2": "Suite 12", "city": "San Francisco", "state": "CA",
           "postal_code": "94105", "country": "US"}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class Server:
    name: str
    cwd: Path
    env: dict[str, str]
    health: str
    port: int = field(default_factory=_free_port)
    process: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, log_dir: Path) -> "Server":
        log = open(log_dir / f"{self.name}.log", "w")  # noqa: SIM115 - closed with the process
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(self.port), "--log-level", "warning"],
            cwd=self.cwd, env={**os.environ, **self.env}, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"{self.name} exited: {(log_dir / f'{self.name}.log').read_text()[-2000:]}")
            try:
                if httpx.get(self.url + self.health, timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise RuntimeError(f"{self.name} did not start")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()


class CheckoutServices:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.mart_data = tmp / "novamart"
        self.connect_db = tmp / "novaconnect" / "novaconnect.db"
        self.mart_data.mkdir(parents=True)
        self.connect_db.parent.mkdir(parents=True)
        self.novaconnect = Server("novaconnect", REPO / "message-platform" / "backend",
                                  {"NOVACONNECT_DB_PATH": str(self.connect_db)}, "/health")
        self.novamart = Server("novamart", REPO / "shopping" / "backend",
                               {"NOVAMART_DATA_DIR": str(self.mart_data), "NOVACONNECT_URL": self.novaconnect.url,
                                "NOVAMART_CONFIRMATION_TIMEOUT": str(CONFIRMATION_TIMEOUT)}, "/api/health")

    def start(self) -> "CheckoutServices":
        self.novaconnect.start(self.tmp)
        self.novamart.start(self.tmp)
        return self

    def stop(self) -> None:
        self.novamart.stop()
        self.novaconnect.stop()

    # ---- faults -------------------------------------------------------- #
    def novaconnect_fault(self, mode: str | None, delay: float = FAULT_DELAY) -> None:
        path = self.connect_db.parent / "opsrelay-outage.json"
        if mode is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps({"scenario": "confirmation_timeout", "mode": mode, "delay_seconds": delay}))

    def novamart_fault(self, scenario: str | None) -> None:
        path = self.mart_data / "opsrelay-outage.json"
        if scenario is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps({"scenario": scenario}))

    # ---- traffic and records ------------------------------------------- #
    def checkout(self, checkout_id: str, product_id: str = "nm-app-002", quantity: int = 1) -> httpx.Response:
        return httpx.post(f"{self.novamart.url}/api/checkout", timeout=10, json={
            "checkout_id": checkout_id, "items": [{"product_id": product_id, "quantity": quantity}],
            "shipping_method": "standard", "customer": CUSTOMER, "shipping_address": ADDRESS})

    def checkout_record(self, checkout_id: str) -> dict:
        return httpx.get(f"{self.novamart.url}/api/checkouts/{checkout_id}", timeout=5).json()

    def confirmations_for(self, order_id: str) -> list[dict]:
        return httpx.get(f"{self.novaconnect.url}/api/confirmations", params={"order_id": order_id},
                         timeout=5).json()["confirmations"]
