from types import SimpleNamespace

import pytest

from pier.environments.daytona import DaytonaEnvironment
from pier.environments.docker.docker import DockerEnvironment


@pytest.mark.parametrize("environment_type", [DockerEnvironment, DaytonaEnvironment])
def test_prebuilt_image_needs_no_environment_definition(
    environment_type, tmp_path
) -> None:
    environment = environment_type.__new__(environment_type)
    environment.environment_dir = tmp_path
    environment.task_env_config = SimpleNamespace(docker_image="example/image:tag")
    environment._compose_mode = False

    environment._validate_definition()


@pytest.mark.parametrize("environment_type", [DockerEnvironment, DaytonaEnvironment])
def test_environment_without_image_or_definition_is_rejected(
    environment_type, tmp_path
) -> None:
    environment = environment_type.__new__(environment_type)
    environment.environment_dir = tmp_path
    environment.task_env_config = SimpleNamespace(docker_image=None)
    environment._compose_mode = False

    with pytest.raises(FileNotFoundError):
        environment._validate_definition()
