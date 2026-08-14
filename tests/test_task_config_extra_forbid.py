"""Unknown task-config keys must fail loudly instead of being dropped.

Pydantic's default ``extra='ignore'`` discarded any key the task models did
not declare. That is how ``[[verifier.collect]]`` and ``network_mode`` were
silently dropped by an older pier while the tasks that declared them still
parsed. Every task-config model now sets ``extra="forbid"``, so a key the
schema does not know raises a ``ValidationError`` naming it.
"""

import pytest
from pydantic import ValidationError

from pier.models.task.config import (
    AgentConfig,
    ArtifactConfig,
    Author,
    EnvironmentConfig,
    HealthcheckConfig,
    MCPServerConfig,
    NetworkPolicyFieldsMixin,
    PackageInfo,
    SolutionConfig,
    StepConfig,
    TaskConfig,
    VerifierCollectConfig,
    VerifierConfig,
)

FORBID_MODELS = [
    AgentConfig,
    ArtifactConfig,
    Author,
    EnvironmentConfig,
    HealthcheckConfig,
    MCPServerConfig,
    NetworkPolicyFieldsMixin,
    PackageInfo,
    SolutionConfig,
    StepConfig,
    TaskConfig,
    VerifierCollectConfig,
    VerifierConfig,
]


@pytest.mark.parametrize("model", FORBID_MODELS, ids=lambda m: m.__name__)
def test_task_config_models_forbid_extra(model):
    assert model.model_config.get("extra") == "forbid"


def test_unknown_environment_key_raises_validation_error_naming_it():
    with pytest.raises(ValidationError) as excinfo:
        TaskConfig.model_validate_toml(
            """
[environment]
docker_image = "example/image:tag"
bogus_key = 1
"""
        )
    errors = excinfo.value.errors()
    assert any(
        error["type"] == "extra_forbidden" and error["loc"] == ("environment", "bogus_key")
        for error in errors
    ), errors
    assert "bogus_key" in str(excinfo.value)


def test_unknown_top_level_key_raises_validation_error_naming_it():
    with pytest.raises(ValidationError) as excinfo:
        TaskConfig.model_validate_toml('schema_version = "1.3"\nbogus_top = true\n')
    assert "bogus_top" in str(excinfo.value)


def test_collect_hook_still_round_trips():
    cfg = TaskConfig.model_validate_toml(
        """
[environment]
docker_image = "example/image:tag"

[[verifier.collect]]
command = "git diff > /logs/artifacts/model.patch"
timeout_sec = 300.0
"""
    )
    assert len(cfg.verifier.collect) == 1
    assert cfg.verifier.collect[0].command == "git diff > /logs/artifacts/model.patch"
    assert cfg.verifier.collect[0].timeout_sec == 300.0


def test_free_form_metadata_is_still_accepted():
    cfg = TaskConfig.model_validate_toml(
        """
[metadata]
anything = "goes"
nested = { still = "fine" }
"""
    )
    assert cfg.metadata["anything"] == "goes"
    assert cfg.metadata["nested"] == {"still": "fine"}
