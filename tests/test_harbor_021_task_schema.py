"""Harbor 0.21 task-schema compatibility (SHP-1261).

Fields Harbor added after 0.5.0 that pier's extra="ignore" models used to
drop silently: [task].version, the 'http' MCP transport alias, unset
resource defaults, [environment.tpu], hardened artifact entries, and the
schema_version support gate.
"""

import logging
import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError

from pier.environments.base import (
    DEFAULT_GPUS,
    DEFAULT_STORAGE_MB,
    BaseEnvironment,
    ExecResult,
)
from pier.environments.capabilities import EnvironmentCapabilities
from pier.models.environment_type import EnvironmentType
from pier.models.task.config import (
    SUPPORTED_SCHEMA_VERSION,
    ArtifactConfig,
    EnvironmentConfig,
    MCPServerConfig,
    TaskConfig,
    TpuSpec,
)
from pier.models.trial.paths import TrialPaths


class _StubEnvironment(BaseEnvironment):
    @staticmethod
    def type() -> EnvironmentType:
        return EnvironmentType.DOCKER

    @property
    def capabilities(self) -> EnvironmentCapabilities:
        return EnvironmentCapabilities()

    def _validate_definition(self):
        pass

    async def start(self, force_build: bool) -> None:
        pass

    async def stop(self, delete: bool):
        pass

    async def upload_file(self, source_path, target_path):
        pass

    async def upload_dir(self, source_dir, target_dir):
        pass

    async def download_file(self, source_path, target_path):
        pass

    async def download_dir(self, source_dir, target_dir):
        pass

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        return ExecResult(return_code=0)


def _make_environment(
    tmp_path: Path, task_env_config: EnvironmentConfig
) -> _StubEnvironment:
    trial_paths = TrialPaths(tmp_path / "trial")
    trial_paths.mkdir()
    return _StubEnvironment(
        environment_dir=tmp_path,
        environment_name="test",
        session_id="session",
        trial_paths=trial_paths,
        task_env_config=task_env_config,
        logger=logging.getLogger(__name__),
    )


# --- [task].version ---------------------------------------------------------


def test_task_version_parses_and_round_trips():
    cfg = TaskConfig.model_validate_toml(
        '[task]\nname = "pier/example"\nversion = "1.2.3"\n'
    )
    assert cfg.task is not None
    assert cfg.task.version == "1.2.3"

    reparsed = TaskConfig.model_validate_toml(cfg.model_dump_toml())
    assert reparsed.task is not None
    assert reparsed.task.version == "1.2.3"


def test_task_version_defaults_to_none_and_rejects_empty():
    cfg = TaskConfig.model_validate_toml('[task]\nname = "pier/example"\n')
    assert cfg.task is not None
    assert cfg.task.version is None

    with pytest.raises(ValidationError):
        TaskConfig.model_validate_toml('[task]\nname = "pier/example"\nversion = ""\n')


# --- MCP transport normalization --------------------------------------------


def test_mcp_http_transport_normalizes_to_streamable_http():
    server = MCPServerConfig(name="srv", transport="http", url="http://mcp.local")
    assert server.transport == "streamable-http"


@pytest.mark.parametrize("transport", ["sse", "streamable-http"])
def test_mcp_canonical_url_transports_accepted(transport: str):
    server = MCPServerConfig(name="srv", transport=transport, url="http://mcp.local")
    assert server.transport == transport


def test_mcp_stdio_transport_accepted():
    server = MCPServerConfig(name="srv", transport="stdio", command="mcp-server")
    assert server.transport == "stdio"


def test_mcp_unknown_transport_rejected():
    with pytest.raises(ValidationError):
        MCPServerConfig(name="srv", transport="websocket", url="http://mcp.local")


def test_mcp_http_transport_normalized_from_toml():
    cfg = TaskConfig.model_validate_toml(
        """
[environment]
[[environment.mcp_servers]]
name = "srv"
transport = "http"
url = "http://mcp.local"
"""
    )
    assert cfg.environment.mcp_servers[0].transport == "streamable-http"


# --- Resource defaults ------------------------------------------------------


def test_resource_defaults_are_unset():
    env = EnvironmentConfig()
    assert env.storage_mb is None
    assert env.gpus is None


def test_unset_resources_fall_back_to_legacy_defaults(tmp_path: Path):
    environment = _make_environment(tmp_path, EnvironmentConfig())
    assert environment._effective_storage_mb == DEFAULT_STORAGE_MB == 10240
    assert environment._effective_gpus == DEFAULT_GPUS == 0


def test_explicit_resources_pass_through(tmp_path: Path):
    environment = _make_environment(
        tmp_path, EnvironmentConfig(storage_mb=2048, gpus=0)
    )
    assert environment._effective_storage_mb == 2048
    assert environment._effective_gpus == 0


