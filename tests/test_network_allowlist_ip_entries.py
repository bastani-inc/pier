"""IP address and CIDR allowlist entries (Harbor-compatible spelling).

Harbor accepts IPv4/IPv6 literals and strict CIDR ranges in ``allowed_hosts``
next to hostnames. Pier classifies each entry once at the config boundary, keeps
hostnames in ``NetworkAllowlist.domains`` and IP entries in ``ip_entries``, and
enforces both halves in the egress proxy.
"""

import pytest

from pier.environments.agent_setup import proxy_policy_env, squid_bootstrap_command
from pier.models.agent.network import (
    NetworkAllowlist,
    classify_allowlist_entry,
    normalize_allowed_hosts,
)
from pier.models.task.config import TaskConfig
from pier.models.trial.config import AgentConfig
from pier.trial.execution import TrialExecution


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("192.0.2.1", "192.0.2.1"),
        (" 203.0.113.7 ", "203.0.113.7"),
        ("2001:0DB8:0000::0001", "2001:db8::1"),
        ("::1", "::1"),
        ("192.0.2.0/24", "192.0.2.0/24"),
        ("10.0.0.0/8", "10.0.0.0/8"),
        ("2001:0DB8::/32", "2001:db8::/32"),
        ("192.0.2.1/32", "192.0.2.1/32"),
    ],
)
def test_ip_entries_are_canonicalized(entry: str, expected: str):
    assert classify_allowlist_entry(entry) == (True, expected)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("API.Example.com.", "api.example.com"),
        ("*.Example.com", ".example.com"),
        (".example.com", ".example.com"),
    ],
)
def test_hostname_entries_keep_the_existing_rules(entry: str, expected: str):
    assert classify_allowlist_entry(entry) == (False, expected)


@pytest.mark.parametrize(
    "entry",
    [
        "192.0.2.1:443",
        "example.com:8080",
        "https://192.0.2.1",
        "https://example.com/path",
        "192.0.2.1/24",  # not a strict network address
        "2001:db8::1/32",  # not a strict network address
        "[2001:db8::1]",
        "[2001:db8::1]:443",
        "*.192.0.2.1",
        ".192.0.2.1",
        "fe80::1%eth0",
        "192.0.2.0/33",
        "",
        "   ",
    ],
)
def test_malformed_entries_are_rejected(entry: str):
    with pytest.raises(ValueError):
        classify_allowlist_entry(entry)


def test_from_entries_splits_hostnames_from_ip_entries():
    allowlist = NetworkAllowlist.from_entries(
        ["*.example.com", "10.0.0.0/8", "api.example.com", "2001:0DB8::1", "10.0.0.0/8"]
    )

    assert allowlist.domains == [".example.com", "api.example.com"]
    assert allowlist.ip_entries == ["10.0.0.0/8", "2001:db8::1"]
    assert allowlist.is_empty is False
    assert NetworkAllowlist().is_empty is True


def test_ip_entries_field_rejects_hostnames():
    with pytest.raises(ValueError, match="IP addresses or CIDR ranges"):
        NetworkAllowlist(ip_entries=["api.example.com"])


def test_normalize_allowed_hosts_returns_hostnames_then_ip_entries():
    assert normalize_allowed_hosts(["10.0.0.0/8", "*.example.com"]) == [
        ".example.com",
        "10.0.0.0/8",
    ]


def test_squid_bootstrap_emits_ip_acl_only_when_ip_entries_exist():
    domains_only = squid_bootstrap_command(NetworkAllowlist(domains=["example.com"]))

    assert 'acl allowed_domains dstdomain "/tmp/allowed_domains.txt"' in domains_only
    assert "http_access allow authenticated allowed_domains" in domains_only
    assert "acl allowed_ips" not in domains_only
    assert "http_access allow authenticated allowed_ips" not in domains_only


def test_squid_bootstrap_emits_both_acls_for_a_mixed_allowlist():
    script = squid_bootstrap_command(
        NetworkAllowlist(domains=["example.com"], ip_entries=["10.0.0.0/8"])
    )

    assert 'acl allowed_ips dst "/tmp/allowed_ips.txt"' in script
    assert "http_access allow authenticated allowed_ips" in script
    assert "allowed_domains" in script


def test_squid_bootstrap_skips_the_domain_acl_for_an_ip_only_allowlist():
    script = squid_bootstrap_command(NetworkAllowlist(ip_entries=["10.0.0.0/8"]))

    assert "allowed_domains.txt" in script  # the file is still written
    assert "acl allowed_domains" not in script
    assert "http_access allow authenticated allowed_domains" not in script


def test_proxy_policy_env_carries_both_allowlist_halves():
    env = proxy_policy_env(
        NetworkAllowlist(domains=["example.com"], ip_entries=["10.0.0.0/8", "::1"]),
        "secret",
    )

    assert env["ALLOWLIST_DOMAINS"] == "example.com"
    assert env["ALLOWLIST_IPS"] == "10.0.0.0/8,::1"


class _StubAgent:
    def network_allowlist(self) -> NetworkAllowlist:
        return NetworkAllowlist(domains=["api.anthropic.com"])


def test_task_toml_ip_entries_reach_the_run_allowlist():
    task_config = TaskConfig.model_validate_toml(
        "[environment]\n"
        'network_mode = "allowlist"\n'
        'allowed_hosts = ["pypi.org", "10.0.0.0/8", "2001:0DB8::1"]\n'
    )

    allowlist = TrialExecution._resolve_network_allowlist(
        agent=_StubAgent(),
        agent_config=AgentConfig(extra_allowed_hosts=["192.0.2.0/24"]),
        allow_internet=task_config.environment.allow_internet,
        task_allowed_hosts=task_config.agent_allowed_hosts(),
    )

    assert allowlist.domains == ["api.anthropic.com", "pypi.org"]
    assert allowlist.ip_entries == ["10.0.0.0/8", "192.0.2.0/24", "2001:db8::1"]
