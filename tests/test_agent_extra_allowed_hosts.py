"""Run-level ``agent.extra_allowed_hosts`` (Harbor >= 0.21) must reach the proxy.

Harbor lets a run widen the agent-phase allowlist so an agent can reach its
model provider inside a restricted-network task. Pier accepts the same field,
normalizes Harbor's spelling onto ``NetworkAllowlist`` form once at the config
boundary, and unions it into the agent's derived inference hosts.
"""

import pytest
from pydantic import ValidationError

from pier.models.agent.network import NetworkAllowlist
from pier.models.trial.config import AgentConfig
from pier.trial.execution import TrialExecution


class _StubAgent:
    def __init__(self, *domains: str) -> None:
        self._domains = list(domains)

    def network_allowlist(self) -> NetworkAllowlist:
        return NetworkAllowlist(domains=self._domains)


def test_harbor_wildcard_becomes_pier_suffix():
    config = AgentConfig(extra_allowed_hosts=["*.Example.COM.", " API.Example.com "])

    assert config.extra_allowed_hosts == [".example.com", "api.example.com"]


def test_duplicate_entries_collapse():
    config = AgentConfig(extra_allowed_hosts=["*.example.com", ".example.com"])

    assert config.extra_allowed_hosts == [".example.com"]


@pytest.mark.parametrize("host", ["10.0.0.1", "10.0.0.0/8", "2001:db8::1", "fd00::/8"])
def test_ip_literals_and_cidrs_are_rejected(host: str):
    with pytest.raises(ValidationError, match="not supported by pier yet"):
        AgentConfig(extra_allowed_hosts=[host])


def test_inner_wildcard_is_rejected():
    with pytest.raises(ValidationError, match="exact domain or leading-dot suffix"):
        AgentConfig(extra_allowed_hosts=["api.*.example.com"])


def test_extra_hosts_union_with_agent_derived_allowlist():
    allowlist = TrialExecution._resolve_network_allowlist(
        agent=_StubAgent("api.anthropic.com"),
        agent_config=AgentConfig(
            extra_allowed_hosts=["*.example.com", "api.anthropic.com"]
        ),
        allow_internet=False,
    )

    assert allowlist.domains == [".example.com", "api.anthropic.com"]


def test_agent_allowlist_passes_through_without_extra_hosts():
    allowlist = TrialExecution._resolve_network_allowlist(
        agent=_StubAgent("api.anthropic.com"),
        agent_config=AgentConfig(),
        allow_internet=False,
    )

    assert allowlist.domains == ["api.anthropic.com"]


def test_public_network_policy_warns_and_ignores_extra_hosts():
    with pytest.warns(UserWarning, match="effective network policy is public"):
        allowlist = TrialExecution._resolve_network_allowlist(
            agent=_StubAgent("api.anthropic.com"),
            agent_config=AgentConfig(extra_allowed_hosts=["api.example.com"]),
            allow_internet=True,
        )

    assert allowlist.domains == ["api.anthropic.com"]
