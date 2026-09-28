"""Sandbox configuration and security constraints for air-gapped Docker execution."""

from pydantic import BaseModel, Field


class SandboxConfig(BaseModel):
    """Resource quotas, security policies, and lifecycle settings for execution containers."""

    # 1. Base Container & User
    image: str = Field(
        default="python:3.12-slim",
        description="Base Docker image containing runtime and dependencies",
    )
    user: str = Field(
        default="1000:1000",
        description="Non-root user UID:GID for execution inside the container",
    )
    workdir: str = Field(
        default="/workspace",
        description="Isolated working directory path inside container",
    )

    # 2. Hard Security Boundaries (Air-Gap)
    network_mode: str = Field(
        default="none",
        description="Air-gapped network mode preventing any inbound/outbound traffic",
    )
    cap_drop: list[str] = Field(
        default_factory=lambda: ["ALL"],
        description="Kernel capabilities dropped to prevent privilege escalation",
    )
    security_opt: list[str] = Field(
        default_factory=lambda: ["no-new-privileges:true"],
        description="Prevents processes from gaining additional privileges",
    )

    # 3. cgroups Resource Limits
    mem_limit: str = Field(
        default="512m",
        description="Maximum memory allocated to container before OOM kill",
    )
    nano_cpus: int = Field(
        default=1_000_000_000,
        description="CPU quota (1,000,000,000 nano_cpus = 1.0 CPU core)",
    )
    pids_limit: int = Field(
        default=100,
        description="Maximum process/thread count to defend against fork bombs",
    )

    # 4. Execution Timeouts
    timeout_seconds: int = Field(
        default=30,
        description="Hard wall-clock timeout in seconds for test command execution",
    )
