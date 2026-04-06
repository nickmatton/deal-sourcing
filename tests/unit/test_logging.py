"""Tests for the structured logging system."""

import logging

import pytest
import structlog

from src.common.logging import (
    PipelineStage,
    configure_logging,
    log_model_event,
    log_pipeline_run,
    log_stage,
    log_step,
)


class TestConfigureLogging:
    def test_configure_sets_level(self):
        configure_logging(log_level="WARNING")
        root = logging.getLogger()
        assert root.level == logging.WARNING
        # Reset for other tests
        configure_logging(log_level="DEBUG")

    def test_configure_json_mode(self):
        configure_logging(log_level="INFO", json_output=True)
        root = logging.getLogger()
        assert root.level == logging.INFO
        configure_logging(log_level="DEBUG", json_output=False)

    def test_noisy_libraries_quieted(self):
        configure_logging(log_level="DEBUG")
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING


class TestLogStage:
    def test_stage_context_manager(self, capfd):
        with log_stage(PipelineStage.SIGNAL_DETECTION, batch=100) as log:
            log.info("scoring companies")
        # Should not raise, context manager completes cleanly

    def test_stage_logs_failure(self):
        with pytest.raises(ValueError, match="test error"):
            with log_stage(PipelineStage.VALUATION) as log:
                raise ValueError("test error")

    def test_stage_yields_bound_logger(self):
        with log_stage(PipelineStage.INGESTION, source="pitchbook") as log:
            assert log is not None


class TestLogStep:
    def test_step_context_manager(self):
        with log_stage(PipelineStage.VALUATION) as stage_log:
            with log_step("revenue_estimation", stage_log, entity="acme") as step_log:
                step_log.debug("estimating")
        # Should complete without error

    def test_step_without_parent(self):
        with log_step("standalone_step") as log:
            log.debug("working")


class TestLogPipelineRun:
    def test_pipeline_run_context(self):
        with log_pipeline_run("test_run", mode="weekly") as log:
            log.info("running pipeline")

    def test_pipeline_run_failure(self):
        with pytest.raises(RuntimeError, match="pipeline broke"):
            with log_pipeline_run("failing_run") as log:
                raise RuntimeError("pipeline broke")


class TestLogModelEvent:
    def test_model_event_logs(self):
        log_model_event(
            "train_complete",
            "sell_probability",
            model_version="v2",
            auc=0.91,
            mape=0.35,
        )
        # Should not raise


class TestPipelineStageEnum:
    def test_all_stages_defined(self):
        assert len(PipelineStage) == 11
        assert PipelineStage.INGESTION == "0A·Ingestion"
        assert PipelineStage.FEEDBACK == "7·Feedback"
