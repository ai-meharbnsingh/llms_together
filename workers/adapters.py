"""
Worker Adapters - CLI-login models (Claude/Kimi/Gemini) and local Ollama (DeepSeek/Qwen/Phi3).
ALL DB writes go through the message bus -> Watchdog.
Workers only have ReadOnlyDB access.
"""

import asyncio
import json
import logging
import os
import shutil
import signal
import time
from abc import ABC, abstractmethod
from typing import Optional

import aiohttp

logger = logging.getLogger("factory.workers")


class WorkerAdapter(ABC):
    """Base worker adapter. No direct DB writes."""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.timeout = config.get("timeout", 120)
        self.max_retries = config.get("max_retries", 2)

    @abstractmethod
    async def send_message(self, message: str, system_prompt: str = None,
                           files: list = None) -> dict:
        pass

    @abstractmethod
    async def check_health(self) -> str:
        pass

    @abstractmethod
    async def is_authenticated(self) -> bool:
        pass

    async def close(self):
        pass


class CLIWorkerAdapter(WorkerAdapter):
    """
    CLI-login adapter for Claude (Max Plan), Kimi (Moderato), Gemini (Plan).
    Uses subprocess to invoke CLI with pipe I/O.
    Subprocesses tracked by ProcessReaper for ghost prevention.
    """

    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        self.cli_command = config.get("cli_command", name)
        self._reaper = None  # set by watchdog after init

    def set_reaper(self, reaper):
        """Inject process reaper for subprocess tracking."""
        self._reaper = reaper

    @staticmethod
    def _kill_proc_group(proc) -> None:
        """Kill the entire process group of a subprocess.

        Node.js (Claude CLI) spawns child processes that survive a bare proc.kill().
        By killing the entire process group (SIGKILL) we ensure all descendants
        are reaped and no orphaned subprocesses remain to burn tokens.
        Falls back to proc.kill() if process-group kill is unavailable.
        """
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass

    def _get_cli_path(self) -> Optional[str]:
        return shutil.which(self.cli_command)

    def _clean_env(self) -> dict:
        """
        Build a clean subprocess environment.
        Strips CLAUDECODE to prevent 'nested session' errors
        (learned from working Streamlit chat app).
        """
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    async def is_authenticated(self) -> bool:
        """
        REAL auth check — sends a trivial prompt and checks for a valid response.
        --version only proves the binary exists, NOT that the session is logged in.
        """
        cli_path = self._get_cli_path()
        if not cli_path:
            logger.error(f"{self.name}: CLI '{self.cli_command}' not found in PATH")
            return False
        try:
            cmd, stdin_input = self._build_auth_check_command(cli_path)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if stdin_input else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._clean_env(),
                start_new_session=True,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_input.encode() if stdin_input else None),
                timeout=30,
            )
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode == 0 and out:
                logger.info(f"{self.name}: auth verified (got response)")
                return True

            # Check for auth-specific errors
            if "not authenticated" in err.lower() or "login" in err.lower():
                logger.error(f"{self.name}: NOT authenticated — run: {self.cli_command} login")
                return False

            # Got output even with non-zero rc? Still consider it alive
            if out:
                logger.warning(f"{self.name}: rc={proc.returncode} but got output — treating as authenticated")
                return True

            logger.error(f"{self.name}: auth check failed — rc={proc.returncode}, stderr={err[:200]}")
            return False

        except asyncio.TimeoutError:
            logger.error(f"{self.name}: auth check timed out (30s)")
            return False
        except Exception as e:
            logger.error(f"{self.name}: auth check error: {e}")
            return False

    def _build_auth_check_command(self, cli_path: str) -> tuple:
        """
        Build a minimal auth-verification command per CLI tool.
        Returns (cmd_list, stdin_input_or_None).
        """
        if self.name == "claude":
            # Claude: --print with stdin (proven pattern from Streamlit app)
            return ([cli_path, "--print", "--output-format", "text"], "say ok")
        elif self.name == "kimi":
            # Kimi: --print -p (same pattern as Claude, see kimi --help)
            return ([cli_path, "--print", "-p", "say ok", "--output-format", "text"], None)
        elif self.name == "gemini":
            # Gemini: --prompt for non-interactive
            return ([cli_path, "--prompt", "say ok", "--output-format", "text"], None)
        else:
            # Generic fallback
            return ([cli_path, "--version"], None)

    async def send_message(self, message: str, system_prompt: str = None,
                           files: list = None) -> dict:
        cli_path = self._get_cli_path()
        if not cli_path:
            return {"error": f"CLI '{self.cli_command}' not found", "success": False}

        start = time.time()
        for attempt in range(self.max_retries + 1):
            proc = None
            tracker_name = f"subprocess-{self.name}-{int(time.time())}"
            try:
                cmd, stdin_input = self._build_command(cli_path, message, system_prompt, files)
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE if stdin_input else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._clean_env(),
                    start_new_session=True,  # own process group → kill -9 kills Node.js tree
                )

                # Register with reaper (no parent — workers are in-process,
                # setting parent_name causes false orphan kills)
                if self._reaper and proc.pid:
                    self._reaper.track_subprocess(
                        proc, name=tracker_name,
                        parent_name=None,
                        max_silent=self.timeout + 30,
                    )

                if stdin_input:
                    proc.stdin.write(stdin_input.encode())
                    try:
                        await proc.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    proc.stdin.close()

                stdout_data = bytearray()
                stderr_data = bytearray()
                last_active = time.time()

                async def read_stream(stream, buffer):
                    nonlocal last_active
                    while True:
                        chunk = await stream.read(4096)
                        if not chunk:
                            break
                        buffer.extend(chunk)
                        last_active = time.time()

                stdout_task = asyncio.create_task(read_stream(proc.stdout, stdout_data))
                stderr_task = asyncio.create_task(read_stream(proc.stderr, stderr_data))

                pending_tasks = {stdout_task, stderr_task}
                while pending_tasks:
                    done, pending_tasks = await asyncio.wait(
                        pending_tasks, 
                        timeout=1.0, 
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    if time.time() - last_active > self.timeout:
                        for t in pending_tasks:
                            t.cancel()
                        raise asyncio.TimeoutError(f"Idle timeout of {self.timeout}s exceeded")

                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    # Process didn't exit cleanly — kill the process group
                    logger.debug(f"{self.name}: proc.wait() timed out, killing process group")
                    self._kill_proc_group(proc)

                out = stdout_data.decode("utf-8", errors="replace").strip()

                # Unregister from reaper (clean exit)
                if self._reaper:
                    self._reaper.unregister(tracker_name)

                if proc.returncode == 0 and out:
                    return {
                        "success": True,
                        "response": self._parse_response(out),
                        "elapsed_ms": int((time.time() - start) * 1000),
                        "worker": self.name,
                    }
                logger.warning(f"{self.name}: attempt {attempt+1} failed rc={proc.returncode}")
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)

            except asyncio.TimeoutError:
                logger.error(f"{self.name}: Timeout {self.timeout}s - killing process group")
                if proc:
                    try:
                        self._kill_proc_group(proc)
                        await asyncio.wait_for(proc.wait(), timeout=3.0)
                    except Exception:
                        logger.debug(f"{self.name}: kill after timeout failed", exc_info=True)
                if self._reaper:
                    self._reaper.unregister(tracker_name)
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)

            except Exception as e:
                logger.error(f"{self.name}: {e}")
                if proc and proc.returncode is None:
                    try:
                        self._kill_proc_group(proc)
                        await asyncio.wait_for(proc.wait(), timeout=3.0)
                    except Exception:
                        logger.debug(f"{self.name}: kill in error handler failed", exc_info=True)
                if self._reaper:
                    self._reaper.unregister(tracker_name)
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)

        return {"error": f"All {self.max_retries+1} attempts failed", "success": False}

    def _parse_response(self, raw: str) -> str:
        """
        Parse raw CLI output. Kimi dumps its internal protocol
        (TurnBegin, ThinkPart, TextPart, etc.) — extract just the TextPart text.
        Other CLIs return clean text and pass through unchanged.
        """
        if "TextPart(" not in raw:
            return raw
        # Extract text= value from TextPart blocks.
        # Use escape-aware pattern: match non-quote/non-backslash chars OR
        # backslash-escaped chars (like \' or \n), so escaped quotes inside
        # the text don't terminate the match early.
        import re
        parts = re.findall(
            r"TextPart\(\s*type='text',\s*text='((?:[^'\\]|\\.)*)'",
            raw, re.DOTALL,
        )
        if parts:
            # Unescape Python string escapes
            cleaned = []
            for p in parts:
                p = p.replace("\\'", "'").replace("\\n", "\n").replace("\\t", "\t")
                cleaned.append(p)
            return "\n\n".join(cleaned).strip()
        return raw

    # Maximum message size accepted by CLI workers (512 KB).
    # Prevents hitting OS argument-length limits when message is passed as a CLI arg.
    _MAX_MESSAGE_BYTES = 512 * 1024

    @staticmethod
    def _sanitize_cli_input(text: str) -> str:
        """
        Sanitize text before passing to CLI subprocess.
        - Rejects messages over 512 KB (raises ValueError).
        - Strips null bytes that could truncate arguments.
        - Prepends a space if text starts with '-' to prevent flag injection.
        """
        if not text:
            return text
        # Guard: reject oversized messages before they hit OS arg-length limits
        if len(text.encode("utf-8", errors="replace")) > CLIWorkerAdapter._MAX_MESSAGE_BYTES:
            raise ValueError(
                f"Message too large for CLI worker: "
                f"{len(text)} chars exceeds {CLIWorkerAdapter._MAX_MESSAGE_BYTES // 1024} KB limit"
            )
        # Remove null bytes
        text = text.replace('\x00', '')
        # Prevent flag injection: if text starts with -, prepend a space
        # so the CLI doesn't interpret it as a flag
        if text.lstrip().startswith('-'):
            text = ' ' + text.lstrip()
        return text

    def _build_command(self, cli_path, message, system_prompt=None, files=None):
        """
        Returns (cmd_list, stdin_input_or_None).
        Claude: prompt via stdin + --print (proven pattern from Streamlit app).
        Kimi/Gemini: prompt via --prompt flag.
        All user-supplied text is sanitized before passing to CLI.
        """
        safe_message = self._sanitize_cli_input(message)
        safe_system = self._sanitize_cli_input(system_prompt) if system_prompt else None

        if self.name == "claude":
            # Claude: send prompt via stdin, use --print for non-interactive
            cmd = [cli_path, "--print", "--output-format", "text"]
            if safe_system:
                cmd.extend(["--system-prompt", safe_system])
            if files:
                for f in files:
                    # Only allow files within expected paths (no flag injection via filenames)
                    if not f.startswith('-'):
                        cmd.extend(["--file", f])
            return (cmd, safe_message)  # message goes to stdin
        elif self.name == "kimi":
            # Kimi: --print -p for non-interactive (mirrors Claude pattern)
            cmd = [cli_path, "--print", "-p", safe_message, "--output-format", "text"]
            return (cmd, None)
        elif self.name == "gemini":
            cmd = [cli_path, "--prompt", safe_message, "--output-format", "text"]
            return (cmd, None)
        else:
            # Generic fallback
            cmd = [cli_path, "--prompt", safe_message]
            return (cmd, None)

    async def check_health(self) -> str:
        if not self._get_cli_path():
            return "offline"
        try:
            return "healthy" if await self.is_authenticated() else "degraded"
        except Exception:
            logger.debug(f"{self.name}: health check failed", exc_info=True)
            return "crashed"


