"""Capability flags describing what an environment type can do.

One ``EnvironmentCapabilities`` instance per environment, computed at
construction time and stored as ``self.capabilities``. Validators and
call sites read from it instead of from individual properties.
"""

from pydantic import BaseModel


class EnvironmentCapabilities(BaseModel):
    gpus: bool = False
    """Whether the environment can allocate GPUs to containers."""

    tpus: bool = False
    """Whether the environment can allocate TPU slices to containers.

    No pier environment supports this yet; a task declaring
    ``[environment.tpu]`` fails loudly at environment construction.
    """

    disable_internet: bool = False
    """Whether the environment can run containers without internet access."""

    filtered_egress: bool = False
    """Whether the environment can allow only declared outbound inference hosts."""

    phase_scoped_egress: bool = False
    """Whether one container can enforce different allowlists per phase.

    True for the proxy-based environments (docker, modal): the egress proxy
    authenticates the agent and a shared verifier as separate users, so each
    phase gets its own hosts. False where the allowlist is applied to the whole
    sandbox (daytona), which cannot tell the phases apart.
    """

    preinstall_agents: bool = False
    """Whether the environment can install selected agents at image build time."""

    windows: bool = False
    """Whether the environment can run Windows containers."""

    mounted: bool = False
    """Whether the environment mounts log directories as host filesystems."""

    docker_compose: bool = False
    """Whether the environment can run Docker Compose task environments."""


class EnvironmentResourceCapabilities(BaseModel):
    cpu_limit: bool = False
    """Whether CPU resources can be applied as a hard ceiling."""

    cpu_request: bool = False
    """Whether CPU resources can be applied as a resource request/reservation."""

    memory_limit: bool = False
    """Whether memory resources can be applied as a hard ceiling."""

    memory_request: bool = False
    """Whether memory resources can be applied as a resource request/reservation."""
