"""Normalization and validation helpers for artifact entries.

Vendored from harbor's ``harbor.models.task.artifacts`` (Harbor 0.21).
Artifact entries are declared at the task level (``artifacts = [...]`` in
task.toml), the step level (``[[steps]].artifacts``), and the trial level
(job config). One *collection set* is the merged list of entries that a
single collection pass operates on. These helpers validate a collection set;
pier keeps its own host-path mapping in ``pier.trial.artifact_handler``, so
harbor's ``source_relative_path`` is not vendored.

Harbor's ``validate_artifact_entries`` also re-checks that a sidecar entry's
source is absolute. Pier does not: ``ArtifactConfig`` enforces that in its own
model validator, so every constructed instance already satisfies it and
string-form entries normalize to ``service=None``.
"""

import warnings
from collections.abc import Sequence
from itertools import combinations
from pathlib import PurePosixPath

from pier.models.task.config import MAIN_SERVICE_NAME, ArtifactConfig, TaskOS


def effective_artifact_service(artifact: ArtifactConfig) -> str:
    """The compose service an artifact entry targets (defaults to main)."""
    return artifact.service or MAIN_SERVICE_NAME


def convention_source_for_os(os: TaskOS) -> str:
    """The conventional agent publish directory for a target OS."""
    # Local import to avoid a circular dependency with models.trial.paths.
    from pier.models.trial.paths import EnvironmentPaths

    return EnvironmentPaths.for_os(os).artifacts_dir.as_posix()


def normalize_artifact_entries(
    entries: Sequence[str | ArtifactConfig],
) -> list[ArtifactConfig]:
    """Convert string-form entries to ``ArtifactConfig``."""
    return [
        ArtifactConfig(source=entry) if isinstance(entry, str) else entry
        for entry in entries
    ]


def is_convention_entry(artifact: ArtifactConfig, convention_source: str) -> bool:
    """True iff *artifact* is the main service's conventional publish dir."""
    return (
        artifact.source.rstrip("/") == convention_source.rstrip("/")
        and effective_artifact_service(artifact) == MAIN_SERVICE_NAME
    )


def with_convention_entry(
    entries: Sequence[str | ArtifactConfig],
    *,
    convention_source: str,
) -> list[ArtifactConfig]:
    """Normalize entries and prepend the implicit main convention entry.

    The convention entry is only injected when no entry already declares the
    convention dir for the main service; an explicit entry (e.g. with
    ``exclude`` patterns) replaces the implicit one.
    """
    normalized = normalize_artifact_entries(entries)
    if not any(
        is_convention_entry(artifact, convention_source) for artifact in normalized
    ):
        normalized.insert(0, ArtifactConfig(source=convention_source))
    return normalized


def sidecar_services(entries: Sequence[str | ArtifactConfig]) -> set[str]:
    """Names of non-main services referenced by artifact entries."""
    return {
        effective_artifact_service(artifact)
        for artifact in normalize_artifact_entries(entries)
        if effective_artifact_service(artifact) != MAIN_SERVICE_NAME
    }


def _paths_overlap(a: str, b: str) -> bool:
    """True when two container paths are equal or one contains the other."""
    path_a = PurePosixPath(a.rstrip("/") or "/")
    path_b = PurePosixPath(b.rstrip("/") or "/")
    return path_a == path_b or path_a in path_b.parents or path_b in path_a.parents


def validate_artifact_entries(
    entries: Sequence[str | ArtifactConfig],
    *,
    convention_source: str,
) -> None:
    """Validate one collection set of artifact entries.

    Overlapping sources or destinations do not raise: since all services
    share one flat artifacts base dir, overlapping entries simply collide on
    the same host path, and a later entry may overwrite the earlier one. We
    surface a load-time warning so the overlap is visible up front.
    """
    full = with_convention_entry(entries, convention_source=convention_source)

    for first, second in combinations(full, 2):
        if not _paths_overlap(first.source, second.source):
            continue
        first_service = effective_artifact_service(first)
        second_service = effective_artifact_service(second)
        warnings.warn(
            "Artifact sources overlap: "
            f"{first.source!r} (service {first_service!r}) and "
            f"{second.source!r} (service {second_service!r}) map to the same "
            "location under the shared artifacts dir; a later collection may "
            "overwrite the earlier one.",
            UserWarning,
            stacklevel=2,
        )

    destinations = [
        artifact.destination for artifact in full if artifact.destination is not None
    ]
    for first_dest, second_dest in combinations(destinations, 2):
        if _paths_overlap(first_dest, second_dest):
            warnings.warn(
                f"Artifact destinations overlap: {first_dest!r} and "
                f"{second_dest!r} map to the same location under the artifacts "
                "dir; a later collection may overwrite the earlier one.",
                UserWarning,
                stacklevel=2,
            )
