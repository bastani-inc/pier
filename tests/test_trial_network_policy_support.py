from types import SimpleNamespace

import pytest

from pier.models.task.config import TaskConfig
from pier.trial.trial import Trial


def _validate(toml: str, *, phase_scoped_egress: bool = True) -> None:
    trial = Trial.__new__(Trial)
    trial._task = SimpleNamespace(config=TaskConfig.model_validate_toml(toml))
    trial._environment = SimpleNamespace(
        capabilities=SimpleNamespace(phase_scoped_egress=phase_scoped_egress),
        type=lambda: "test",
    )
    trial._validate_network_policy_support()


def test_rejects_dynamic_agent_policy_that_cannot_be_enforced() -> None:
    with pytest.raises(ValueError, match="agent phase network policy"):
        _validate(
            """
[environment]
network_mode = "public"

[agent]
network_mode = "no-network"
"""
        )


def test_accepts_allowlist_phase_over_closed_baseline() -> None:
    _validate(
        """
[environment]
network_mode = "no-network"

[agent]
network_mode = "allowlist"
allowed_hosts = ["example.com"]
"""
    )


def test_rejects_allowlist_phase_when_provider_applies_it_to_whole_sandbox() -> None:
    with pytest.raises(ValueError, match="agent phase network policy"):
        _validate(
            """
[environment]
network_mode = "no-network"

[agent]
network_mode = "allowlist"
allowed_hosts = ["example.com"]
""",
            phase_scoped_egress=False,
        )


def test_rejects_different_agent_policies_across_steps() -> None:
    with pytest.raises(ValueError, match="different agent network policies"):
        _validate(
            """
[environment]
network_mode = "no-network"

[[steps]]
name = "first"
[steps.agent]
network_mode = "allowlist"
allowed_hosts = ["one.example"]

[[steps]]
name = "second"
[steps.agent]
network_mode = "allowlist"
allowed_hosts = ["two.example"]
"""
        )
