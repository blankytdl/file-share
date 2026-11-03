# HotShare — PC <-> Android file sharing over hotspot / Wi-Fi / USB tethering / Ethernet

    HotShare/
      .github/workflows/build.yaml   one workflow that builds BOTH apps
      windows/                       PC app (Python + tkinter): hotshare_core.py + hotshare_desktop.py
      android/                       Android app (Kotlin, Android Studio / Gradle project)

## Build both on GitHub (no installs needed)
1. Create a GitHub repo and upload everything in this folder (keep the `.github` folder).
2. Open the repo's **Actions** tab -> *Build HotShare* -> **Run workflow** (it also runs on every push).
3. When it turns green, open the run and download **HotShare-windows-exe** and **HotShare-android-apk** from *Artifacts*.
4. Windows: run HotShare.exe and allow it through the firewall (private networks).
5. Android: unzip the artifact first (GitHub wraps it in a .zip), copy `app-release.apk` to the phone, allow "install unknown apps" for your file manager, tap it.
   **If you installed an earlier HotShare build, uninstall it first** (older builds were signed with a different random key, so Android refuses to update them).
   From now on every build uses the same signing key (`android/hotshare.keystore`), so new builds install over old ones. Needs Android 8.0 or newer.

## Run without building
- PC: `python windows/hotshare_desktop.py` (Python 3.12+, no extra packages)
- Android: open the `android` folder in Android Studio and press Run.

## Choosing where files are saved
Both apps have a **Receive into** card on the main screen: **Change folder** opens the system folder chooser, **Open folder** opens it in your file manager.
On the PC you can also pick a different folder for one transfer in the Accept dialog, and when sending you can name a subfolder to create on the other device.

## What's in the apps
- **PC:** sidebar (devices, receive-into folder, themes) + main area with a **drag-and-drop** zone, **Ctrl+V / Paste** (files copied in Explorer, or text),
  an editable **To send** list (remove one item or clear all), sticky Send bar, and transfer cards with an overall bar **and a progress bar for every file**
  (for both sending and receiving). Ten colour themes (Midnight, Crimson, **Redline** red+black, **Mono Dark** black+white, **Mono Light** white+black, Ocean, Emerald, Sunset, Orchid, Daylight), sharp text on high-DPI screens,
  a title bar coloured to match the theme (Windows 10/11).
- **Android:** the same send list, Paste button, per-file progress and themes; Send bar stays on screen.

## Reliability: pause, resume, power cuts, cache
- **Pause / Resume** on every transfer card (play/pause button). Pausing keeps the partial data; Resume continues from the exact byte.
  The receiver can pause too: the sender then waits and carries on by itself when the receiver resumes.
- **Dropped connection / sleeping phone / Wi-Fi hiccup:** both apps reconnect automatically for 15 minutes (Settings > "Keep reconnecting for")
  and continue where they stopped. A transfer you already accepted is never asked about again on reconnect.
- **Power cut or crash:** unfinished sends are saved. On the next start they appear as **paused** cards with a Resume button.
  Android can resume files you picked with the file chooser or the folder chooser, and apps; things pasted from other apps cannot be reopened after a restart.
- **X** on a running transfer stops it and erases its cached data. **Cache** (partial files of unfinished transfers) is erased automatically
  as soon as a transfer succeeds, and also with the **Clear cache** button (Transfers header and Settings).
- **Android quits by itself:** with the app closed and nothing transferring, the background service stops after "Quit when idle" minutes
  (default 3; 0 = never) and the app exits. Open it again to receive.
- Faster: large network buffers, disk + hashing overlapped with sending on the PC, Wi-Fi high-performance lock on Android, light animations while a transfer runs.
- Files named with characters Windows refuses (`: ? " < > |`) are saved with `_` instead of failing; Windows antivirus locks no longer fail a finished file.

## Cancel one file (PC app)
Every file row in a running transfer card has its own small **X**. It skips just that file; the rest of the transfer carries on.
- **Receiving:** the PC drops the connection, the sender reconnects by itself (a few seconds) and is told that file is "done", so it never sends it. Works with any sender.
- **Sending:** the cancelled file (and files already delivered) are left out and the rest is sent as a fresh, smaller transfer. The phone may ask you to Accept again unless the PC is trusted/auto-accept there, and may keep a small partial file of the cancelled one until its cache is cleared.
- The big X on the card still cancels the whole transfer. Skipped rows show "Skipped" in amber (never as an error).

