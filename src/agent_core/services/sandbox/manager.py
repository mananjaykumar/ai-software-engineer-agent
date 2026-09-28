"""Air-gapped Docker container execution sandbox for secure test runner isolation."""

import asyncio
import logging
from typing import Any

from docker.client import DockerClient
from docker.errors import DockerException

from agent_core.agent.state import FilePatch, VerificationResult
from agent_core.services.sandbox.config import SandboxConfig
from agent_core.services.sandbox.patcher import SandboxPatcher

logger = logging.getLogger(__name__)


class DockerSandboxManager:
    """Manages ephemeral, air-gapped Docker containers for validating untrusted code patches."""

    def __init__(
        self,
        config: SandboxConfig | None = None,
        docker_client: DockerClient | None = None,
    ) -> None:
        self.config = config or SandboxConfig()
        self._docker_client = docker_client

    @property
    def client(self) -> DockerClient:
        """Lazy-loaded Docker client."""
        if self._docker_client is None:
            self._docker_client = DockerClient.from_env()
        return self._docker_client

    async def run_in_sandbox(
        self,
        base_files: dict[str, str],
        patches: list[FilePatch] | None = None,
        command: list[str] | str | None = None,
        timeout: int | None = None,
    ) -> VerificationResult:
        """Asynchronously executes tests in an air-gapped container via asyncio.to_thread."""
        effective_timeout = timeout or self.config.timeout_seconds
        return await asyncio.to_thread(
            self._run_container_sync,
            base_files=base_files,
            patches=patches or [],
            command=command or ["python", "-m", "pytest", "-v"],
            timeout=effective_timeout,
        )

    def _run_container_sync(
        self,
        base_files: dict[str, str],
        patches: list[FilePatch],
        command: list[str] | str,
        timeout: int,
    ) -> VerificationResult:
        """Synchronously executes the full sandbox lifecycle."""
        # 1. Apply in-memory patches to virtual files
        try:
            patched_files = SandboxPatcher.apply_patches(base_files, patches)
        except Exception as e:
            return VerificationResult(
                passed=False,
                exit_code=1,
                stdout="",
                stderr=f"Patch application failed: {e}",
                failing_tests=["PATCH_APPLICATION_ERROR"],
            )

        # 2. Package into in-memory tar stream
        tar_bytes = SandboxPatcher.create_tar_archive(patched_files)

        # 3. Create isolated ephemeral container
        cmd_args = command if isinstance(command, list) else command.split()
        container: Any = None

        try:
            container = self.client.containers.create(
                image=self.config.image,
                command=cmd_args,
                working_dir=self.config.workdir,
                network_mode=self.config.network_mode,
                mem_limit=self.config.mem_limit,
                nano_cpus=self.config.nano_cpus,
                pids_limit=self.config.pids_limit,
                cap_drop=self.config.cap_drop,
                security_opt=self.config.security_opt,
                user=self.config.user,
                detach=True,
            )

            # 4. Stream tar into container workspace
            container.put_archive(self.config.workdir, tar_bytes)

            # 5. Start container and await completion with timeout
            container.start()
            wait_res = container.wait(timeout=timeout)
            status_code = wait_res.get("StatusCode", 1)

            # 6. Extract demuxed stdout and stderr
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

            # 7. Parse failure telemetry
            passed = status_code == 0
            failing_tests = [] if passed else SandboxPatcher.extract_failing_tests(stdout, stderr)

            return VerificationResult(
                passed=passed,
                exit_code=status_code,
                stdout=stdout,
                stderr=stderr,
                failing_tests=failing_tests,
            )

        except DockerException as de:
            logger.error("Docker execution error: %s", de)
            return VerificationResult(
                passed=False,
                exit_code=125,
                stdout="",
                stderr=f"Docker container error: {de}",
                failing_tests=["DOCKER_ERROR"],
            )
        except Exception as e:
            error_str = str(e)
            is_timeout = "timeout" in error_str.lower() or "read timed out" in error_str.lower()
            exit_code = 124 if is_timeout else 1
            stderr_msg = (
                f"Execution timed out after {timeout}s" if is_timeout else f"Sandbox error: {e}"
            )
            return VerificationResult(
                passed=False,
                exit_code=exit_code,
                stdout="",
                stderr=stderr_msg,
                failing_tests=["TIMEOUT"] if is_timeout else ["EXECUTION_ERROR"],
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
