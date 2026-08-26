"""Pub/Sub worker skeleton for OpsPilot background jobs.

When deployed to Google Cloud, this worker subscribes to the OpsPilot
job queue topic and invokes the ADK orchestrator for each incoming message.

The worker intentionally shares the backend application code — no new agent
logic is introduced here. Only the transport changes from
`asyncio.create_task()` (local) to `Pub/Sub pull + ack` (Cloud).

Usage (local with Pub/Sub emulator):
  GOOGLE_CLOUD_PROJECT=local-dev \
  PUBSUB_EMULATOR_HOST=localhost:8085 \
  python -m backend.app.workflows.pubsub_worker
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from backend.app.config.settings import get_settings

settings = get_settings()


def _get_pubsub_client() -> Any | None:
    try:
        from google.cloud import pubsub_v1  # type: ignore

        return pubsub_v1
    except Exception:
        return None


def ensure_topics_and_subscriptions(project_id: str, topic_id: str, subscription_id: str) -> None:
    pubsub_v1 = _get_pubsub_client()
    if pubsub_v1 is None:
        return
    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()
    topic_path = publisher.topic_path(project_id, topic_id)
    sub_path = subscriber.subscription_path(project_id, subscription_id)
    try:
        publisher.create_topic(name=topic_path)
    except Exception:
        pass
    try:
        subscriber.create_subscription(name=sub_path, topic=topic_path)
    except Exception:
        pass


def publish_job_message(project_id: str, topic_id: str, payload: dict[str, Any]) -> str:
    pubsub_v1 = _get_pubsub_client()
    if pubsub_v1 is None:
        raise RuntimeError("google-cloud-pubsub not installed")
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project_id, topic_id)
    data = json.dumps(payload, default=str).encode("utf-8")
    future = publisher.publish(topic_path, data)
    return future.result()


def handle_message(message: Any) -> None:
    """Handle a single pulled Pub/Sub message representing a queued job.

    Message payload:
        {
          "job_id": "...",
          "project_id": "...",
          "goal": "...",
          "github_owner": "...",
          "github_repo": "...",
          "auto_approve": false,
          "demo_mode": false,
        }
    """
    from backend.app.agents import OpsPilotOrchestrator

    try:
        payload = json.loads(message.data.decode("utf-8"))
    except Exception:
        message.nack()
        return

    orchestrator = OpsPilotOrchestrator(
        settings, demo_mode=bool(payload.get("demo_mode"))
    )

    async def _run_job() -> None:
        await orchestrator.start_job(
            goal=str(payload.get("goal", "")),
            project_id=str(payload.get("project_id", "")),
            github_owner=str(payload.get("github_owner", "")),
            github_repo=str(payload.get("github_repo", "")),
            auto_approve=bool(payload.get("auto_approve", False)),
            background=False,
        )

    import asyncio

    try:
        asyncio.run(_run_job())
        message.ack()
    except Exception:
        message.nack()


def run_forever(project_id: str, subscription_id: str) -> None:
    pubsub_v1 = _get_pubsub_client()
    if pubsub_v1 is None:
        print("[pubsub-worker] google-cloud-pubsub not installed; idle loop only.", file=sys.stderr)
        while True:
            time.sleep(60)
    subscriber = pubsub_v1.SubscriberClient()
    sub_path = subscriber.subscription_path(project_id, subscription_id)
    streaming_pull = subscriber.subscribe(sub_path, callback=handle_message)
    with subscriber:
        try:
            streaming_pull.result()
        except KeyboardInterrupt:
            streaming_pull.cancel()
            streaming_pull.result()


if __name__ == "__main__":
    pid = os.environ.get("GOOGLE_CLOUD_PROJECT") or settings.google_cloud_project or "local-dev"
    tid = os.environ.get("OPSPILOT_JOB_TOPIC", "opspilot-jobs")
    sid = os.environ.get("OPSPILOT_JOB_SUBSCRIPTION", "opspilot-jobs-worker")
    ensure_topics_and_subscriptions(pid, tid, sid)
    print(f"[pubsub-worker] starting project={pid} subscription={sid}")
    run_forever(pid, sid)
