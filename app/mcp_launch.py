"""Build a Windows-safe stdio command for an npx-launched MCP server.

``npx`` on Windows is ``npx.cmd``. Starting that batch file with a scoped
package such as ``@playwright/mcp@latest`` hands the argument list to
``cmd.exe``, which treats a leading ``@`` as the echo-off operator. The
package name is dropped, so npx opens an interactive command prompt. That
prompt writes the Windows copyright banner to stdout and echoes the MCP
initialize request (``clientInfo`` name ``mcp``, version ``0.1.0``) onto the
same stream. The Python MCP client then fails every line with
``Failed to parse JSONRPC message from server``.

The repair is to install the package with ``node.exe`` running ``npm-cli.js``
and to spawn ``node.exe <package bin>`` for the session. ``node.exe`` is a
real executable, so cmd.exe never owns the JSON-RPC pipes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

logger = logging.getLogger(__name__)

_NPX_NAMES = {"npx", "npx.cmd", "npx.exe", "npx.ps1"}
_YES_FLAGS = {"-y", "--yes"}
_JS_SUFFIXES = {".js", ".cjs", ".mjs"}
_INSTALL_TIMEOUT_SECONDS = 600
_locks_guard = threading.Lock()
_spec_locks: dict[str, threading.Lock] = {}


def build_mcp_stdio_command(
    command: str,
    args: Sequence[str],
    *,
    platform: str | None = None,
    node_executable: str | None = None,
    resolve_cli: Callable[[str], str] | None = None,
) -> tuple[str, list[str]]:
    """Return the executable and arguments the MCP stdio client should spawn."""
    plat = sys.platform if platform is None else platform
    if plat != "win32" or not _is_npx(command):
        return command, list(args)

    package_spec, server_args = split_npx_args(args)
    node = _node_executable(node_executable)
    if not node:
        raise RuntimeError(
            "Playwright MCP on Windows needs node.exe. Install Node.js 18 or newer "
            "and put `node` on PATH. Starting npx.cmd opens cmd.exe, and that shell "
            "writes its copyright banner onto the MCP stdio pipe."
        )
    cli = (resolve_cli or resolve_mcp_cli)(package_spec)
    cli_path = Path(cli)
    if not cli_path.is_file():
        raise RuntimeError(f"Resolved MCP entry does not exist: {cli_path}")
    if cli_path.suffix.lower() not in _JS_SUFFIXES:
        raise RuntimeError(
            f"Resolved MCP entry {cli_path} is not JavaScript. On Windows the stdio "
            "server has to be started with node.exe so cmd.exe cannot write its banner "
            "onto stdout."
        )
    logger.info("Starting MCP server with node.exe %s", cli_path)
    return node, [str(cli_path), *server_args]


def split_npx_args(args: Sequence[str]) -> tuple[str, list[str]]:
    """Split ``[npx flags...] <package spec> [server args...]``."""
    index = 0
    while index < len(args) and args[index] in _YES_FLAGS:
        index += 1
    if index >= len(args) or args[index].startswith("-"):
        raise RuntimeError(
            "MCP_ARGS must name an npm package before the server flags, "
            "for example [\"@playwright/mcp@latest\", \"--user-data-dir\", \".browser-profile\"]."
        )
    return args[index], list(args[index + 1 :])


def npm_package_name(spec: str) -> str:
    """Drop the version from ``@scope/name@version`` or ``name@version``."""
    text = spec.strip()
    if text.startswith("@") and "/" in text:
        scope, rest = text[1:].split("/", 1)
        name = rest.partition("@")[0]
        package = f"@{scope}/{name}"
    else:
        package = text.partition("@")[0]
    if not package or package.endswith("/") or package == "@":
        raise RuntimeError(f"Invalid npm package spec: {spec}")
    return package


def resolve_mcp_cli(
    package_spec: str,
    *,
    roots: Sequence[Path] | None = None,
    install_prefix: Path | None = None,
    node: str | None = None,
    npm_cli: Path | None = None,
    run: Callable[..., Any] | None = None,
) -> str:
    """Return the JavaScript bin for ``package_spec``, installing it if needed."""
    package_name = npm_package_name(package_spec)
    with _lock_for(package_spec):
        search_roots = list(roots) if roots is not None else _default_search_roots(package_spec)
        found = find_installed_cli(package_name, search_roots)
        if found:
            return found
        prefix = install_prefix or _install_prefix(package_spec)
        return _install_cli(
            package_spec,
            package_name,
            prefix=prefix,
            node=node,
            npm_cli=npm_cli,
            run=run,
        )


def find_installed_cli(package_name: str, roots: Sequence[Path]) -> str | None:
    """Return a package bin under one of ``roots`` if it is already on disk."""
    for root in roots:
        package_dir = root / "node_modules" / Path(*package_name.split("/"))
        cli = cli_from_package_dir(package_dir)
        if cli:
            return str(cli)
    return None


def cli_from_package_dir(package_dir: Path) -> Path | None:
    """Read ``package.json`` ``bin`` and return the existing script, if any."""
    meta_path = package_dir / "package.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    relative = _bin_relative(meta.get("bin"))
    if not relative:
        return None
    candidate = (package_dir / relative).resolve()
    if candidate.is_file():
        return candidate
    return None


def _install_cli(
    package_spec: str,
    package_name: str,
    *,
    prefix: Path,
    node: str | None,
    npm_cli: Path | None,
    run: Callable[..., Any] | None,
) -> str:
    node_exe = _node_executable(node)
    if not node_exe:
        raise RuntimeError(
            "Node.js is required to install the Playwright MCP server on Windows."
        )
    cli_js = npm_cli or find_npm_cli(node_exe)
    if cli_js is None:
        raise RuntimeError(
            "Could not find npm-cli.js next to node.exe. Reinstall Node.js, "
            "or set MCP_COMMAND to node and MCP_ARGS to the Playwright MCP cli.js path."
        )
    prefix.mkdir(parents=True, exist_ok=True)
    argv = [
        node_exe,
        str(cli_js),
        "install",
        "--no-save",
        "--no-fund",
        "--no-audit",
        "--loglevel=error",
        "--prefix",
        str(prefix),
        package_spec,
    ]
    logger.info("Installing %s for the Windows MCP stdio launch", package_spec)
    runner = run or subprocess.run
    run_kwargs: dict[str, Any] = {
        "cwd": str(prefix),
        "stdin": subprocess.DEVNULL,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": _INSTALL_TIMEOUT_SECONDS,
        "check": False,
    }
    if sys.platform == "win32":
        run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = runner(argv, **run_kwargs)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Timed out installing {package_spec} after {_INSTALL_TIMEOUT_SECONDS}s."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Could not run node.exe to install {package_spec}: {exc}") from exc

    found = find_installed_cli(package_name, [prefix])
    if found:
        if getattr(completed, "returncode", 1) != 0:
            logger.warning(
                "npm install %s exited %s but %s is present",
                package_spec,
                getattr(completed, "returncode", None),
                found,
            )
        return found
    detail = _process_detail(completed)
    raise RuntimeError(f"Could not install {package_spec} for the Windows MCP launch. {detail}".strip())


def find_npm_cli(node: str) -> Path | None:
    """Locate ``npm-cli.js`` for this Node install without launching ``npm.cmd``."""
    node_path = Path(node).resolve()
    node_dir = node_path.parent
    candidates = [
        node_dir / "node_modules" / "npm" / "bin" / "npm-cli.js",
        node_dir.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
        node_dir / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
    ]
    which_npm = shutil.which("npm")
    if which_npm:
        npm_dir = Path(which_npm).resolve().parent
        candidates.append(npm_dir / "node_modules" / "npm" / "bin" / "npm-cli.js")
        candidates.append(npm_dir / "npm-cli.js")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    if not node_path.is_file():
        return None
    try:
        completed = subprocess.run(
            [str(node_path), "-e", "process.stdout.write(require.resolve('npm/package.json'))"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    package_json = Path(completed.stdout.strip())
    cli = package_json.parent / "bin" / "npm-cli.js"
    return cli if cli.is_file() else None


def _is_npx(command: str) -> bool:
    return Path(command).name.lower() in _NPX_NAMES


def _node_executable(explicit: str | None) -> str | None:
    """Return node.exe, never a cmd/ps1 shim that would start a shell."""
    node = explicit or shutil.which("node")
    if not node:
        return None
    path = Path(node)
    if path.suffix.lower() not in {".cmd", ".bat", ".ps1"}:
        return str(path)
    sibling = path.with_suffix(".exe")
    if sibling.is_file():
        return str(sibling)
    resolved = shutil.which("node.exe")
    if resolved and Path(resolved).suffix.lower() == ".exe":
        return resolved
    return None


def _bin_relative(bin_field: object) -> str | None:
    if isinstance(bin_field, str) and bin_field.strip():
        return bin_field.strip()
    if not isinstance(bin_field, Mapping) or not bin_field:
        return None
    preferred = bin_field.get("playwright-mcp")
    if isinstance(preferred, str) and preferred.strip():
        return preferred.strip()
    for key in sorted(str(item) for item in bin_field):
        value = bin_field[key]
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _default_search_roots(package_spec: str) -> list[Path]:
    roots: list[Path] = [Path.cwd(), Path(__file__).resolve().parents[1], _install_prefix(package_spec)]
    node = shutil.which("node")
    if node:
        node_dir = Path(node).resolve().parent
        roots.extend([node_dir, node_dir.parent])
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "npm")
    roots.extend(_npx_cache_roots())
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _npx_cache_roots() -> list[Path]:
    """Return npx cache directories that already contain a ``node_modules`` tree."""
    bases: list[Path] = []
    configured = os.environ.get("npm_config_cache") or os.environ.get("NPM_CONFIG_CACHE")
    if configured:
        bases.append(Path(configured) / "_npx")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        bases.append(Path(local) / "npm-cache" / "_npx")
    appdata = os.environ.get("APPDATA")
    if appdata:
        bases.append(Path(appdata) / "npm-cache" / "_npx")
    bases.append(Path.home() / ".npm" / "_npx")
    found: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        try:
            children = [child for child in base.iterdir() if (child / "node_modules").is_dir()]
        except OSError:
            continue
        children.sort(key=_mtime, reverse=True)
        found.extend(children)
    return found


def _install_prefix(package_spec: str) -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or tempfile.gettempdir()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", package_spec).strip("._") or "package"
    return Path(base) / "agentic-crawler-studio" / "mcp" / slug


def _lock_for(package_spec: str) -> threading.Lock:
    with _locks_guard:
        lock = _spec_locks.get(package_spec)
        if lock is None:
            lock = threading.Lock()
            _spec_locks[package_spec] = lock
        return lock


def _process_detail(completed: Any) -> str:
    stderr = getattr(completed, "stderr", "") or ""
    stdout = getattr(completed, "stdout", "") or ""
    text = (stderr or stdout).strip()
    if not text:
        return f"npm exited with status {getattr(completed, 'returncode', 'unknown')}."
    return text[-1500:]
