"""A declared artifact that never arrives must fail the trial.

``download_artifacts`` is best-effort and has always returned a manifest whose
entries carry ``status``. Nothing read it, so a trial whose task declared
``/logs/artifacts/model.patch`` and produced none finished as an ordinary
completed trial with no error recorded. ``exception_info`` is the only field
``JobStats.increment`` counts as an error, so that is where it now lands.
"""

import asyncio
import functools
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pier.models.trial.artifact_manifest import ArtifactManifest, ArtifactManifestEntry
from pier.models.trial.config import ArtifactConfig
from pier.models.trial.paths import EnvironmentPaths
from pier.models.trial.result import ExceptionInfo, StepResult, TrialResult
from pier.trial.artifact_handler import (
    ArtifactHandler,
    MissingArtifactError,
    failed_artifact_entries,
)
from pier.trial.trial import Trial

ENV_ARTIFACTS_DIR = EnvironmentPaths().artifacts_dir


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def _entry(status: str, *, type: str = "file", source: str = "/logs/artifacts/model.patch"):
    return ArtifactManifestEntry(
        source=source, destination="artifacts/model.patch", type=type, status=status
    )


class _StubTrial:
    """Just enough of Trial for the recorder under test."""

    def __init__(self, result: TrialResult) -> None:
        self._logger = logging.getLogger("test-trial")
        self._result = result

    @property
    def result(self) -> TrialResult:
        return self._result


def _trial_result() -> TrialResult:
    return TrialResult.model_construct(
        trial_name="t",
        task_name="task",
        exception_info=None,
        step_results=[],
    )


# --- which entries count ------------------------------------------------------


def test_a_failed_entry_counts():
    manifest = ArtifactManifest(entries=[_entry("failed")])

    assert failed_artifact_entries(manifest) == tuple(manifest.entries)


def test_an_empty_file_entry_counts():
    manifest = ArtifactManifest(entries=[_entry("empty")])

    assert len(failed_artifact_entries(manifest)) == 1


def test_an_empty_directory_entry_does_not_count():
    """An empty artifacts directory is the normal shape of a task with none."""
    manifest = ArtifactManifest(
        entries=[_entry("empty", type="directory", source="/logs/artifacts")]
    )

    assert failed_artifact_entries(manifest) == ()


def test_an_ok_entry_does_not_count():
    assert failed_artifact_entries(ArtifactManifest(entries=[_entry("ok")])) == ()


def test_the_error_names_every_offending_source():
    error = MissingArtifactError([_entry("failed"), _entry("empty", source="/logs/other")])

    assert "/logs/artifacts/model.patch (failed)" in str(error)
    assert "/logs/other (empty)" in str(error)


# --- how the status is assigned ----------------------------------------------


def test_a_zero_byte_downloaded_file_is_empty(tmp_path: Path):
    target = tmp_path / "model.patch"
    target.write_text("")

    assert ArtifactHandler._downloaded_status(target, "file") == "empty"


def test_a_non_empty_downloaded_file_is_ok(tmp_path: Path):
    target = tmp_path / "model.patch"
    target.write_text("diff --git a/x b/x\n")

    assert ArtifactHandler._downloaded_status(target, "file") == "ok"


def test_a_missing_target_is_left_ok(tmp_path: Path):
    """The download reported success; the path belongs to the caller."""
    assert ArtifactHandler._downloaded_status(tmp_path / "nope", "file") == "ok"


def test_a_directory_is_left_ok(tmp_path: Path):
    assert ArtifactHandler._downloaded_status(tmp_path, "directory") == "ok"


@run_async
async def test_download_marks_an_empty_declared_file_empty(tmp_path: Path):
    artifacts_dir = tmp_path / "artifacts"
    environment = AsyncMock()
    environment.capabilities.mounted = False
    # The convention artifacts dir is a directory; the declared patch is a file.
    environment.is_dir = AsyncMock(
        side_effect=lambda source, user=None: not source.endswith(".patch")
    )
    environment.download_dir = AsyncMock()

    async def _download_file(*, source_path: str, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("")

    environment.download_file = AsyncMock(side_effect=_download_file)
    handler = ArtifactHandler(
        artifacts=[ArtifactConfig(source="/logs/artifacts/model.patch")],
        logger=logging.getLogger("test"),
    )

    manifest = await handler.download_artifacts(
        environment, artifacts_dir, source_artifacts_dir=ENV_ARTIFACTS_DIR
    )

    patch_entries = [e for e in manifest.entries if e.source.endswith("model.patch")]
    assert [entry.status for entry in patch_entries] == ["empty"]
    assert len(failed_artifact_entries(manifest)) == 1


# --- what the trial records ---------------------------------------------------


def test_a_missing_artifact_becomes_trial_exception_info():
    result = _trial_result()
    stub = _StubTrial(result)

    Trial._record_missing_artifacts(stub, ArtifactManifest(entries=[_entry("failed")]))

    assert result.exception_info is not None
    assert result.exception_info.exception_type == "MissingArtifactError"
    assert "model.patch" in result.exception_info.exception_message


def test_a_complete_manifest_records_nothing():
    result = _trial_result()

    Trial._record_missing_artifacts(_StubTrial(result), ArtifactManifest(entries=[_entry("ok")]))

    assert result.exception_info is None


def test_an_in_flight_exception_is_never_clobbered():
    """This also runs on the cancel and outer-except paths."""
    result = _trial_result()
    result.exception_info = ExceptionInfo.from_exception(RuntimeError("the real cause"))

    Trial._record_missing_artifacts(_StubTrial(result), ArtifactManifest(entries=[_entry("failed")]))

    assert result.exception_info.exception_type == "RuntimeError"
    assert result.exception_info.exception_message == "the real cause"


def test_a_step_result_is_recorded_when_given():
    result = _trial_result()
    step_result = StepResult(step_name="only")

    Trial._record_missing_artifacts(
        _StubTrial(result), ArtifactManifest(entries=[_entry("failed")]), step_result=step_result
    )

    assert step_result.exception_info is not None
    assert step_result.exception_info.exception_type == "MissingArtifactError"
    assert result.exception_info is None


def test_the_recorded_exception_is_what_job_stats_counts():
    """JobStats.increment counts a trial as errored iff exception_info is set."""
    from pier.models.job.result import JobStats
    from pier.models.trial.result import AgentInfo

    result = TrialResult.model_construct(
        trial_name="t",
        task_name="task",
        source=None,
        verifier_result=None,
        exception_info=None,
        step_results=[],
        agent_info=AgentInfo(name="atomic", version="0.9.3", model_info=None),
    )
    Trial._record_missing_artifacts(_StubTrial(result), ArtifactManifest(entries=[_entry("failed")]))

    stats = JobStats()
    stats.increment(result)

    assert stats.n_errored_trials == 1
    assert stats.n_completed_trials == 1
    errored = stats.evals[next(iter(stats.evals))].exception_stats
    assert "MissingArtifactError" in errored


@pytest.mark.parametrize("status", ["failed", "empty"])
def test_both_statuses_error_the_trial(status: str):
    result = _trial_result()

    Trial._record_missing_artifacts(_StubTrial(result), ArtifactManifest(entries=[_entry(status)]))

    assert result.exception_info is not None
