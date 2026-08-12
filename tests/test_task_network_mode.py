"""network_mode (Harbor >= 0.18 schema) must resolve onto allow_internet.

Regression tests for the air-gap escape where pier silently ignored
``network_mode = "no-network"`` and defaulted to allow_internet=True.
'allowlist' resolves the same closed way and additionally collects the task's
``allowed_hosts`` for the egress proxy.
"""

import pytest
from pydantic import ValidationError

from pier.models.task.config import NetworkMode, TaskConfig

V11_STYLE = """
schema_version = "1.3"
[verifier]
network_mode = "no-network"
environment_mode = "separate"
timeout_sec = 1800.0
[verifier.environment]
build_timeout_sec = 1800.0
[agent]
network_mode = "no-network"
timeout_sec = 5400.0
[environment]
docker_image = "example/image:tag"
"""


def test_v11_no_network_resolves_to_no_internet():
    cfg = TaskConfig.model_validate_toml(V11_STYLE)
    assert cfg.environment.allow_internet is False
    assert cfg.environment.network_mode is NetworkMode.NO_NETWORK
    assert cfg.verifier.environment is not None
    assert cfg.verifier.environment.allow_internet is False


def test_public_mode_allows_internet():
    cfg = TaskConfig.model_validate_toml("[environment]\nnetwork_mode = \"public\"\n")
    assert cfg.environment.allow_internet is True


def test_task_wide_mode_applies_to_explicit_verifier_environment():
    cfg = TaskConfig.model_validate_toml(
        """
[verifier]
environment_mode = "separate"
[verifier.environment]
docker_image = "example/verifier:tag"
[environment]
network_mode = "no-network"
docker_image = "example/image:tag"
"""
    )
    assert cfg.environment.allow_internet is False
    assert cfg.verifier.environment.allow_internet is False


def test_step_verifier_inherits_task_wide_mode():
    cfg = TaskConfig.model_validate_toml(
        """
[environment]
network_mode = "no-network"
docker_image = "example/image:tag"
[[steps]]
name = "one"
[steps.verifier]
environment_mode = "separate"
[steps.verifier.environment]
docker_image = "example/verifier:tag"
"""
    )
    assert cfg.steps[0].verifier.environment.allow_internet is False


def test_agent_override_beats_environment_scope():
    cfg = TaskConfig.model_validate_toml(
        """
[agent]
network_mode = "no-network"
[environment]
network_mode = "public"
"""
    )
    assert cfg.environment.allow_internet is False


def test_shared_verifier_conflicting_override_rejected():
    with pytest.raises(ValidationError):
        TaskConfig.model_validate_toml(
            """
[agent]
network_mode = "no-network"
[verifier]
network_mode = "public"
[environment]
docker_image = "example/image:tag"
"""
        )


def test_separate_verifier_override_materializes_environment():
    cfg = TaskConfig.model_validate_toml(
        """
[verifier]
network_mode = "no-network"
environment_mode = "separate"
[environment]
network_mode = "public"
docker_image = "example/image:tag"
"""
    )
    assert cfg.environment.allow_internet is True
    assert cfg.verifier.environment is not None
    assert cfg.verifier.environment.allow_internet is False


def test_legacy_allow_internet_still_honored():
    cfg = TaskConfig.model_validate_toml("[environment]\nallow_internet = false\n")
    assert cfg.environment.allow_internet is False
    cfg = TaskConfig.model_validate_toml("[environment]\n")
    assert cfg.environment.allow_internet is True  # unchanged legacy default


ALLOWLIST_TASK = """
[agent]
network_mode = "allowlist"
allowed_hosts = ["*.Example.com"]
[verifier]
environment_mode = "separate"
allowed_hosts = ["pypi.org"]
[verifier.environment]
docker_image = "example/verifier:tag"
[environment]
network_mode = "allowlist"
allowed_hosts = ["files.pythonhosted.org", "*.example.com"]
docker_image = "example/image:tag"
[[steps]]
name = "one"
[steps.agent]
allowed_hosts = ["step.example.org"]
[steps.verifier]
environment_mode = "separate"
[steps.verifier.environment]
allowed_hosts = ["crates.io"]
"""


def test_allowlist_mode_resolves_to_no_internet():
    cfg = TaskConfig.model_validate_toml(ALLOWLIST_TASK)
    assert cfg.environment.network_mode is NetworkMode.ALLOWLIST
    assert cfg.environment.allow_internet is False
    assert cfg.verifier.environment.allow_internet is False
    assert cfg.steps[0].verifier.environment.allow_internet is False


def test_allowlist_agent_override_beats_public_environment():
    cfg = TaskConfig.model_validate_toml(
        "[agent]\nnetwork_mode = \"allowlist\"\n"
        "[environment]\nnetwork_mode = \"public\"\n"
    )
    assert cfg.environment.allow_internet is False


def test_declared_allowed_hosts_unions_every_scope():
    cfg = TaskConfig.model_validate_toml(ALLOWLIST_TASK)
    assert cfg.declared_allowed_hosts() == [
        ".example.com",
        "crates.io",
        "files.pythonhosted.org",
        "pypi.org",
        "step.example.org",
    ]


def test_allowed_hosts_normalized_at_parse():
    cfg = TaskConfig.model_validate_toml(
        "[environment]\nallowed_hosts = [\"*.Example.COM.\", \" API.Example.com \"]\n"
    )
    assert cfg.environment.allowed_hosts == [".example.com", "api.example.com"]


@pytest.mark.parametrize("host", ["10.0.0.1", "10.0.0.0/8", "2001:db8::1"])
def test_allowed_hosts_ip_and_cidr_still_rejected(host: str):
    with pytest.raises(ValidationError, match="not supported by pier yet"):
        TaskConfig.model_validate_toml(f"[environment]\nallowed_hosts = [\"{host}\"]\n")


def test_shared_verifier_conflicting_allowlist_override_rejected():
    with pytest.raises(ValidationError):
        TaskConfig.model_validate_toml(
            """
[agent]
network_mode = "allowlist"
[verifier]
network_mode = "public"
[environment]
docker_image = "example/image:tag"
"""
        )


def test_unknown_network_mode_rejected():
    with pytest.raises(ValidationError):
        TaskConfig.model_validate_toml("[agent]\nnetwork_mode = \"offline\"\n")
