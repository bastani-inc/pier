"""A separate verifier environment gets its own, tighter egress policy.

The verifier's sandbox is built with the hosts the [verifier] scope declares —
never the agent's model hosts — and the verifier command is the one that
receives the proxy variables there. Shared-environment verifiers are unchanged:
they run inside the agent's container and stay offline.
"""

import tempfile
from pathlib import Path

from pier.models.agent.network import NetworkAllowlist

from test_trial_verifier_artifact_transfer import (
    _make_env,
    _make_factory_recorder,
    _mock_agent,
    _run_trial,
    run_async,
)

PROXY_URL = "http://agent:secret@pier-egress-proxy:8080"

SEPARATE_ALLOWLIST_TOML = """
[agent]
timeout_sec = 10.0
network_mode = "allowlist"
allowed_hosts = ["agent.only.example"]
[verifier]
timeout_sec = 10.0
environment_mode = "separate"
network_mode = "allowlist"
allowed_hosts = ["deps.internal.example"]
[environment]
network_mode = "allowlist"
"""

SEPARATE_NO_HOSTS_TOML = """
[agent]
timeout_sec = 10.0
[verifier]
timeout_sec = 10.0
environment_mode = "separate"
network_mode = "no-network"
[environment]
network_mode = "no-network"
"""

SHARED_ALLOWLIST_TOML = """
[agent]
timeout_sec = 10.0
[verifier]
timeout_sec = 10.0
[environment]
network_mode = "allowlist"
allowed_hosts = ["deps.internal.example"]
"""


def _task(tmp: Path, toml: str) -> Path:
    task_dir = tmp / "task"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text(toml)
    (task_dir / "instruction.md").write_text("Do nothing.\n")
    env_dir = task_dir / "environment"
    env_dir.mkdir()
    (env_dir / "Dockerfile").write_text("FROM ubuntu:24.04\n")
    tests_dir = task_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "Dockerfile").write_text("FROM ubuntu:24.04\n")
    (tests_dir / "test.sh").write_text("#!/bin/bash\nexit 0\n")
    return task_dir


async def _run(tmp: Path, toml: str, verifier_envs: list) -> tuple[list[dict], object]:
    task_dir = _task(tmp, toml)
    trials_dir = tmp / "trials"
    trials_dir.mkdir()
    agent_env = _make_env(mounted=True)
    fake_create, calls = _make_factory_recorder(agent_env, verifier_envs)
    await _run_trial(
        task_dir,
        trials_dir,
        fake_create,
        agent=_mock_agent(NetworkAllowlist(domains=["api.model.example"])),
    )
    return calls, agent_env


@run_async
async def test_separate_verifier_env_gets_verifier_scoped_allowlist():
    with tempfile.TemporaryDirectory() as tmp:
        verifier_env = _make_env(mounted=True)
        calls, _ = await _run(Path(tmp), SEPARATE_ALLOWLIST_TOML, [verifier_env])

        assert calls[1]["network_allowlist"].domains == ["deps.internal.example"]
        # The agent env keeps its own, wider allowlist.
        agent_domains = calls[0]["network_allowlist"].domains
        assert "api.model.example" in agent_domains
        assert "agent.only.example" in agent_domains


@run_async
async def test_separate_verifier_env_without_hosts_stays_offline():
    with tempfile.TemporaryDirectory() as tmp:
        verifier_env = _make_env(mounted=True)
        calls, _ = await _run(Path(tmp), SEPARATE_NO_HOSTS_TOML, [verifier_env])

        assert calls[1]["network_allowlist"] is None


@run_async
async def test_separate_verifier_command_gets_proxy_env():
    with tempfile.TemporaryDirectory() as tmp:
        verifier_env = _make_env(mounted=True)
        verifier_env.agent_process_env.side_effect = lambda process_env: {
            **(process_env or {}),
            "HTTP_PROXY": PROXY_URL,
        }

        await _run(Path(tmp), SEPARATE_ALLOWLIST_TOML, [verifier_env])

        exec_envs = [
            call.kwargs.get("env") for call in verifier_env.exec.await_args_list
        ]
        assert any(
            env is not None and env.get("HTTP_PROXY") == PROXY_URL for env in exec_envs
        )


@run_async
async def test_shared_verifier_command_inherits_baseline_proxy_env():
    with tempfile.TemporaryDirectory() as tmp:
        calls, agent_env = await _run(Path(tmp), SHARED_ALLOWLIST_TOML, [])

        assert len(calls) == 1  # no separate verifier env was created
        agent_env.agent_process_env.assert_called_once_with(None)
        exec_envs = [call.kwargs.get("env") for call in agent_env.exec.await_args_list]
        assert all(env is None or "HTTP_PROXY" not in env for env in exec_envs)
