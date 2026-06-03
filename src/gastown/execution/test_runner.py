"""
Test runner for executing pytest.

SECURITY NOTE: This runner performs NO sandboxing of its own.
In production, pytest commands MUST be validated and executed
inside an isolated container. The basic validation below is
ONLY a safety net against accidental dangerous operations.
"""

import asyncio
import os
import re
from pathlib import Path

from loguru import logger


class TestRunResult:
    """Result of a test run."""

    def __init__(
        self,
        success: bool,
        total: int,
        passed: int,
        failed: int,
        errors: int,
        skipped: int,
        output: str,
        duration: float,
        return_code: int = 0,
    ):
        self.success = success
        self.return_code = return_code
        self.total = total
        self.passed = passed
        self.failed = failed
        self.errors = errors
        self.skipped = skipped
        self.output = output
        self.duration = duration

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "return_code": self.return_code,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "output": self.output,
            "duration": self.duration,
        }

    def __repr__(self) -> str:
        return (
            f"<TestRunResult success={self.success} total={self.total} "
            f"passed={self.passed}>"
        )


DANGEROUS_COMMANDS = {
    "rm", "mkfs", "dd", "format", "shutdown", "reboot", "halt",
    "poweroff", "init", "killall", "pkill", "chmod", "chown",
    "mount", "umount", "fdisk", "parted", "mkswap",
}


class TestRunner:
    """Runs pytest on a given directory.

    SECURITY NOTE: This runner performs NO sandboxing of its own.
    In production, commands MUST be validated against an allowlist and
    executed inside an isolated container (Docker/gVisor/Firecracker).
    The basic command validation below is ONLY a safety net against
    accidental dangerous commands, not a security boundary.
    """

    def __init__(self, test_dir: str = ".", python_path: str = "python"):
        self.test_dir = Path(test_dir)
        self.python_path = python_path

    @staticmethod
    def _validate_command(cmd: list[str]) -> str | None:
        """Basic safety-net validation for the command list."""
        # Check the python path
        base = os.path.basename(cmd[0]) if cmd else ""
        if base in DANGEROUS_COMMANDS:
            return f"Command '{base}' is blocked by safety validation"
        # Check each arg for dangerous shell metacharacters
        dangerous_chars = {"|", ";", "&", "$", "`", "(", ")", "{", "}", "<", ">"}
        for arg in cmd:
            for char in dangerous_chars:
                if char in arg:
                    return f"Argument contains dangerous shell metacharacter '{char}'"
        return None

    async def run_tests(
        self,
        test_path: str | None = None,
        verbose: bool = False,
        extra_args: list[str] = None,
        timeout: float = 300.0,
    ) -> TestRunResult:
        """Run pytest and return results."""
        import time

        start_time = time.time()

        # Build command
        cmd = [self.python_path, "-m", "pytest"]
        if verbose:
            cmd.append("-v")
        if test_path:
            cmd.append(test_path)

        if extra_args:
            cmd.extend(extra_args)

        # Basic command validation safety net
        validation_error = self._validate_command(cmd)
        if validation_error:
            logger.warning(f"Blocked dangerous command: {validation_error}")
            return TestRunResult(
                success=False,
                return_code=-1,
                total=0,
                passed=0,
                failed=0,
                errors=1,
                skipped=0,
                output=validation_error,
                duration=0.0,
            )

        logger.debug(f"Running test command: {' '.join(cmd)}")

        try:
            # Run pytest
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(self.test_dir),
            )

            try:
                stdout, _ = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                return TestRunResult(
                    success=False,
                    return_code=-1,
                    total=0,
                    passed=0,
                    failed=0,
                    errors=1,
                    skipped=0,
                    output=f"Tests timed out after {timeout} seconds",
                    duration=time.time() - start_time,
                )

            duration = time.time() - start_time

            output = stdout.decode()

            # Parse pytest output (simplified)
            # In production, use pytest's JSON output plugin
            success = process.returncode == 0

            # Parse test counts from pytest summary line
            # Example lines:
            # "4 passed in 0.01s"
            # "1 failed in 0.03s"
            # "1 failed, 3 passed in 0.05s"
            # "2 passed, 1 failed, 1 error, 1 skipped in 0.05s"
            total = passed = failed = errors = skipped = 0
            for line in output.split("\n"):
                # Look for summary line containing passed/failed counts
                if "passed" in line or "failed" in line:
                    # Extract numbers for each category
                    match = re.search(r"(\d+)\s+passed", line)
                    if match:
                        passed = int(match.group(1))
                    match = re.search(r"(\d+)\s+failed", line)
                    if match:
                        failed = int(match.group(1))
                    match = re.search(r"(\d+)\s+error", line)
                    if match:
                        errors = int(match.group(1))
                    match = re.search(r"(\d+)\s+skipped", line)
                    if match:
                        skipped = int(match.group(1))
                    # If we found any counts, assume this is the summary line
                    if passed or failed or errors or skipped:
                        total = passed + failed + errors + skipped
                        break

            return TestRunResult(
                success=success,
                return_code=process.returncode,
                total=total,
                passed=passed,
                failed=failed,
                errors=errors,
                skipped=skipped,
                output=output,
                duration=duration,
            )

        except Exception as e:
            logger.error(f"Failed to run tests: {e}")
            return TestRunResult(
                success=False,
                return_code=-1,
                total=0,
                passed=0,
                failed=0,
                errors=1,
                skipped=0,
                output=str(e),
                duration=time.time() - start_time,
            )

    async def run_single_test(self, test_file: str, test_name: str) -> TestRunResult:
        """Run a single test by name."""
        return await self.run_tests(test_path=f"{test_file}::{test_name}")
