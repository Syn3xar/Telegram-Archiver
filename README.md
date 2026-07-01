# Telegram Archiver

Telegram Archiver is a Windows-friendly GUI tool for authorized Telegram group administrators.

Built by Syn3xar.

It can:

- search messages sent by a target Telegram user ID
- show message IDs in a table
- archive text-only chat to browser-readable reports
- download media with resumable, bounded parallel workers
- preview a message by message ID
- delete a message with sender verification and confirmation

Use this tool only for groups where you have permission to administer content.

## Requirements

- Windows
- Python 3.11 or newer for source runs
- Telegram API credentials from Telegram's developer portal
- A Telegram account with the necessary admin permissions in the target group

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Run From Source

```powershell
python .\src\telegram_admin_gui_app.py
```

Fill in the app fields:

- Admin user ID
- Group ID
- Target user ID
- Phone
- API ID
- API hash
- Two-step verification password, if required
- Total, small-file, and large-file stream limits
- Output folder

The recommended media settings are `4` total streams, `4` small-file
streams, and `2` large/unknown-file streams. The downloader scans the visible
group history first, reports the remaining known size, and then downloads into
a stable media folder. Completed files are skipped on later runs.

Temporary Telegram server errors, flood waits, and transfer cancellations are
handled per worker. A failed item remains pending without ending the other
downloads, and concurrency is reduced automatically when repeated failures
occur.

## Build a Windows EXE

Build:

```powershell
.\scripts\build_windows_exe.ps1
```

The executable is written to:

```text
dist\TelegramAdminGUI.exe
```

## Download a GitHub-Built EXE

GitHub Actions builds the Windows executable automatically on pushes to `main` and when the workflow is run manually.

To download it:

1. Open the repository on GitHub.
2. Go to **Actions**.
3. Open the latest **Build Windows GUI EXE** run.
4. Download the `TelegramAdminGUI-windows` artifact.

To publish the executable as a GitHub Release asset, create and push a version tag:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

The release workflow attaches `TelegramAdminGUI.exe` to the tag release.

## User Guide

See [docs/USER_GUIDE.md](docs/USER_GUIDE.md) or open [docs/USER_GUIDE.html](docs/USER_GUIDE.html) in a browser.

## Security Notes

Do not commit:

- Telegram session files
- API credentials
- two-step verification passwords
- downloaded media
- generated reports containing private chat content

The `.gitignore` excludes common local output and session files.
