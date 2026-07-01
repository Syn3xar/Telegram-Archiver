# Telegram Admin GUI User Guide

Built by Syn3xar.

This guide explains how to use the Telegram admin GUI app.

The app is intended for authorized group administrators who need to:

- find messages from a specific account
- archive text chat
- download media
- preview a message before taking action
- delete a message with confirmation

Use it only in groups where you have permission to administer content.

## 1. Open the App

1. Open the folder where the app was provided.
2. Double-click the app.
3. Wait for the window named `Telegram Admin Tool` to appear.

The first launch can take a few seconds.

## 2. Fill In the Top Fields

At the top of the window, fill in the required fields.

### Admin User ID

Enter the Telegram user ID for the admin account that will perform the actions.

This is used as a safety check so the app does not run under the wrong account.

### Group ID

Enter the Telegram group or supergroup ID where the admin account has permission to operate.

### Target User ID

Enter the Telegram user ID for the account you want to search for, review, or verify before deletion.

### Phone

Enter the Telegram phone number for the admin account in international format.

### API ID and API Hash

Enter the Telegram API credentials assigned to the app.

If the app was prepared for your organization, these fields may already be filled in.

### Two-Step Verification Password

Enter the Telegram two-step verification password if the admin account uses one.

If the account does not use two-step verification, this field may not be needed.

### Batch Size

This controls how many messages the app reads from Telegram per page.

Recommended default:

```text
100
```

Use a smaller number such as `50` or `25` if Telegram disconnects often.

### Output Folder

Choose where reports, archives, and downloaded media should be saved.

Use the `Browse` button if you want to choose a different folder.

### Media Streams

- `Total streams` limits all simultaneous media downloads. The recommended value is `4`.
- `Small streams` limits files up to 20 MB. The recommended value is `4`.
- `Large streams` limits files over 20 MB or with unknown size. The recommended value is `2`.

The small and large values cannot exceed the total stream limit. Values from 1 to 20 are accepted.

## 3. First Login

When you start an action for the first time, Telegram may ask for a login code.

1. Click an action, such as `Search Target Messages`.
2. Telegram sends a code to the admin account.
3. The app shows a pop-up asking for the Telegram login code.
4. Enter the code.
5. If Telegram asks for two-step verification, enter the password.

After successful login, the app saves a local session so future launches usually do not need another code.

## 4. Search Target Messages

Use this to find messages sent by the target account.

1. Confirm the `Target User ID` field is correct.
2. Click `Search Target Messages`.
3. Watch the `Status and Text Screen` for progress.
4. Found messages appear in the `Detected Target Messages` table.

The table shows:

- message ID
- message date
- sender label
- media type
- reply reference
- message text

The message ID is important because it can be used for previewing or deleting a message.

The app also saves a search report in the selected output folder.

## 5. Preview a Message

Use this before deleting anything.

1. Click a row in the detected messages table.
2. The app fills the message ID field automatically.
3. Click `Preview Message`.
4. Read the preview in the `Status and Text Screen`.

The preview shows:

- message ID
- date
- sender ID
- sender name or label
- media type
- text or media preview

## 6. Delete a Message

Use this to delete a message by ID from the group.

Important: deletion can be permanent for the group.

1. Search for the target account's messages first.
2. Select the message row you want to delete.
3. Confirm the message ID field is correct.
4. Click `Delete Message`.
5. Confirm the warning pop-up.
6. Type exactly:

   ```text
   DELETE
   ```

7. The app checks that the sender matches the target user ID.
8. If the sender matches, the app sends the delete request.
9. The app asks Telegram again and reports whether the message is gone.

A successful confirmation reports that the message was deleted.

If the sender does not match the target user, the app refuses to delete by default.

Only use `Allow any sender` if you intentionally want to delete a message even when the sender is not the target account.

## 7. Archive Text Chat

Use this to save the group text conversation without downloading media.

1. Click `Archive Text Chat`.
2. Wait while the app scans messages.
3. Open the saved report from the selected output folder.

The archive includes text messages and message metadata.

## 8. Download Media

Use this to download media from the group.

1. Choose whether to check `Only target media`.
2. Click `Download Media`.
3. Wait while the app scans the visible group history and reports the queued media and remaining known size.
4. Watch each worker's progress in the `Status and Text Screen`.
5. The app saves files in a stable media folder inside the selected output folder.

If `Only target media` is checked, the app downloads media only from the target account.

If it is not checked, the app downloads media from all visible messages in the group.

The app also saves a media manifest with message IDs, sender IDs, media types, and saved locations.
When the task is run again, complete files are skipped. Temporary Telegram server errors, flood waits,
and cancelled transfers are retried without ending the other workers. Files that still fail remain
pending for a later run.

## 9. Stop a Long Task

Use `Stop Current Task` if a scan is taking too long.

During a media run, active file transfers are cancelled cleanly and incomplete files are removed.

It may not stop instantly, but it should stop cleanly.

## 10. Understanding Output

The app can create:

- searchable message reports
- readable chat archives
- downloaded media folders
- media manifests for review

Use browser-readable reports for easy review.

Use spreadsheet-readable reports for filtering, sorting, or later audit work.

## 11. Troubleshooting

### Login Code Prompt

If Telegram asks for a code, enter the code received by the admin account.

### Two-Step Verification Prompt

If Telegram asks for two-step verification, enter the account password.

### Wrong Logged-In Account

If the app says the logged-in account does not match the admin user ID:

1. Close the app.
2. Remove the saved local session for the app if you intentionally need to log in again.
3. Reopen the app and log in with the correct admin account.

### No Messages Found

Check:

- the target user ID is correct
- the group ID is correct
- the admin account can see the group's history
- the target account may not have posted in the visible history

### Telegram Disconnects During a Scan

Try reducing `Batch Size` to:

```text
50
```

or:

```text
25
```

Then run the action again.

### Delete Fails

Possible reasons:

- the admin account does not have permission to delete that message
- the message ID is wrong
- the message is already deleted
- Telegram does not allow deleting that message

## 12. Recommended Safe Workflow

For message moderation:

1. Enter the admin user ID, group ID, and target user ID.
2. Click `Search Target Messages`.
3. Select the message from the table.
4. Click `Preview Message`.
5. Confirm it is the correct message.
6. Click `Delete Message`.
7. Type `DELETE`.
8. Check the confirmation line in the status screen.

This reduces the chance of deleting the wrong message.
