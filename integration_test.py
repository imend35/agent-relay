"""Acceptance test against a running Agent Relay HTTP service.

The test deliberately uses HTTP for the relay operations and a separate
database connection for the final assertion. It can therefore be run against
the local process, Docker Compose, or the port-forwarded Kubernetes service.

Required environment variables:

* ``RELAY_BASE_URL`` - for example ``http://127.0.0.1:8000``
* ``TEST_DATABASE_URL`` - a database URL reachable from the test process
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
from sqlalchemy import create_engine, text


BASE_URL = os.getenv("RELAY_BASE_URL")
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


def _assert_status(response: httpx.Response, expected: int) -> dict:
    assert response.status_code == expected, response.text
    return response.json() if response.content else {}


def test_first_acceptance_scenario_over_http_and_persistence():
    """Register, send, claim, complete, and read a task through the real API."""

    if not BASE_URL or not TEST_DATABASE_URL:
        pytest.skip("set RELAY_BASE_URL and TEST_DATABASE_URL to run the live integration test")

    run_id = uuid.uuid4().hex[:10]
    # Local Docker/kind endpoints must not be sent through a developer's
    # HTTP(S)/SOCKS proxy environment variables.
    with httpx.Client(base_url=BASE_URL.rstrip("/"), timeout=15, trust_env=False) as client:
        sender = _assert_status(
            client.post("/api/v1/agents", json={"name": f"sender-{run_id}"}), 201
        )
        recipient = _assert_status(
            client.post("/api/v1/agents", json={"name": f"recipient-{run_id}"}), 201
        )
        sender_headers = {"Authorization": f"Bearer {sender['token']}"}
        recipient_headers = {"Authorization": f"Bearer {recipient['token']}"}

        sent = _assert_status(
            client.post(
                "/api/v1/tasks",
                headers={**sender_headers, "Idempotency-Key": f"integration-{run_id}"},
                json={"to": recipient["agent_id"], "input": "hello from integration"},
            ),
            201,
        )
        assert sent["status"] == "queued"

        claim = _assert_status(
            client.post(
                "/api/v1/tasks/claim",
                headers=recipient_headers,
                json={"worker_id": f"integration-worker-{run_id}", "wait_seconds": 0},
            ),
            200,
        )
        assert claim["task_id"] == sent["task_id"]
        assert claim["input"] == "hello from integration"

        completed = _assert_status(
            client.post(
                f"/api/v1/tasks/{claim['task_id']}/complete",
                headers=recipient_headers,
                json={"claim_token": claim["claim_token"], "output": "HELLO FROM INTEGRATION"},
            ),
            200,
        )
        assert completed == {"task_id": sent["task_id"], "status": "completed"}

        result = _assert_status(
            client.get(f"/api/v1/tasks/{sent['task_id']}", headers=sender_headers), 200
        )
        assert result["status"] == "completed"
        assert result["output"] == "HELLO FROM INTEGRATION"

    db_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        with db_engine.connect() as connection:
            row = connection.execute(
                text("SELECT status, input, output FROM tasks WHERE id = :task_id"),
                {"task_id": sent["task_id"]},
            ).mappings().one()
        assert row["status"] == "completed"
        assert row["input"] == "hello from integration"
        assert row["output"] == "HELLO FROM INTEGRATION"
    finally:
        db_engine.dispose()
