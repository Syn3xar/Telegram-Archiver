# Telegram Archiver

Telegram Archiver is a Windows-friendly GUI tool for authorized Telegram group administrators.

It can:

- search messages sent by a target Telegram user ID
- show message IDs in a table
- archive text-only chat to browser-readable reports
- download media from visible group history
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
- Output folder

## Build a Windows EXE

Install PyInstaller:

```powershell
python -m pip install pyinstaller
```

Build:

```powershell
.\scripts\build_windows_exe.ps1
```

The executable is written to:

```text
dist\TelegramAdminGUI.exe
```

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
