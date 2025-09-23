# Quick Launch shortcut helper

The `quick_launch` module now ships with a small command line interface so you
can create the ReplyBot Quick Launch shortcut either directly with Python or by
building a standalone Windows executable.

## Run the helper with Python

1. Install Python 3.9 or later on your Windows machine.
2. Download the ReplyBot source (or copy `quick_launch.py`) and open a
   PowerShell prompt in that folder.
3. Execute the helper:

   ```powershell
   python -m quick_launch "C:\Path\To\ReplyBot\replybot.exe" `
       --name "ReplyBot" `
       --arguments "--config C:\Path\To\config.yaml" `
       --icon "C:\Path\To\replybot.ico"
   ```

   Only the target executable is required.  The optional switches let you pick
   the label shown in the Quick Launch bar, extra command line arguments,
   working directory, icon, or even an alternative Quick Launch folder.
4. On success the command prints the path to the created `.lnk` shortcut.  The
   shortcut is immediately available from the Windows taskbar.

## Package a standalone `.exe`

If you prefer a double-click experience you can turn the helper into a Windows
executable with [PyInstaller](https://pyinstaller.org/):

1. Install the packager:

   ```powershell
   python -m pip install pyinstaller
   ```

2. Build the utility:

   ```powershell
   pyinstaller --onefile --name ReplyBotQuickLaunch quick_launch.py
   ```

   The resulting `ReplyBotQuickLaunch.exe` will live in the `dist` folder.

3. Copy the generated `.exe` to your target machine.  It accepts the same
   command line options as the Python version and can be run from PowerShell or
   Command Prompt without Python installed.

> **Tip:** pass the ReplyBot executable path directly on the command line, e.g.
> `ReplyBotQuickLaunch.exe "C:\ReplyBot\replybot.exe"`.
