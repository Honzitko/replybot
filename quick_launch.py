"""Utilities for integrating ReplyBot with the Windows Quick Launch bar.

The functions in this module generate Windows shell shortcuts (``.lnk`` files)
so that the desktop application can be launched from the Quick Launch area of
Windows taskbars.  The implementation intentionally avoids pywin32
dependencies; instead it shells out to PowerShell which is available on
supported Windows versions.  The PowerShell invocation is structured in a way
that makes the behaviour straightforward to test on non-Windows platforms.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Sequence

__all__ = ["QuickLaunchError", "create_quick_launch_icon", "resolve_quick_launch_dir"]


class QuickLaunchError(RuntimeError):
    """Raised when the Quick Launch shortcut cannot be created."""


_INVALID_FILENAME_CHARS = set('<>:"/\\|?*')


def _is_windows() -> bool:
    """Return ``True`` when running on Windows."""

    return os.name == "nt"


def _ps_quote(value: str) -> str:
    """Return PowerShell-safe single quoted string literal."""

    return "'" + value.replace("'", "''") + "'"


def _powershell_executable() -> str:
    """Return the PowerShell executable to use."""

    override = os.environ.get("POWERSHELL")
    if override:
        return override

    for candidate in ("powershell", "pwsh"):
        located = shutil.which(candidate)
        if located:
            return located

    # Fall back to the legacy name; subprocess will surface an error later if
    # it is not available.  Using the simple string avoids hard failing on
    # Windows flavours where PowerShell is installed in a non-standard
    # location yet present on ``PATH``.
    return "powershell"


def _sanitize_filename(name: str) -> str:
    """Return ``name`` sanitised for use as a Windows filename."""

    cleaned = "".join("_" if ch in _INVALID_FILENAME_CHARS else ch for ch in name)
    cleaned = cleaned.strip()
    cleaned = cleaned.rstrip(" .")
    if not cleaned or not cleaned.strip("_"):
        raise QuickLaunchError("Shortcut name resolves to an empty file name.")
    return cleaned


def resolve_quick_launch_dir(
    override: Optional[os.PathLike[str] | str] = None,
) -> Path:
    """Return the Quick Launch directory, optionally honouring ``override``.

    The function first checks the optional ``override`` parameter, then the
    ``REPLYBOT_QUICK_LAUNCH_DIR`` environment variable which simplifies
    automated tests.  When neither is provided the path is derived from the
    ``APPDATA`` environment variable using the conventional Quick Launch
    location on modern Windows versions.
    """

    if override is not None:
        return Path(override).expanduser()

    env_override = os.environ.get("REPLYBOT_QUICK_LAUNCH_DIR")
    if env_override:
        return Path(env_override).expanduser()

    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise QuickLaunchError(
            "APPDATA environment variable is not set; cannot determine Quick Launch folder.",
        )

    return Path(appdata) / "Microsoft" / "Internet Explorer" / "Quick Launch"


def _build_powershell_script(
    link_path: Path,
    target_path: Path,
    *,
    arguments: Optional[str],
    description: Optional[str],
    working_dir: Optional[Path],
    icon_path: Optional[Path],
) -> str:
    """Return the PowerShell snippet that creates the shortcut."""

    pieces = [
        "$ErrorActionPreference = 'Stop'",
        "$shell = New-Object -ComObject WScript.Shell",
        f"$shortcut = $shell.CreateShortcut({_ps_quote(str(link_path))})",
        f"$shortcut.TargetPath = {_ps_quote(str(target_path))}",
    ]

    if description:
        pieces.append(f"$shortcut.Description = {_ps_quote(description)}")
    if arguments:
        pieces.append(f"$shortcut.Arguments = {_ps_quote(arguments)}")
    if working_dir:
        pieces.append(f"$shortcut.WorkingDirectory = {_ps_quote(str(working_dir))}")
    if icon_path:
        pieces.append(f"$shortcut.IconLocation = {_ps_quote(str(icon_path))}")

    pieces.append("$shortcut.Save()")
    return "; ".join(pieces)


def _run_powershell(command: Sequence[str]) -> None:
    """Execute PowerShell ``command`` raising :class:`QuickLaunchError` on failure."""

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise QuickLaunchError(
            f"PowerShell reported error creating Quick Launch shortcut: {stderr}",
        )


def create_quick_launch_icon(
    target_path: os.PathLike[str] | str,
    *,
    name: str = "ReplyBot",
    arguments: Optional[str] = None,
    description: Optional[str] = None,
    working_dir: Optional[os.PathLike[str] | str] = None,
    icon_path: Optional[os.PathLike[str] | str] = None,
    quick_launch_dir: Optional[os.PathLike[str] | str] = None,
) -> Path:
    """Create and return the path to a Windows Quick Launch shortcut."""

    if not _is_windows():
        raise QuickLaunchError("Quick Launch icon creation is only supported on Windows.")

    quick_launch_directory = resolve_quick_launch_dir(quick_launch_dir)
    quick_launch_directory.mkdir(parents=True, exist_ok=True)

    shortcut_name = _sanitize_filename(name)
    link_path = quick_launch_directory / f"{shortcut_name}.lnk"

    target = Path(target_path).expanduser()
    if not target.is_absolute():
        target = target.resolve()

    working = Path(working_dir).expanduser() if working_dir else None
    if working and not working.is_absolute():
        working = working.resolve()

    icon = Path(icon_path).expanduser() if icon_path else None
    if icon and not icon.is_absolute():
        icon = icon.resolve()

    ps_script = _build_powershell_script(
        link_path,
        target,
        arguments=arguments,
        description=description or name,
        working_dir=working,
        icon_path=icon,
    )

    powershell = _powershell_executable()
    command = [
        powershell,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        ps_script,
    ]
    _run_powershell(command)
    return link_path
