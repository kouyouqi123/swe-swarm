"""
Build runner for executing shell commands.
"""

import asyncio
import os

from loguru import logger


class BuildResult:
    """Result of a build command."""

    def __init__(
        self,
        success: bool,
        return_code: int,
        stdout: str,
        stderr: str,
        duration: float,
    ):
        self.success = success
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration = duration

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "return_code": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration": self.duration,
        }

    def __repr__(self) -> str:
        return f"<BuildResult success={self.success} return_code={self.return_code}>"


DANGEROUS_COMMANDS = {
    "rm", "mkfs", "dd", "format", "shutdown", "reboot", "halt",
    "poweroff", "init", "killall", "pkill", "chmod", "chown",
    "mount", "umount", "fdisk", "parted", "mkswap",
}


class BuildRunner:
    """Runs shell commands for building, installing, etc.

    SECURITY NOTE: This runner performs NO sandboxing of its own.
    In production, commands MUST be validated against an allowlist and
    executed inside an isolated container (Docker/gVisor/Firecracker).
    The basic command validation below is ONLY a safety net against
    accidental dangerous commands, not a security boundary.
    """

    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir

    @staticmethod
    def _validate_command(command: str) -> str | None:
        """Basic safety-net validation: block known dangerous commands.

        This is NOT a security boundary — it's a best-effort guard against
        accidental destructive operations. Real sandboxing requires OS-level
        isolation (containers, seccomp, landlock, etc.).
        """
        base = os.path.basename(command)
        if base in DANGEROUS_COMMANDS:
            return f"Command '{command}' is blocked by safety validation"
        # Block shell metacharacters that could enable argument injection
        dangerous_chars = {"|", ";", "&", "$", "`", "(", ")", "{", "}", "<", ">"}
        for char in dangerous_chars:
            if char in command:
                return f"Command contains dangerous shell metacharacter '{char}'"
        return None

    async def run_command(
        self,
        command: str,
        args: list[str] = None,
        env: dict[str, str] = None,
        timeout: float = 300.0,  # 5 minutes
    ) -> BuildResult:
        """Run a shell command and return result."""
        # Basic command validation safety net
        validation_error = self._validate_command(command)
        if validation_error:
            logger.warning(f"Blocked dangerous command '{command}': {validation_error}")
            return BuildResult(
                success=False,
                return_code=-1,
                stdout="",
                stderr=validation_error,
                duration=0.0,
            )

        import time

        start_time = time.time()

        # Build full command
        cmd = [command]
        if args:
            cmd.extend(args)

        logger.debug(f"Running command: {' '.join(cmd)}")

        try:
            # Merge environment
            full_env = os.environ.copy()
            if env:
                full_env.update(env)

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
                cwd=self.working_dir,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                return BuildResult(
                    success=False,
                    return_code=-1,
                    stdout="",
                    stderr=f"Command timed out after {timeout} seconds",
                    duration=time.time() - start_time,
                )

            duration = time.time() - start_time

            return BuildResult(
                success=process.returncode == 0,
                return_code=process.returncode,
                stdout=stdout.decode(),
                stderr=stderr.decode(),
                duration=duration,
            )

        except Exception as e:
            logger.error(f"Failed to run command: {e}")
            return BuildResult(
                success=False,
                return_code=-1,
                stdout="",
                stderr=str(e),
                duration=time.time() - start_time,
            )

    async def install_dependencies(
        self, requirements_file: str = "requirements.txt"
    ) -> BuildResult:
        """Install Python dependencies from requirements.txt."""
        return await self.run_command("pip", ["install", "-r", requirements_file])

    async def run_build_script(self, script_path: str) -> BuildResult:
        """Run a build script (e.g., setup.py, build.sh)."""
        return await self.run_command("bash", [script_path])