## "'utf-8' codec can't decode byte ... invalid continuation byte" (fixed)
That message meant the PC expected a checksum line after a file but received raw file bytes: the two apps had lost step with each other after a reconnect
(typically a phone running an older HotShare build that ignores the receiver's "done" list and resends files the PC already saved). It then repeated on every reconnect.
Now the PC checks the checksum line instead of decoding it, switches that device to a compatibility mode for the resend, and if one particular file keeps
disagreeing with its announced size it skips only that file (shown as Skipped) and finishes the rest. Updating the phone app to the build in this folder is still recommended.

## Checksum mismatch on a slow or unstable connection (fixed)
When a connection stalls, the phone gives up and reconnects, but the old connection can still deliver bytes it had buffered. The PC used to let both write into the same
partial file, which corrupted it ("checksum mismatch") and could repeat. Now the newest connection of a transfer always wins: an older one is stopped, and a stale one
whose header arrives late is refused. A file that still fails its checksum after one clean resend is skipped (shown as Skipped) instead of looping.

## Automatic repair
- **Damaged partial files:** while a file arrives, the PC saves a checkpoint (position + checksum) every 4 MB. Before resuming, it re-checks the partial file; if anything
  was changed or lost (power cut, antivirus, disk error) it keeps the verified part, drops the rest and downloads only that again. The card log shows "Repaired ...".
- **Failed checksum:** the file is downloaded again from scratch, up to 3 attempts, then only that file is skipped.

## Block verification (damage found the moment it arrives)
A new sender announces `"caps":["blk"]` in its header; the receiver answers with `"blk": 4194304` and the sender then writes a SHA-256 line after every 4 MiB block
(block boundaries are absolute file offsets). The receiver checks each block as it arrives. If one is damaged it keeps everything before it, cuts the partial file back to
the start of that block and the sender resumes from there - one bad block costs 4 MB, not the whole video. The same block failing 5 times (or 40 repairs in one file)
skips only that file. Senders that don't announce `blk` (older apps) keep working exactly as before with the whole-file checksum only.
The phone side lives in `Core.kt` (`sendStream`) - rebuild the APK to get it. The Windows app shows the build tag `r7-blockrepair` in its title.

## If the phone cannot find or send to the PC (but the PC finds the phone)
That is almost always **Windows Firewall** on the PC (hotspot networks count as "Public", which the default prompt does not cover).
HotShare now handles it two ways:
1. **One-click fix:** on Windows the app notices the missing rule, shows a warning bar and offers *Allow in Windows Firewall* (one Windows approval prompt;
   also in Settings). It replaces any blocking rules with a single allow rule for all networks.
2. **Works even without the fix:** the PC knocks on every phone it finds every few seconds. The phone then lists the PC by itself, and when you send to
   it and the PC can't be reached directly, the phone waits and the PC **pulls** the files over a connection the PC opens. Keep HotShare open on the PC.
Other checks: both devices on the same network; Android is pinned to the Wi-Fi even if it has no internet; use **Add IP** as a last resort.

## Transfers that stop when the phone sleeps
Android cuts network access for background apps when the screen turns off. HotShare asks once to be allowed to ignore battery optimisation
(also in Settings > "Allow background activity"). On Xiaomi/Huawei/Samsung also set HotShare to "Unrestricted" battery use.

## "It restarted at 100%" (fixed)
After the last byte the receiver verifies, saves and virus-scans the file, which can take minutes for big files. The sender used to treat the wait as a
dead connection, reconnect and send everything again. Now the receiver remembers files it has already saved (a reconnect skips them), and the sender waits
patiently for the final "DONE".

## Using it
Put both devices on the same hotspot / Wi-Fi / USB-tether link, open the app on both, tap Refresh/Scan, select the other
device, then send files, a folder, an app (Android) or text. Optional: set the same PIN on both devices in Settings.
Interrupted transfers resume automatically; Android keeps receiving in the background (foreground service).

## Security note
Transfers are NOT encrypted (by design, for personal use). Files, text and the PIN travel as plain TCP, so anyone on the same
network could read them. Use your own hotspot or a network you trust. Every file is still checksum-verified on arrival.
