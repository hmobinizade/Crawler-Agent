import json
from pathlib import Path

import pytest

from app.mcp_launch import (
    build_mcp_stdio_command,
    cli_from_package_dir,
    find_npm_cli,
    npm_package_name,
    resolve_mcp_cli,
    split_npx_args,
)


DEFAULT_ARGS = ["@playwright/mcp@latest", "--user-data-dir", ".browser-profile"]


def _write_package(root: Path, package_name: str, bin_field) -> Path:
    package_dir = root / "node_modules" / Path(*package_name.split("/"))
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps({"name": package_name, "bin": bin_field}),
        encoding="utf-8",
    )
    relative = bin_field if isinstance(bin_field, str) else bin_field["playwright-mcp"]
    cli = package_dir / relative
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    return cli


def test_npm_package_name_strips_version():
    assert npm_package_name("@playwright/mcp@latest") == "@playwright/mcp"
    assert npm_package_name("@playwright/mcp@0.0.41") == "@playwright/mcp"
    assert npm_package_name("@playwright/mcp") == "@playwright/mcp"
    assert npm_package_name("left-pad@1.2.3") == "left-pad"
    assert npm_package_name("left-pad") == "left-pad"


def test_split_npx_args_keeps_server_flags():
    assert split_npx_args(DEFAULT_ARGS) == (
        "@playwright/mcp@latest",
        ["--user-data-dir", ".browser-profile"],
    )
    assert split_npx_args(["-y", "--yes", "@playwright/mcp@latest", "--headless"]) == (
        "@playwright/mcp@latest",
        ["--headless"],
    )


def test_split_npx_args_requires_a_package():
    with pytest.raises(RuntimeError, match="MCP_ARGS"):
        split_npx_args(["--user-data-dir", ".browser-profile"])


def test_other_platforms_keep_npx():
    command, args = build_mcp_stdio_command("npx", DEFAULT_ARGS, platform="linux")
    assert command == "npx"
    assert args == DEFAULT_ARGS


def test_non_npx_command_is_unchanged_on_windows():
    command, args = build_mcp_stdio_command(
        "node",
        ["C:/tools/cli.js", "--headless"],
        platform="win32",
    )
    assert command == "node"
    assert args == ["C:/tools/cli.js", "--headless"]


def test_windows_scoped_npx_spawns_node_instead_of_cmd(tmp_path):
    """cmd.exe must not own the MCP pipes.

    Launching npx.cmd with @playwright/mcp makes cmd.exe interactive. The shell
    writes ``Microsoft Windows [Version ...]`` and ``(c) Microsoft Corporation``
    to stdout, then echoes the initialize request (``"name":"mcp","version":"0.1.0"``)
    after the ``C:\\...>`` prompt. Those lines are not JSON-RPC.
    """
    node = tmp_path / "node.exe"
    node.write_text("", encoding="utf-8")
    cli = tmp_path / "pkg" / "cli.js"
    cli.parent.mkdir()
    cli.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    seen = {}

    def resolve(spec):
        seen["spec"] = spec
        return str(cli)

    command, args = build_mcp_stdio_command(
        "npx",
        ["-y", *DEFAULT_ARGS],
        platform="win32",
        node_executable=str(node),
        resolve_cli=resolve,
    )
    assert command == str(node)
    assert args == [str(cli), "--user-data-dir", ".browser-profile"]
    assert seen["spec"] == "@playwright/mcp@latest"
    assert "cmd.exe" not in command.lower()
    assert all(not item.lower().endswith((".cmd", ".bat")) for item in [command, *args])


def test_windows_prefers_node_exe_over_cmd_shim(tmp_path):
    node_cmd = tmp_path / "node.cmd"
    node_exe = tmp_path / "node.exe"
    node_cmd.write_text("@echo off\r\n", encoding="utf-8")
    node_exe.write_text("", encoding="utf-8")
    cli = tmp_path / "cli.js"
    cli.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    command, args = build_mcp_stdio_command(
        "npx",
        DEFAULT_ARGS,
        platform="win32",
        node_executable=str(node_cmd),
        resolve_cli=lambda spec: str(cli),
    )
    assert command == str(node_exe)
    assert args[0] == str(cli)


