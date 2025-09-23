from __future__ import annotations

import subprocess

import pytest

import quick_launch


def test_resolve_quick_launch_dir_from_override(tmp_path, monkeypatch):
    override = tmp_path / "custom"
    result = quick_launch.resolve_quick_launch_dir(override)
    assert result == override


def test_resolve_quick_launch_dir_from_env(tmp_path, monkeypatch):
    env_dir = tmp_path / "env"
    monkeypatch.setenv("REPLYBOT_QUICK_LAUNCH_DIR", str(env_dir))
    result = quick_launch.resolve_quick_launch_dir()
    assert result == env_dir


def test_resolve_quick_launch_dir_default(monkeypatch, tmp_path):
    monkeypatch.delenv("REPLYBOT_QUICK_LAUNCH_DIR", raising=False)
    appdata = tmp_path / "AppData"
    monkeypatch.setenv("APPDATA", str(appdata))
    expected = appdata / "Microsoft" / "Internet Explorer" / "Quick Launch"
    assert quick_launch.resolve_quick_launch_dir() == expected


def test_resolve_quick_launch_dir_missing_appdata(monkeypatch):
    monkeypatch.delenv("REPLYBOT_QUICK_LAUNCH_DIR", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    with pytest.raises(quick_launch.QuickLaunchError):
        quick_launch.resolve_quick_launch_dir()


def test_sanitize_filename_rejects_empty():
    with pytest.raises(quick_launch.QuickLaunchError):
        quick_launch._sanitize_filename(" :: ")


def test_sanitize_filename_replaces_invalid_chars():
    assert quick_launch._sanitize_filename("Reply:Bot*") == "Reply_Bot_"


def test_create_quick_launch_icon_requires_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(quick_launch, "_is_windows", lambda: False)
    monkeypatch.setenv("REPLYBOT_QUICK_LAUNCH_DIR", str(tmp_path))
    with pytest.raises(quick_launch.QuickLaunchError):
        quick_launch.create_quick_launch_icon("C:/ReplyBot/replybot.exe")


def test_create_quick_launch_icon_invokes_powershell(monkeypatch, tmp_path):
    recorded = {}

    def fake_run(cmd, capture_output, text, check):
        recorded["cmd"] = cmd
        recorded["capture_output"] = capture_output
        recorded["text"] = text
        recorded["check"] = check
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(quick_launch, "_is_windows", lambda: True)
    monkeypatch.setenv("REPLYBOT_QUICK_LAUNCH_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

    target = tmp_path / "ReplyBot" / "replybot.exe"
    icon = tmp_path / "replybot.ico"
    link_path = quick_launch.create_quick_launch_icon(
        target,
        name="ReplyBot",
        arguments="--debug",
        icon_path=icon,
    )

    expected_link = tmp_path / "ReplyBot.lnk"
    assert link_path == expected_link
    assert expected_link.parent.exists()

    cmd = recorded["cmd"]
    assert cmd[0].lower().endswith("powershell") or cmd[0].lower().endswith("pwsh")
    assert "-NoProfile" in cmd
    assert "-NonInteractive" in cmd
    assert "-ExecutionPolicy" in cmd
    assert cmd[-2] == "-Command"
    ps_script = cmd[-1]
    assert "ReplyBot" in ps_script
    assert str(target) in ps_script
    assert "--debug" in ps_script
    assert recorded["capture_output"] is True
    assert recorded["text"] is True
    assert recorded["check"] is False


def test_main_creates_shortcut(monkeypatch, tmp_path, capsys):
    recorded = {}

    def fake_run(cmd, capture_output, text, check):
        recorded["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(quick_launch, "_is_windows", lambda: True)
    monkeypatch.setenv("REPLYBOT_QUICK_LAUNCH_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

    target_dir = tmp_path / "ReplyBot"
    target_dir.mkdir()
    target = target_dir / "replybot.exe"
    target.write_bytes(b"")
    icon = tmp_path / "replybot.ico"
    icon.write_bytes(b"")

    exit_code = quick_launch.main(
        [
            str(target),
            "--name",
            "ReplyBot CLI",
            "--arguments",
            "--log-level debug",
            "--icon",
            str(icon),
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    expected_link = tmp_path / "ReplyBot CLI.lnk"
    assert str(expected_link) in captured.out
    assert captured.err == ""

    cmd = recorded["cmd"]
    assert cmd[-2] == "-Command"
    script = cmd[-1]
    assert str(target) in script
    assert "--log-level debug" in script
    assert str(icon) in script


def test_main_reports_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(quick_launch, "_is_windows", lambda: False)

    exit_code = quick_launch.main([str(tmp_path / "replybot.exe")])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Failed to create Quick Launch shortcut" in captured.err
