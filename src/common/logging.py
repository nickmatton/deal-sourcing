"""Structured logging configuration for the deal sourcing pipeline.

Call `configure_logging()` once at startup. All modules that use
`structlog.get_logger()` will then emit properly formatted, colored,
level-filtered output.
"""

import logging
import sys
import time
from contextlib import contextmanager
from enum import StrEnum
from functools import wraps
from typing import Any, Generator

import structlog


class PipelineStage(StrEnum):
    INGESTION = "0A·Ingestion"
    ENTITY_RESOLUTION = "0B·EntityRes"
    FEATURE_STORE = "0C·Features"
    SIGNAL_DETECTION = "1·Signals"
    THESIS_MATCHING = "2·ThesisMatch"
    VALUATION = "3A·Valuation"
    ALPHA_DETECTION = "3B·Alpha"
    OUTREACH = "4·Outreach"
    UNDERWRITING = "5·Underwrite"
    IC_PREP = "6·ICPrep"
    FEEDBACK = "7·Feedback"


def configure_logging(
    log_level: str = "INFO",
    json_output: bool = False,
    show_caller: bool = True,
) -> None:
    """Configure structlog with pretty console output or JSON for production.

    Must be called once at process startup before any logging occurs.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="%H:%M:%S", utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if show_caller:
        shared_processors.append(structlog.processors.CallsiteParameterAdder(
            [
                structlog.processors.CallsiteParameter.MODULE,
                structlog.processors.CallsiteParameter.FUNC_NAME,
            ]
        ))

    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(
            colors=sys.stderr.isatty(),
            pad_event_to=40,
        )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet noisy libraries
    for lib in ("httpx", "httpcore", "urllib3", "asyncio", "kafka"):
        logging.getLogger(lib).setLevel(logging.WARNING)


@contextmanager
def log_stage(
    stage: PipelineStage,
    **bind_kv: Any,
) -> Generator[structlog.stdlib.BoundLogger, None, None]:
    """Context manager that logs stage entry/exit with duration.

    Usage:
        with log_stage(PipelineStage.INGESTION, source="pitchbook") as log:
            log.info("fetching companies", batch_size=1000)
            ...
    """
    logger = structlog.get_logger().bind(stage=stage.value, **bind_kv)
    logger.info("stage.started")
    t0 = time.perf_counter()
    try:
        yield logger
        elapsed = time.perf_counter() - t0
        logger.info("stage.completed", duration_s=round(elapsed, 2))
    except Exception:
        elapsed = time.perf_counter() - t0
        logger.error("stage.failed", duration_s=round(elapsed, 2), exc_info=True)
        raise


@contextmanager
def log_step(
    step_name: str,
    parent_logger: structlog.stdlib.BoundLogger | None = None,
    **bind_kv: Any,
) -> Generator[structlog.stdlib.BoundLogger, None, None]:
    """Context manager for substeps within a stage.

    Usage:
        with log_stage(PipelineStage.VALUATION) as stage_log:
            with log_step("revenue_estimation", stage_log, entity="acme") as log:
                log.info("estimating revenue")
    """
    logger = (parent_logger or structlog.get_logger()).bind(step=step_name, **bind_kv)
    logger.debug("step.started")
    t0 = time.perf_counter()
    try:
        yield logger
        elapsed = time.perf_counter() - t0
        logger.debug("step.completed", duration_ms=round(elapsed * 1000, 1))
    except Exception:
        elapsed = time.perf_counter() - t0
        logger.error("step.failed", duration_ms=round(elapsed * 1000, 1), exc_info=True)
        raise


def log_model_event(
    event: str,
    model_name: str,
    model_version: str = "v1",
    **metrics: Any,
) -> None:
    """Log an ML model lifecycle event (train, predict, evaluate, drift)."""
    logger = structlog.get_logger().bind(
        model=model_name,
        model_version=model_version,
    )
    logger.info(event, **metrics)


@contextmanager
def log_pipeline_run(
    run_name: str = "pipeline",
    **bind_kv: Any,
) -> Generator[structlog.stdlib.BoundLogger, None, None]:
    """Top-level context manager for an entire pipeline run.

    Usage:
        with log_pipeline_run("weekly_scoring") as log:
            with log_stage(PipelineStage.SIGNAL_DETECTION) as slog:
                ...
    """
    logger = structlog.get_logger().bind(run=run_name, **bind_kv)
    logger.info("pipeline.started", **bind_kv)
    t0 = time.perf_counter()
    try:
        yield logger
        elapsed = time.perf_counter() - t0
        logger.info("pipeline.completed", total_duration_s=round(elapsed, 2))
    except Exception:
        elapsed = time.perf_counter() - t0
        logger.error("pipeline.failed", total_duration_s=round(elapsed, 2), exc_info=True)
        raise
