from __future__ import annotations

import json
import secrets
import shlex
from pathlib import Path

from pier.models.agent.install import AgentInstallSpec, InstallStep
from pier.models.agent.network import NetworkAllowlist

AGENT_INSTALL_DIR = ".pier-agent-install"
EGRESS_PROXY_SERVICE = "pier-egress-proxy"
EGRESS_PROXY_PORT = 8080
# Proxy users. The agent phase always authenticates as PROXY_AGENT_USER; a
# shared verifier with a network policy of its own gets PROXY_VERIFIER_USER,
# with its own token and its own ACL files (see squid_bootstrap_command).
PROXY_AGENT_USER = "agent"
PROXY_VERIFIER_USER = "verifier"


def docker_run_command(script: str) -> str:
    return "RUN " + json.dumps(["/bin/bash", "-c", script])


def _run_with_step_env(step: InstallStep) -> str:
    if not step.env:
        return step.run
    exports = "".join(
        f"export {key}={shlex.quote(value)}; " for key, value in step.env.items()
    )
    return exports + step.run


def dockerfile_install_commands(
    install: AgentInstallSpec,
    *,
    user: str | int | None,
) -> list[str]:
    commands: list[str] = []
    docker_agent_user = "root" if user is None else str(user)
    for step in install.steps:
        docker_user = "root" if step.user == "root" else docker_agent_user
        commands.extend(
            [
                f"USER {docker_user}",
                docker_run_command(_run_with_step_env(step)),
            ]
        )
    return commands


def write_agent_dockerfile(
    *,
    build_dir: Path,
    source_environment_dir: Path,
    prebuilt_image_name: str | None,
    install: AgentInstallSpec,
    user: str | int | None,
) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    dockerfile_path = build_dir / "Dockerfile"

    if prebuilt_image_name:
        dockerfile = [f"FROM {prebuilt_image_name}"]
    else:
        source = source_environment_dir / "Dockerfile"
        dockerfile = [source.read_text()]

    fingerprint = install.fingerprint()
    dockerfile.extend(
        [
            f"ARG PIER_AGENT_INSTALL_FINGERPRINT={fingerprint}",
            docker_run_command(
                'printf "Pier agent install fingerprint: %s\\n" '
                '"$PIER_AGENT_INSTALL_FINGERPRINT"'
            ),
        ]
    )
    dockerfile.extend(dockerfile_install_commands(install, user=user))
    dockerfile.append("")
    dockerfile_path.write_text("\n".join(dockerfile))
    return dockerfile_path


def proxy_environment(
    token: str,
    host: str,
    port: int = EGRESS_PROXY_PORT,
    user: str = PROXY_AGENT_USER,
) -> dict[str, str]:
    proxy_url = f"http://{user}:{token}@{host}:{port}"
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
    }


def new_proxy_token() -> str:
    return secrets.token_urlsafe(24)


def squid_bootstrap_command(
    allowlist: NetworkAllowlist,
    verifier_allowlist: NetworkAllowlist | None = None,
) -> str:
    """Build the proxy bootstrap script for one or two phase allowlists.

    The allowlist values travel in the environment (see :func:`proxy_policy_env`),
    but the ACL lines are baked in here: squid warns about an ACL pointed at an
    empty file and such an ACL matches nothing, so a rule is emitted only when
    its side of the allowlist has entries.

    ``verifier_allowlist`` is the policy of a *shared* verifier that has one of
    its own. When it has entries, a second htpasswd user is provisioned with its
    own token and its own ACL files, and every ``http_access allow`` line is
    scoped to the proxy user it belongs to, so neither phase can reach the
    other's hosts. With no verifier entries the script is byte-for-byte the
    single-user script pier has always generated.
    """
    verifier_hosts = verifier_allowlist or NetworkAllowlist()
    two_users = not verifier_hosts.is_empty
    acls: list[str] = []
    rules: list[str] = []
    agent_acl = "authenticated"
    if two_users:
        # Squid warns about ACLs no rule references, so the agent's user ACL is
        # emitted only when the agent phase has hosts of its own to allow.
        agent_acl = "agent_user"
        if not allowlist.is_empty:
            acls.append(f"acl {agent_acl} proxy_auth {PROXY_AGENT_USER}")
        acls.append(f"acl verifier_user proxy_auth {PROXY_VERIFIER_USER}")
    if allowlist.domains:
        acls.append('acl allowed_domains dstdomain "/tmp/allowed_domains.txt"')
        rules.append(f"http_access allow {agent_acl} allowed_domains")
    if allowlist.ip_entries:
        acls.append('acl allowed_ips dst "/tmp/allowed_ips.txt"')
        rules.append(f"http_access allow {agent_acl} allowed_ips")
    if verifier_hosts.domains:
        acls.append(
            'acl allowed_domains_verifier dstdomain "/tmp/allowed_domains_verifier.txt"'
        )
        rules.append("http_access allow verifier_user allowed_domains_verifier")
    if verifier_hosts.ip_entries:
        acls.append('acl allowed_ips_verifier dst "/tmp/allowed_ips_verifier.txt"')
        rules.append("http_access allow verifier_user allowed_ips_verifier")
    acl_block = "\n".join(acls)
    rule_block = "\n".join(rules)
    verifier_files = ""
    verifier_htpasswd = ""
    if two_users:
        verifier_files = (
            "\n"
            + r"""printf '%s' "$VERIFIER_ALLOWLIST_DOMAINS" | tr ',' '\n' | sed '/^[[:space:]]*$/d' \
  > /tmp/allowed_domains_verifier.txt
printf '%s' "$VERIFIER_ALLOWLIST_IPS" | tr ',' '\n' | sed '/^[[:space:]]*$/d' \
  > /tmp/allowed_ips_verifier.txt
"""
        )
        verifier_htpasswd = (
            f"\nhtpasswd -b /tmp/squid.passwd {PROXY_VERIFIER_USER} "
            '"$VERIFIER_PROXY_TOKEN"'
        )
    return rf"""#!/usr/bin/env bash
set -eu

printf '%s' "$ALLOWLIST_DOMAINS" | tr ',' '\n' | sed '/^[[:space:]]*$/d' \
  > /tmp/allowed_domains.txt
printf '%s' "$ALLOWLIST_IPS" | tr ',' '\n' | sed '/^[[:space:]]*$/d' \
  > /tmp/allowed_ips.txt
{verifier_files}
htpasswd -bc /tmp/squid.passwd {PROXY_AGENT_USER} "$PROXY_TOKEN"{verifier_htpasswd}

cat > /tmp/squid.conf <<'EOF'
http_port 0.0.0.0:8080
pid_filename /tmp/squid.pid
coredump_dir /tmp

auth_param basic program /usr/lib/squid/basic_ncsa_auth /tmp/squid.passwd
auth_param basic realm PierPolicyProxy
acl authenticated proxy_auth REQUIRED

acl SSL_ports port 443
acl Safe_ports port 80 443
acl CONNECT method CONNECT
{acl_block}

http_access deny !Safe_ports
http_access deny CONNECT !SSL_ports
{rule_block}
http_access deny all

cache deny all
access_log stdio:/tmp/squid_access.log
cache_log /tmp/squid_cache.log
log_mime_hdrs off
shutdown_lifetime 1 seconds
EOF

exec squid -N -f /tmp/squid.conf -d 1
"""