def test_windows_npx_without_node_explains_the_banner(monkeypatch):
    monkeypatch.setattr("app.mcp_launch.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="copyright banner"):
        build_mcp_stdio_command("npx.cmd", DEFAULT_ARGS, platform="win32", node_executable=None)


def test_existing_install_is_reused(tmp_path):
    cli = _write_package(tmp_path, "@playwright/mcp", {"playwright-mcp": "cli.js", "other": "other.js"})
    (tmp_path / "node_modules" / "@playwright" / "mcp" / "other.js").write_text("", encoding="utf-8")

    def fail_run(*args, **kwargs):
        raise AssertionError("npm install should not run when the bin is already present")

    resolved = resolve_mcp_cli(
        "@playwright/mcp@latest",
        roots=[tmp_path],
        run=fail_run,
    )
    assert resolved == str(cli.resolve())
    assert cli_from_package_dir(cli.parent) == cli.resolve()


def test_existing_npx_cache_is_reused(tmp_path, monkeypatch):
    cache = tmp_path / "npm-cache" / "_npx" / "hashed"
    _write_package(cache, "@playwright/mcp", "cli.js")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    def fail_run(*args, **kwargs):
        raise AssertionError("npm install should reuse the npx cache")

    resolved = resolve_mcp_cli("@playwright/mcp@latest", run=fail_run)
    assert resolved.replace("\\", "/").endswith("/node_modules/@playwright/mcp/cli.js")


def test_install_uses_node_and_ignores_cmd_banner(tmp_path):
    node = tmp_path / "node.exe"
    node.write_text("", encoding="utf-8")
    npm_cli = tmp_path / "npm-cli.js"
    npm_cli.write_text("", encoding="utf-8")
    prefix = tmp_path / "prefix"
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        _write_package(prefix, "@playwright/mcp", "cli.js")
        class Completed:
            returncode = 0
            stdout = (
                "Microsoft Windows [Version 10.0.22000.3260]\r\n"
                "(c) Microsoft Corporation. All rights reserved.\r\n"
                "\r\n"
                "added 1 package\n"
            )
            stderr = ""
        return Completed()

    resolved = resolve_mcp_cli(
        "@playwright/mcp@latest",
        roots=[],
        install_prefix=prefix,
        node=str(node),
        npm_cli=npm_cli,
        run=fake_run,
    )
    assert resolved.endswith(str(Path("@playwright") / "mcp" / "cli.js"))
    assert captured["argv"][0] == str(node)
    assert captured["argv"][1] == str(npm_cli)
    assert captured["argv"][-1] == "@playwright/mcp@latest"
    assert "npx.cmd" not in captured["argv"]
    assert captured["kwargs"].get("shell") is not True
    assert "cmd.exe" not in " ".join(captured["argv"]).lower()


def test_install_failure_includes_npm_output(tmp_path):
    node = tmp_path / "node.exe"
    node.write_text("", encoding="utf-8")
    npm_cli = tmp_path / "npm-cli.js"
    npm_cli.write_text("", encoding="utf-8")

    def fake_run(argv, **kwargs):
        class Completed:
            returncode = 1
            stdout = ""
            stderr = "npm ERR! network unreachable"
        return Completed()

    with pytest.raises(RuntimeError, match="network unreachable"):
        resolve_mcp_cli(
            "@playwright/mcp@latest",
            roots=[],
            install_prefix=tmp_path / "prefix",
            node=str(node),
            npm_cli=npm_cli,
            run=fake_run,
        )


def test_find_npm_cli_checks_windows_and_unix_layouts(tmp_path):
    windows = tmp_path / "windows"
    (windows / "node_modules" / "npm" / "bin").mkdir(parents=True)
    win_cli = windows / "node_modules" / "npm" / "bin" / "npm-cli.js"
    win_cli.write_text("", encoding="utf-8")
    (windows / "node.exe").write_text("", encoding="utf-8")
    assert find_npm_cli(str(windows / "node.exe")) == win_cli

    unix = tmp_path / "unix"
    (unix / "bin").mkdir(parents=True)
    (unix / "lib" / "node_modules" / "npm" / "bin").mkdir(parents=True)
    unix_cli = unix / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    unix_cli.write_text("", encoding="utf-8")
    (unix / "bin" / "node").write_text("", encoding="utf-8")
    assert find_npm_cli(str(unix / "bin" / "node")) == unix_cli
