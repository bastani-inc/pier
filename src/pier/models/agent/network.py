from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class NetworkAllowlist(BaseModel):
    domains: list[str] = Field(
        default_factory=list,
        description=(
            "Exact domain or leading-dot suffix allowed by network policy, "
            "for example 'api.anthropic.com' or '.anthropic.com'."
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


def normalize_allowed_hosts(hosts: list[str]) -> list[str]:
    """Normalize Harbor-style allowlist entries into :class:`NetworkAllowlist` form.

    Harbor spells a suffix match ``*.foo.com``; pier spells the same match
    ``.foo.com``. Entries are lowercased and stripped of a trailing dot here,
    so nothing downstream has to translate them again.

    IP literals and CIDR ranges are rejected: pier's egress proxy matches on
    hostname only, so allowing them would silently under-enforce the policy.
    """
    hostnames: list[str] = []
    for value in hosts:
        host = value.strip().lower().rstrip(".")
        if host.startswith("*."):
            host = host[1:]
        if "/" in host or ":" in host or host.replace(".", "").isdigit():
            raise ValueError(
                f"IP address and CIDR allowlist entries such as {value!r} are not "
                "supported by pier yet; use a hostname ('api.example.com') or a "
                "leading-dot suffix ('.example.com')."
            )
        hostnames.append(host)
    return NetworkAllowlist(domains=hostnames).domains
