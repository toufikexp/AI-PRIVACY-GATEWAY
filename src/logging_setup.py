from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure stdlib logging + structlog with structured JSON by default.

    Hard rule: this codebase NEVER uses `print`. All output goes through
    structlog so audit invariants and log redaction are uniform.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    final_renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.UnicodeDecoder(),
            final_renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO),
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        stream=sys.stderr,
        force=True,
    )
