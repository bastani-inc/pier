"""Harbor 1.4 task network schema compatibility."""

import warnings

import pytest
from pydantic import ValidationError

from pier.models.task.config import NetworkMode, TaskConfig


def test_environment_defaults_to_public() -> None:
    config = TaskConfig.model_validate_toml("")
    assert config.environment.network_mode is NetworkMode.PUBLIC
    assert config.environment.allow_internet is None


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [(False, NetworkMode.NO_NETWORK), (True, NetworkMode.PUBLIC)],
)
def test_legacy_allow_internet_migrates_without_serializing(
    legacy: bool, expected: NetworkMode
) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config = TaskConfig.model_validate_toml(
            f"[environment]\nallow_internet = {str(legacy).lower()}\n"
        )
    assert config.environment.network_mode is expected
    assert config.environment.allow_internet is None
    assert "allow_internet" not in config.model_dump_toml()
    assert any(issubclass(item.category, DeprecationWarning) for item in caught)


def test_phase_overrides_do_not_mutate_environment_baseline() -> None:
    config = TaskConfig.model_validate_toml(
        """
[environment]
network_mode = "no-network"
[agent]
network_mode = "public"
[verifier]
network_mode = "allowlist"
allowed_hosts = ["pypi.org"]
"""
    )
    assert config.environment.network_mode is NetworkMode.NO_NETWORK
    assert config.agent.network_mode is NetworkMode.PUBLIC
    assert config.verifier.network_mode is NetworkMode.ALLOWLIST


def test_separate_verifier_environment_has_its_own_public_default() -> None:
    config = TaskConfig.model_validate_toml(
        """
[environment]
network_mode = "no-network"
[verifier.environment]
cpus = 1
"""
    )
    assert config.environment.network_mode is NetworkMode.NO_NETWORK
    assert config.verifier.environment is not None
    assert config.verifier.environment.network_mode is NetworkMode.PUBLIC


@pytest.mark.parametrize(
    "section",
    ["agent", "verifier", "environment"],
)
def test_allowed_hosts_require_allowlist_mode(section: str) -> None:
    with pytest.raises(ValidationError, match="only valid"):
        TaskConfig.model_validate_toml(
            f'[{section}]\nnetwork_mode = "public"\nallowed_hosts = ["pypi.org"]\n'
        )


def test_allowed_hosts_without_mode_is_rejected() -> None:
    with pytest.raises(ValidationError, match="only valid"):
        TaskConfig.model_validate_toml('[agent]\nallowed_hosts = ["pypi.org"]\n')


def test_allowlist_may_be_empty() -> None:
    config = TaskConfig.model_validate_toml(
        '[environment]\nnetwork_mode = "allowlist"\n'
    )
    assert config.environment.allowed_hosts is None
    assert config.environment.resolve_baseline().allowed_hosts == []


def test_allowed_hosts_match_harbor_normalization() -> None:
    config = TaskConfig.model_validate_toml(
        """
[agent]
network_mode = "allowlist"
allowed_hosts = ["*.IANA.org.", "2001:0DB8::1", "192.0.2.0/24"]
"""
    )
    assert config.agent.allowed_hosts == [
        "*.iana.org",
        "2001:db8::1",
        "192.0.2.0/24",
    ]


@pytest.mark.parametrize(
    "host",
    [
        "https://pypi.org/simple",
        "pypi.org:443",
        "fe80::1%eth0",
        "192.0.2.1/24",
        "*",
        "pypi.*",
        "api.*.pypi.org",
        "*pypi.org",
        "*.1.1.1.1",
        ".example.com",
        "bad host.example",
    ],
)
def test_invalid_allowed_hosts_are_rejected(host: str) -> None:
    with pytest.raises(ValidationError):
        TaskConfig.model_validate_toml(
            f'[agent]\nnetwork_mode = "allowlist"\nallowed_hosts = ["{host}"]\n'
        )


def test_valid_dynamic_multistep_policy_parses_without_rewriting() -> None:
    config = TaskConfig.model_validate_toml(
        """
[environment]
network_mode = "public"
[[steps]]
name = "offline"
[steps.agent]
network_mode = "no-network"
[steps.verifier]
network_mode = "public"
"""
    )
    step = config.steps[0]
    assert config.environment.network_mode is NetworkMode.PUBLIC
    assert step.agent.network_mode is NetworkMode.NO_NETWORK
    assert step.verifier.network_mode is NetworkMode.PUBLIC


def test_network_policy_round_trip() -> None:
    config = TaskConfig.model_validate_toml(
        """
[environment]
network_mode = "allowlist"
allowed_hosts = ["example.com", "*.example.com"]
"""
    )
    reparsed = TaskConfig.model_validate_toml(config.model_dump_toml())
    assert reparsed.model_dump(mode="json") == config.model_dump(mode="json")
