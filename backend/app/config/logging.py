from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

from backend.app.config.settings import Settings


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter with job_id/project_id/step context."""

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "funcName": record.funcName,
            "line": record.lineno,
        }
        context = getattr(record, "opspilot_context", None)
        if isinstance(context, dict):
            for key, value in context.items():
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class StructuredLogger:
    """Thin wrapper that attaches OpsPilot-wide structured context."""

    def __init__(self, settings: Settings, name: str = "opspilot") -> None:
        self._settings = settings
        self._logger = logging.getLogger(name)
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(JsonFormatter())
            self._logger.addHandler(handler)
        level = getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO)
        self._logger.setLevel(level)

    @property
    def raw(self) -> logging.Logger:
        return self._logger

    def _extra(
        self,
        job_id: str | None = None,
        project_id: str | None = None,
        agent_step: str | None = None,
        tool_name: str | None = None,
        duration_ms: int | None = None,
        status: str | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx: dict[str, Any] = {}
        if job_id:
            ctx["job_id"] = job_id
        if project_id:
            ctx["project_id"] = project_id
        if agent_step:
            ctx["agent_step"] = agent_step
        if tool_name:
            ctx["tool_name"] = tool_name
        if duration_ms is not None:
            ctx["duration_ms"] = duration_ms
        if status:
            ctx["status"] = status
        if error:
            ctx["error"] = error
        if extra:
            ctx.update(extra)
        return {"opspilot_context": ctx}

    def info(
        self,
        message: str,
        job_id: str | None = None,
        project_id: str | None = None,
        agent_step: str | None = None,
        tool_name: str | None = None,
        duration_ms: int | None = None,
        status: str | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._logger.info(
            message,
            extra=self._extra(job_id, project_id, agent_step, tool_name, duration_ms, status, error, extra),
        )

    def warning(
        self,
        message: str,
        job_id: str | None = None,
        project_id: str | None = None,
        agent_step: str | None = None,
        tool_name: str | None = None,
        duration_ms: int | None = None,
        status: str | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._logger.warning(
            message,
            extra=self._extra(job_id, project_id, agent_step, tool_name, duration_ms, status, error, extra),
        )

    def error(
        self,
        message: str,
        job_id: str | None = None,
        project_id: str | None = None,
        agent_step: str | None = None,
        tool_name: str | None = None,
        duration_ms: int | None = None,
        status: str | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._logger.error(
            message,
            extra=self._extra(job_id, project_id, agent_step, tool_name, duration_ms, status, error, extra),
        )

    def debug(
        self,
        message: str,
        job_id: str | None = None,
        project_id: str | None = None,
        agent_step: str | None = None,
        tool_name: str | None = None,
        duration_ms: int | None = None,
        status: str | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._logger.debug(
            message,
            extra=self._extra(job_id, project_id, agent_step, tool_name, duration_ms, status, error, extra),
        )

    def timed_block(
        self,
        message: str,
        job_id: str | None = None,
        project_id: str | None = None,
        agent_step: str | None = None,
        tool_name: str | None = None,
    ) -> "_TimedBlock":
        return _TimedBlock(self, message, job_id, project_id, agent_step, tool_name)


class _TimedBlock:
    def __init__(
        self,
        logger: StructuredLogger,
        message: str,
        job_id: str | None,
        project_id: str | None,
        agent_step: str | None,
        tool_name: str | None,
    ) -> None:
        self._logger = logger
        self._message = message
        self._job_id = job_id
        self._project_id = project_id
        self._agent_step = agent_step
        self._tool_name = tool_name
        self._started: float = 0.0

    def __enter__(self) -> "_TimedBlock":
        self._started = time.perf_counter()
        self._logger.info(
            f"START {self._message}",
            job_id=self._job_id,
            project_id=self._project_id,
            agent_step=self._agent_step,
            tool_name=self._tool_name,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        duration_ms = int((time.perf_counter() - self._started) * 1000)
        status = "success" if exc_type is None else "error"
        error = str(exc_val) if exc_val else None
        self._logger.info(
            f"END {self._message}",
            job_id=self._job_id,
            project_id=self._project_id,
            agent_step=self._agent_step,
            tool_name=self._tool_name,
            duration_ms=duration_ms,
            status=status,
            error=error,
        )
