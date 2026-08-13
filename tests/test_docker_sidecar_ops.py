"""Per-service (compose sidecar) operations on DockerEnvironment."""

import asyncio
import functools
from pathlib import Path

import pytest

from pier.environments.base import ExecResult
from pier.environments.docker.docker import DockerEnvironment
from pier.models.task.config import EnvironmentConfig, TaskOS
from pier.models.trial.paths import TrialPaths


def run_async(fn):
    """Drive an async test with asyncio.run (pier has no pytest-asyncio)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def _make_environment(tmp_path: Path, os: TaskOS = TaskOS.LINUX) -> DockerEnvironment:
    (tmp_path / "Dockerfile").write_text("FROM scratch")
    trial_paths = TrialPaths(tmp_path / "trial")
    trial_paths.mkdir()
    environment = DockerEnvironment(
        environment_dir=tmp_path,
        environment_name="test",
        session_id="session",
        trial_paths=trial_paths,
        task_env_config=EnvironmentConfig(os=os),
    )
    environment.compose_commands = []

    async def fake_compose(command, check=True, timeout_sec=None):
        environment.compose_commands.append(command)
        return ExecResult(stdout="", stderr="", return_code=0)

    environment._run_docker_compose_command = fake_compose
    return environment


@run_async
async def test_sidecar_exec_targets_the_service_with_sh(tmp_path: Path) -> None:
    """Sidecar images may lack bash, so sidecar execs are wrapped with sh."""
    environment = _make_environment(tmp_path)

    await environment.service_exec("ls /var", service="db")

    assert environment.compose_commands == [["exec", "db", "sh", "-c", "ls /var"]]


@run_async
async def test_main_exec_keeps_the_platform_shell(tmp_path: Path) -> None:
    environment = _make_environment(tmp_path)

    await environment.exec("echo hi")

    assert environment.compose_commands == [["exec", "main", "bash", "-c", "echo hi"]]


@run_async
async def test_sidecar_download_copies_from_the_service(tmp_path: Path) -> None:
    environment = _make_environment(tmp_path)

    await environment.service_download_file(
        "/var/db/dump.sql", tmp_path / "dump.sql", service="db"
    )
    await environment.service_download_dir("/var/lib/db", tmp_path / "db", service="db")

    copies = [command for command in environment.compose_commands if command[0] == "cp"]
    assert copies == [
        ["cp", "db:/var/db/dump.sql", str(tmp_path / "dump.sql")],
        ["cp", "db:/var/lib/db/.", str(tmp_path / "db")],
    ]


@run_async
async def test_main_service_download_still_targets_main(tmp_path: Path) -> None:
    environment = _make_environment(tmp_path)

    await environment.service_download_file(
        "/logs/artifacts/x.json", tmp_path / "x.json", service=None
    )

    copies = [command for command in environment.compose_commands if command[0] == "cp"]
    assert copies == [["cp", "main:/logs/artifacts/x.json", str(tmp_path / "x.json")]]


@run_async
async def test_windows_containers_reject_sidecar_operations(tmp_path: Path) -> None:
    """Windows transfers go through a single named container, not compose."""
    environment = _make_environment(tmp_path, os=TaskOS.WINDOWS)

    with pytest.raises(NotImplementedError, match="Windows"):
        await environment.service_exec("dir", service="db")


def test_windows_does_not_advertise_sidecar_or_proxy_capabilities(
    tmp_path: Path,
) -> None:
    environment = _make_environment(tmp_path, os=TaskOS.WINDOWS)

    assert environment.capabilities.docker_compose is False
    assert environment.capabilities.filtered_egress is False
    assert environment.capabilities.phase_scoped_egress is False


def test_compose_does_not_advertise_single_container_proxy_capabilities(
    tmp_path: Path,
) -> None:
    (tmp_path / "docker-compose.yaml").write_text("services: {}\n")
    environment = _make_environment(tmp_path)

    assert environment.capabilities.docker_compose is True
    assert environment.capabilities.filtered_egress is False
    assert environment.capabilities.phase_scoped_egress is False
    assert environment.capabilities.preinstall_agents is False
