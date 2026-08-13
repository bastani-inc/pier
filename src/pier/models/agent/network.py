from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable

from pydantic import BaseModel, Field, field_validator

_INVALID_ENTRY_MESSAGE = (
    "allowlist entries must be hostnames, IP addresses, or IP CIDR ranges, "
    "not URLs, ports, or paths"
)
_NETWORK_HOST_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def classify_allowlist_entry(value: str) -> tuple[bool, str]:
    """Normalize one allowlist entry and say whether it is an IP entry.

    Returns ``(is_ip_entry, normalized)``. Hostnames come back lowercased, with
    Harbor's ``*.foo.com`` wildcard rewritten to pier's ``.foo.com`` suffix
    form. IP literals and CIDR ranges come back canonicalized by
    :mod:`ipaddress` (compressed IPv6, ``2001:db8::/32`` and so on).
    """
    host = value.strip().lower().rstrip(".")
    if not host:
        raise ValueError("allowlist entry cannot be empty")
    if any(char in host for char in "[]%"):
        raise ValueError(f"{_INVALID_ENTRY_MESSAGE}: {value!r}")
    if host.startswith("*."):
        host = host[1:]
    if "/" in host:
        try:
            return True, ipaddress.ip_network(host, strict=True).compressed
        except ValueError as exc:
            raise ValueError(f"{_INVALID_ENTRY_MESSAGE}: {value!r}") from exc
    if ":" in host:
        try:
            return True, ipaddress.ip_address(host).compressed
        except ValueError as exc:
            raise ValueError(f"{_INVALID_ENTRY_MESSAGE}: {value!r}") from exc
    try:
        address = ipaddress.ip_address(host.lstrip("."))
    except ValueError:
        return False, host
    if host.startswith("."):
        raise ValueError(
            f"wildcard allowlist entries must target hostnames, not IP "
            f"address literals: {value!r}"
        )
    return True, address.compressed


class NetworkAllowlist(BaseModel):
    domains: list[str] = Field(
        default_factory=list,
        description=(
            "Exact domain or leading-dot suffix allowed by network policy, "
            "for example 'api.anthropic.com' or '.anthropic.com'."
        ),
    )
    ip_entries: list[str] = Field(
        default_factory=list,
        description=(
            "IP address literal or CIDR range allowed by network policy, "
            "for example '203.0.113.7', '10.0.0.0/8' or '2001:db8::/32'."
        ),
    )

    @field_validator("domains")
    @classmethod
    def normalize_domains(cls, domains: list[str]) -> list[str]:
        normalized: set[str] = set()
        for value in domains:
            domain = value.strip().lower().rstrip(".")
            if not domain:
                raise ValueError("domain cannot be empty")
            if any(char in domain for char in "/:*"):
                raise ValueError("domain must be an exact domain or leading-dot suffix")
            normalized.add(domain)
        return sorted(normalized)

    @field_validator("ip_entries")
    @classmethod
    def normalize_ip_entries(cls, entries: list[str]) -> list[str]:
        normalized: set[str] = set()
        for value in entries:
            is_ip, entry = classify_allowlist_entry(value)
            if not is_ip:
                raise ValueError(
                    f"ip_entries must be IP addresses or CIDR ranges: {value!r}"
                )
            normalized.add(entry)
        return sorted(normalized)

    @classmethod
    def from_entries(cls, entries: Iterable[str]) -> "NetworkAllowlist":
        """Build an allowlist from mixed hostname and IP/CIDR entries."""
        domains: list[str] = []
        ip_entries: list[str] = []
        for value in entries:
            is_ip, entry = classify_allowlist_entry(value)
            (ip_entries if is_ip else domains).append(entry)
        return cls(domains=domains, ip_entries=ip_entries)

    @property
    def is_empty(self) -> bool:
        return not self.domains and not self.ip_entries


def normalize_allowed_hosts(hosts: list[str]) -> list[str]:
    """Validate and normalize Harbor-style network allowlist entries."""
    normalized: list[str] = []
    for raw_host in hosts:
        host = raw_host.strip().lower().rstrip(".")
        if not host:
            raise ValueError("allowed_hosts entries must be non-empty hostnames.")
        if "/" in host:
            if "%" in host:
                raise ValueError(_INVALID_ENTRY_MESSAGE)
            try:
                normalized.append(ipaddress.ip_network(host, strict=True).compressed)
            except ValueError as exc:
                raise ValueError(_INVALID_ENTRY_MESSAGE) from exc
            continue
        if ":" in host:
            if "%" in host:
                raise ValueError(_INVALID_ENTRY_MESSAGE)
            try:
                address = ipaddress.ip_address(host)
            except ValueError as exc:
                raise ValueError(_INVALID_ENTRY_MESSAGE) from exc
            if address.version != 6:
                raise ValueError(_INVALID_ENTRY_MESSAGE)
            normalized.append(address.compressed)
            continue
        if "[" in host or "]" in host:
            raise ValueError(_INVALID_ENTRY_MESSAGE)
        if host.startswith("*."):
            hostname = host[2:]
            if not hostname:
                raise ValueError(
                    "allowed_hosts wildcard entries must include a hostname suffix."
                )
            try:
                ipaddress.ip_address(hostname)
            except ValueError:
                pass
            else:
                raise ValueError(
                    "allowed_hosts wildcard entries must target hostnames, not IP "
                    "address literals."
                )
        elif "*" in host:
            raise ValueError(
                "allowed_hosts wildcard entries must use a leading '*.' prefix."
            )
        else:
            hostname = host
        if not all(
            _NETWORK_HOST_LABEL_PATTERN.match(label) for label in hostname.split(".")
        ):
            raise ValueError(
                "allowed_hosts entries must be IP addresses, IP CIDR ranges, or "
                "valid hostnames or leading wildcard host patterns containing "
                "only letters, digits, hyphens, and dots."
            )
        normalized.append(host)
    return normalized


def normalize_pier_allowed_hosts(hosts: list[str]) -> list[str]:
    """Normalize Pier run-config entries, including legacy ``.example.com``."""
    allowlist = NetworkAllowlist.from_entries(hosts)
    return [*allowlist.domains, *allowlist.ip_entries]