def proxy_policy_env(
    allowlist: NetworkAllowlist,
    token: str,
    verifier_allowlist: NetworkAllowlist | None = None,
    verifier_token: str | None = None,
) -> dict[str, str]:
    env = {
        "PROXY_TOKEN": token,
        "ALLOWLIST_DOMAINS": ",".join(allowlist.domains),
        "ALLOWLIST_IPS": ",".join(allowlist.ip_entries),
    }
    if verifier_token is not None and verifier_allowlist is not None:
        env |= {
            "VERIFIER_PROXY_TOKEN": verifier_token,
            "VERIFIER_ALLOWLIST_DOMAINS": ",".join(verifier_allowlist.domains),
            "VERIFIER_ALLOWLIST_IPS": ",".join(verifier_allowlist.ip_entries),
        }
    return env


def merge_proxy_env(
    proxy_env: dict[str, str] | None, env: dict[str, str] | None
) -> dict[str, str] | None:
    """Layer a phase's proxy variables under its per-exec environment."""
    if not proxy_env:
        return env
    merged = dict(proxy_env)
    if env:
        merged.update(env)
    return merged or None


def write_docker_proxy_compose(
    *,
    path: Path,
    proxy_dir: Path,
    allowlist: NetworkAllowlist,
    token: str,
    verifier_allowlist: NetworkAllowlist | None = None,
    verifier_token: str | None = None,
) -> Path:
    proxy_dir.mkdir(parents=True, exist_ok=True)
    (proxy_dir / "Dockerfile").write_text(
        "\n".join(
            [
                "FROM ubuntu:24.04",
                "RUN apt-get update && "
                "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
                "apache2-utils ca-certificates squid && "
                "rm -rf /var/lib/apt/lists/*",
                "COPY start-squid.sh /usr/local/bin/start-squid.sh",
                "RUN chmod +x /usr/local/bin/start-squid.sh",
                'CMD ["bash", "/usr/local/bin/start-squid.sh"]',
                "",
            ]
        )
    )
    (proxy_dir / "start-squid.sh").write_text(
        squid_bootstrap_command(allowlist, verifier_allowlist)
    )
    compose = {
        "services": {
            "main": {
                "networks": ["pier-egress-internal"],
                "depends_on": {
                    EGRESS_PROXY_SERVICE: {
                        "condition": "service_healthy",
                    },
                },
            },
            EGRESS_PROXY_SERVICE: {
                "build": {"context": str(proxy_dir.resolve().absolute())},
                "environment": proxy_policy_env(
                    allowlist, token, verifier_allowlist, verifier_token
                ),
                "healthcheck": {
                    "test": ["CMD-SHELL", "bash -lc '</dev/tcp/127.0.0.1/8080'"],
                    "interval": "1s",
                    "timeout": "1s",
                    "retries": 30,
                },
                "networks": ["pier-egress-internal", "default"],
            },
        },
        "networks": {
            "pier-egress-internal": {
                "internal": True,
            },
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(compose, indent=2))
    return path


def shell_export_env(env: dict[str, str]) -> str:
    return " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
