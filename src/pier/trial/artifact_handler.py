import json
import logging
import shutil
from collections.abc import Sequence
from pathlib import Path, PurePath, PurePosixPath

from pier.environments.base import BaseEnvironment, EnvironmentPath
from pier.models.task.artifacts import (
    effective_artifact_service,
    is_convention_entry,
    with_convention_entry,
)
from pier.models.task.config import ArtifactConfig
from pier.models.trial.artifact_manifest import (
    ArtifactManifest,
    ArtifactManifestEntry,
)


class ArtifactHandler:
    def __init__(
        self,
        *,
        artifacts: Sequence[str | ArtifactConfig],
        logger: logging.Logger,
    ):
        self.artifacts = list(artifacts)
        self.logger = logger

    @staticmethod
    def move_dir_contents(src: Path, dst: Path) -> None:
        """Move all contents from src to dst, leaving src empty."""
        if not src.exists():
            return

        items = list(src.iterdir())
        if not items:
            return

        dst.mkdir(parents=True, exist_ok=True)
        for item in items:
            target = dst / item.name
            if target.exists():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(item), target)

    async def download_artifacts(
        self,
        source_env: BaseEnvironment,
        artifacts_dir: Path,
        *,
        source_artifacts_dir: EnvironmentPath,
        artifacts: Sequence[str | ArtifactConfig] | None = None,
    ) -> ArtifactManifest:
        """Best-effort artifact download with a manifest of attempted sources."""
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        entries: list[ArtifactManifestEntry] = []
        convention_source = self._environment_path_str(source_artifacts_dir)

        for artifact in self._normalized_artifacts(artifacts, convention_source):
            entries.append(
                await self._download_artifact(
                    source_env=source_env,
                    artifacts_dir=artifacts_dir,
                    artifact=artifact,
                    convention_source=convention_source,
                )
            )

        manifest = ArtifactManifest(entries=entries)
        self._write_manifest(artifacts_dir, manifest)
        return manifest

    async def upload_artifacts(
        self,
        target_env: BaseEnvironment,
        artifacts_dir: Path,
        *,
        source_artifacts_dir: EnvironmentPath,
        target_artifacts_dir: EnvironmentPath,
        artifacts: Sequence[str | ArtifactConfig] | None = None,
    ) -> None:
        """Upload host artifacts back to their configured environment sources."""
        source_convention = self._environment_path_str(source_artifacts_dir)
        target_convention = self._environment_path_str(target_artifacts_dir)

        for artifact in self._normalized_artifacts(artifacts, source_convention):
            host_path = self._host_path(
                artifacts_dir,
                artifact,
                convention_source=source_convention,
            )
            if not host_path.exists():
                continue

            target_source = self._upload_target_source(
                artifact,
                source_convention=source_convention,
                target_convention=target_convention,
            )
            if host_path.is_dir():
                await target_env.empty_dirs([target_source], chmod=True)
                await target_env.upload_dir(
                    source_dir=host_path,
                    target_dir=target_source,
                )
                continue

            await target_env.upload_file(
                source_path=host_path,
                target_path=target_source,
            )

    def _normalized_artifacts(
        self,
        artifacts: Sequence[str | ArtifactConfig] | None,
        convention_source: str,
    ) -> list[ArtifactConfig]:
        return with_convention_entry(
            [*self.artifacts, *(artifacts or [])],
            convention_source=convention_source,
        )

    async def _download_artifact(
        self,
        *,
        source_env: BaseEnvironment,
        artifacts_dir: Path,
        artifact: ArtifactConfig,
        convention_source: str,
    ) -> ArtifactManifestEntry:
        source = artifact.source
        target = self._host_path(
            artifacts_dir,
            artifact,
            convention_source=convention_source,
        )
        manifest_destination = self._manifest_destination(artifacts_dir, target)

        if (
            is_convention_entry(artifact, convention_source)
            and not artifact.destination
            and source_env.capabilities.mounted
            and not artifact.exclude
        ):
            return self._record_mounted_artifacts_dir(
                source=source,
                target=target,
                manifest_destination=manifest_destination,
                service=artifact.service,
            )

        try:
            is_dir = await source_env.service_is_dir(
                source, service=artifact.service, user="root"
            )
        except Exception:
            is_dir = not Path(source).suffix

        try:
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
                if artifact.exclude:
                    await source_env.download_dir_with_exclusions(
                        source_dir=source,
                        target_dir=target,
                        exclude=artifact.exclude,
                        service=artifact.service,
                    )
                else:
                    await source_env.service_download_dir(
                        source_dir=source,
                        target_dir=target,
                        service=artifact.service,
                    )
                artifact_type = "directory"
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                await source_env.service_download_file(
                    source_path=source,
                    target_path=target,
                    service=artifact.service,
                )
                artifact_type = "file"

            return ArtifactManifestEntry(
                source=source,
                destination=manifest_destination,
                type=artifact_type,
                status="ok",
                service=artifact.service,
            )
        except Exception:
            self.logger.debug(
                f"Failed to download artifact '{source}' from service "
                f"'{effective_artifact_service(artifact)}' (best-effort)",
                exc_info=True,
            )
            return ArtifactManifestEntry(
                source=source,
                destination=manifest_destination,
                type="directory" if is_dir else "file",
                status="failed",
                service=artifact.service,
            )

    def _record_mounted_artifacts_dir(
        self,
        *,
        source: str,
        target: Path,
        manifest_destination: str,
        service: str | None,
    ) -> ArtifactManifestEntry:
        has_contents = target.exists() and any(target.iterdir())
        return ArtifactManifestEntry(
            source=source,
            destination=manifest_destination,
            type="directory",
            status="ok" if has_contents else "empty",
            service=service,
        )

    def _host_path(
        self,
        artifacts_dir: Path,
        artifact: ArtifactConfig,
        *,
        convention_source: str,
    ) -> Path:
        if is_convention_entry(artifact, convention_source):
            destination = artifact.destination or artifact.source
            if (
                self._is_environment_artifacts_dir(destination, convention_source)
                or destination == "."
            ):
                return artifacts_dir

        destination = artifact.destination or PurePosixPath(artifact.source).name
        return artifacts_dir / self._relative_host_destination(destination)

    @staticmethod
    def _relative_host_destination(destination: str) -> Path:
        destination_path = PurePosixPath(destination)
        parts = [part for part in destination_path.parts if part not in ("", "/")]
        return Path(*parts) if parts else Path(".")

    def _is_environment_artifacts_dir(
        self,
        source: str,
        convention_source: str,
    ) -> bool:
        return source.rstrip("/") == convention_source.rstrip("/")

    def _upload_target_source(
        self,
        artifact: ArtifactConfig,
        *,
        source_convention: str,
        target_convention: str,
    ) -> str:
        if is_convention_entry(artifact, source_convention):
            return target_convention
        return artifact.source

    @staticmethod
    def _environment_path_str(path: EnvironmentPath) -> str:
        if isinstance(path, PurePath):
            return path.as_posix()
        return path

    def _manifest_destination(self, artifacts_dir: Path, target: Path) -> str:
        if target == artifacts_dir:
            return "artifacts"
        return f"artifacts/{target.relative_to(artifacts_dir).as_posix()}"

    def _write_manifest(
        self,
        artifacts_dir: Path,
        manifest: ArtifactManifest,
    ) -> None:
        if not manifest.entries:
            return

        try:
            (artifacts_dir / "manifest.json").write_text(
                json.dumps(manifest.to_json_data(), indent=2)
            )
        except Exception:
            self.logger.debug("Failed to write artifacts manifest", exc_info=True)
