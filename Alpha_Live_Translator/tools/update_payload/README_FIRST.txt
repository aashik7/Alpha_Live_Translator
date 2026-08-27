ALPHA LIVE TRANSLATOR - UPDATE
==============================

WHAT THIS IS
------------
An update for Alpha Live Translator that is already installed on this machine.
It replaces the program files only. You do NOT uninstall anything, and you do
NOT need the original installer.


HOW TO RUN IT
-------------
1. Close Alpha Live Translator completely.
   The update will refuse to run while it is open, on purpose.

2. Extract this whole folder somewhere - the Desktop is fine.
   Do not run it from inside the zip preview window; that gives it nothing
   to copy and it will tell you so.

3. Double-click  Update_Alpha.bat

4. Read the last line. It says either

       Done. You can start Alpha Live Translator now.

   or it says the update did not complete, in which case NOTHING was changed
   and the app is exactly as it was.

5. Start Alpha Live Translator normally.


WHAT IS KEPT
------------
These are yours and are never touched:

  * your Deepgram and DeepL keys
  * your language setting
  * every saved session under troubleshooting\ - all transcripts, logs and
    recordings from previous runs

The previous version of the program files is also saved next to the app, in a
folder named app_backup_<date>-<time>. Delete it once you have confirmed the
app works.


IF SOMETHING GOES WRONG
-----------------------
The window stays open and prints exactly what happened. Copy that text and
send it to whoever gave you this update. Nothing is left half-applied: if any
part of the update fails, the previous version is put back automatically before
the window closes.


INSTALLED SOMEWHERE UNUSUAL?
----------------------------
By default the update looks in

  %LOCALAPPDATA%\Programs\Alpha Live Translator

If Alpha is installed elsewhere, drag that folder onto Update_Alpha.bat instead
of double-clicking it.