def test_unset_resources_not_serialized():
    dumped = TaskConfig().model_dump_toml()
    assert "storage_mb" not in dumped
    assert "gpus" not in dumped


# --- TPU --------------------------------------------------------------------


def test_tpu_spec_parses_and_derives_chip_count():
    cfg = TaskConfig.model_validate_toml(
        '[environment.tpu]\ntype = "v6e"\ntopology = "2x4"\n'
    )
    assert cfg.environment.tpu is not None
    assert cfg.environment.tpu.type == "v6e"
    assert cfg.environment.tpu.topology == "2x4"
    assert cfg.environment.tpu.chip_count == 8


def test_tpu_three_axis_topology_chip_count():
    assert TpuSpec(type="v4", topology="2x2x1").chip_count == 4


@pytest.mark.parametrize("topology", ["", "2x", "x4", "2x0", "4", "2x-2", "axb"])
def test_tpu_invalid_topology_rejected(topology: str):
    with pytest.raises(ValidationError):
        TpuSpec(type="v6e", topology=topology)


def test_tpu_capability_defaults_to_false():
    assert EnvironmentCapabilities().tpus is False


def test_tpu_request_without_capability_fails_loudly(tmp_path: Path):
    config = EnvironmentConfig(tpu=TpuSpec(type="v6e", topology="2x4"))
    with pytest.raises(ValueError, match="TPU"):
        _make_environment(tmp_path, config)


# --- Artifact hardening -----------------------------------------------------


def test_artifact_source_traversal_rejected():
    with pytest.raises(ValidationError):
        ArtifactConfig(source="/logs/../etc/passwd")


def test_artifact_destination_traversal_rejected():
    with pytest.raises(ValidationError):
        ArtifactConfig(source="/tmp/out.txt", destination="../escape.txt")


def test_artifact_destination_absolute_rejected():
    with pytest.raises(ValidationError):
        ArtifactConfig(source="/tmp/out.txt", destination="/etc/out.txt")


def test_artifact_destination_backslash_rejected():
    with pytest.raises(ValidationError):
        ArtifactConfig(source="/tmp/out.txt", destination="dir\\out.txt")


def test_artifact_destination_manifest_json_rejected():
    with pytest.raises(ValidationError):
        ArtifactConfig(source="/tmp/out.txt", destination="manifest.json")


def test_artifact_sidecar_service_requires_absolute_source():
    with pytest.raises(ValidationError):
        ArtifactConfig(source="dump.sql", service="db")


def test_artifact_sidecar_service_with_absolute_source_accepted():
    artifact = ArtifactConfig(source="/var/db/dump.sql", service="db")
    assert artifact.service == "db"


def test_artifact_main_service_relative_source_accepted():
    artifact = ArtifactConfig(source="out.txt", service="main")
    assert artifact.service == "main"


def test_artifact_invalid_service_name_rejected():
    with pytest.raises(ValidationError):
        ArtifactConfig(source="/tmp/out.txt", service="-bad")


def test_task_level_overlapping_artifact_sources_warn():
    with pytest.warns(UserWarning, match="Artifact sources overlap"):
        TaskConfig.model_validate_toml(
            'artifacts = ["/data/output", "/data/output/model.patch"]\n'
        )


def test_task_level_overlapping_artifact_destinations_warn():
    with pytest.warns(UserWarning, match="Artifact destinations overlap"):
        TaskConfig.model_validate_toml(
            """
[[artifacts]]
source = "/tmp/a"
destination = "out"

[[artifacts]]
source = "/var/b"
destination = "out/b.txt"
"""
        )


def test_step_level_sidecar_relative_source_rejected():
    with pytest.raises(ValidationError):
        TaskConfig.model_validate_toml(
            """
[[steps]]
name = "one"

[[steps.artifacts]]
source = "relative/dump.sql"
service = "db"
"""
        )


def test_disjoint_artifact_sources_load_without_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = TaskConfig.model_validate_toml(
            'artifacts = ["/data/output", "/var/log/other.txt"]\n'
        )
    assert len(cfg.artifacts) == 2


# --- schema_version gate ----------------------------------------------------


def test_schema_version_default_matches_harbor_021():
    assert TaskConfig().schema_version == SUPPORTED_SCHEMA_VERSION == "1.4"


@pytest.mark.parametrize("version", ["1.5", "2.0", "1.4.1"])
def test_newer_schema_version_warns(version: str):
    with pytest.warns(UserWarning, match="newer"):
        TaskConfig.model_validate_toml(f'schema_version = "{version}"\n')


@pytest.mark.parametrize("version", ["1.4", "1.2", "1.0"])
def test_supported_and_older_schema_versions_load_silently(version: str):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = TaskConfig.model_validate_toml(f'schema_version = "{version}"\n')
    assert cfg.schema_version == version


def test_non_numeric_schema_version_tolerated():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = TaskConfig.model_validate_toml('schema_version = "experimental"\n')
    assert cfg.schema_version == "experimental"
