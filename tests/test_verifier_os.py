import asyncio
from unittest.mock import AsyncMock, MagicMock

from pier.environments.base import ExecResult
from pier.models.task.config import TaskOS
from pier.models.task.task import Task
from pier.models.trial.paths import EnvironmentPaths, TrialPaths
from pier.verifier.verifier import Verifier


def test_separate_verifier_uses_its_own_os_for_command_paths(tmp_path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text(
        '[environment]\nos = "linux"\n[verifier.environment]\nos = "windows"\n'
    )
    (task_dir / "instruction.md").write_text("Do nothing.\n")
    tests_dir = task_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test.bat").write_text("@echo off\r\n")

    trial_paths = TrialPaths(tmp_path / "trial")
    trial_paths.mkdir()
    trial_paths.reward_text_path.write_text("1")

    environment = MagicMock()
    environment.task_os = TaskOS.WINDOWS
    environment.env_paths = EnvironmentPaths.for_windows()
    environment.capabilities.mounted = True
    environment.exec = AsyncMock(
        return_value=ExecResult(stdout="", stderr="", return_code=0)
    )

    verifier = Verifier(
        task=Task(task_dir),
        trial_paths=trial_paths,
        environment=environment,
        skip_tests_upload=True,
    )

    asyncio.run(verifier.verify())

    command = environment.exec.await_args.kwargs["command"]
    assert "cmd /c C:\\tests\\test.bat" in command
    assert "> C:\\logs\\verifier\\test-stdout.txt" in command


def test_reward_json_takes_precedence_over_reward_text(tmp_path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text("")
    (task_dir / "instruction.md").write_text("Do nothing.\n")
    tests_dir = task_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test.sh").write_text("#!/bin/sh\n")

    trial_paths = TrialPaths(tmp_path / "trial")
    trial_paths.mkdir()
    trial_paths.reward_text_path.write_text("0")
    trial_paths.reward_json_path.write_text('{"correctness": 1}')

    environment = MagicMock()
    environment.task_os = TaskOS.LINUX
    environment.env_paths = EnvironmentPaths()
    environment.capabilities.mounted = True
    environment.exec = AsyncMock(
        return_value=ExecResult(stdout="", stderr="", return_code=0)
    )

    verifier = Verifier(
        task=Task(task_dir),
        trial_paths=trial_paths,
        environment=environment,
        skip_tests_upload=True,
    )

    result = asyncio.run(verifier.verify())

    assert result.rewards == {"correctness": 1}