class OllamaWorkerAdapter(WorkerAdapter):
    """Local Ollama adapter for DeepSeek, Qwen, Phi3."""

    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        self.api_base = config.get("api_base", "http://localhost:11434")
        self.model = config.get("model", name)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def is_authenticated(self) -> bool:
        try:
            s = await self._get_session()
            async with s.get(f"{self.api_base}/api/tags",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    base = self.model.split(":")[0]
                    return any(base in m["name"] for m in data.get("models", []))
            return False
        except Exception as e:
            logger.error(f"{self.name}: Ollama check failed: {e}")
            return False

    async def send_message(self, message: str, system_prompt: str = None,
                           files: list = None) -> dict:
        start = time.time()
        payload = {
            "model": self.model,
            "prompt": message,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 4096},
        }
        if system_prompt:
            payload["system"] = system_prompt

        for attempt in range(self.max_retries + 1):
            try:
                s = await self._get_session()
                async with s.post(f"{self.api_base}/api/generate",
                                  json=payload,
                                  timeout=aiohttp.ClientTimeout(total=self.timeout)) as r:
                    if r.status == 200:
                        data = await r.json()
                        return {
                            "success": True,
                            "response": data.get("response", ""),
                            "elapsed_ms": int((time.time() - start) * 1000),
                            "worker": self.name,
                            "tokens": {
                                "prompt": data.get("prompt_eval_count", 0),
                                "completion": data.get("eval_count", 0),
                            },
                        }
            except asyncio.TimeoutError:
                logger.error(f"{self.name}: Timeout {self.timeout}s")
            except Exception as e:
                logger.error(f"{self.name}: {e}")
            if attempt < self.max_retries:
                await asyncio.sleep(2 ** attempt)

        return {"error": f"All {self.max_retries+1} attempts failed", "success": False}

    async def check_health(self) -> str:
        try:
            s = await self._get_session()
            async with s.get(f"{self.api_base}/api/tags",
                             timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    return "healthy" if await self.is_authenticated() else "degraded"
            return "crashed"
        except Exception:
            logger.debug(f"{self.name}: Ollama health check failed", exc_info=True)
            return "offline"

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


def create_worker_adapter(name: str, config: dict) -> WorkerAdapter:
    wtype = config.get("type", "")
    if wtype in ("cli_login", "dual_auth"):
        return CLIWorkerAdapter(name, config)
    elif wtype == "local_ollama":
        return OllamaWorkerAdapter(name, config)
    raise ValueError(f"Unknown worker type '{wtype}' for {name}")
