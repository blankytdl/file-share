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

## Why checksum errors come and go
They are not random bad luck; each has a cause that depends on the situation:
1. **File size:** damage on a link is proportional to bytes sent, so small files never fail and big videos often do. Block verification (above) now repairs only the damaged 4 MB.
2. **A stale connection:** after a stall the old connection can still deliver buffered bytes while the reconnect is already writing the same file. Fixed: the newest connection wins.
3. **The file changed on the phone** between the interruption and the resume (same name and size; the phone uses modified-time 0 when a file provider gives none, so the file id cannot tell).
   The sender now also sends the checksum of the part it skips and the PC compares it with its partial copy at once ("changed on the sender ... starting that file over") instead of
   downloading everything and failing at the end.
4. **A damaged partial file** (power loss, antivirus, disk): found by the checkpoints and cut back before resuming.
Build tag shown in the window title: `r8-staledetect`.

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

## r9: silent corruption guards, taskbar progress, completion summary (Windows app)
- **Saved file is verified from disk:** after a file arrives, the PC reads the saved file back and compares its SHA-256 and size with the sender's. Before, only the bytes that came off the network were hashed. Turn off in Settings ("Re-check each saved file...") or with `"verify_disk": false` if big files finalize too slowly.
- **Short disk writes can no longer go unnoticed** (the receiver used to ignore how many bytes a raw write actually stored).
- **A file that changes while the PC is sending it** (size or modified time differs afterwards) now produces a warning in the completion summary (it never blocks the transfer).
- **Taskbar icon progress:** green while transferring, yellow while waiting/reconnecting, red for a few seconds after a failure.
- **Completion summary window:** lists every file as done / failed / skipped, "Open file location" (per file and for the whole transfer), and, for sends from the PC, "Send N failed again". The receiver also tells the sender which files it gave up on (`"failed"` in the OK reply; older senders ignore it).
- Not changed: the Android app (Core.kt / MainActivity.kt).

## r11: repair only the files that fail (like a RAR "recovery record", on demand)
- Files are sent normally. The PC checks every 4 MiB block as it arrives. A file whose blocks arrive damaged is **held**; the other files carry on. When everything else is done the PC asks the sender for **recovery data for those files only** (`REPAIR {"files":[...]}`), the sender builds it by re-reading just those files (one parity block per 32 blocks, about 3%, each with its own SHA-256) and the PC **rebuilds each damaged block itself**, checks it against the block checksum and saves the file. Files that arrive intact cost nothing extra.
- **Limit:** one damaged block per group of 32 blocks (128 MiB). With more damage than that, or with unusable recovery data, only that file is fetched again from its first bad block (the old behaviour).
- **Which files:** Settings -> "Repair files up to (GB)" (default 1.0; files of one block, 4 MiB, or less are simply re-sent) and the "Repair files that arrive damaged..." switch. Config keys `rec_max_gb` and `recovery`.
- **End screen:** repaired files are listed in amber with how many blocks were rebuilt, and the header says "N repaired with recovery data". When the PC is the sender, the screen notes which files the receiver repaired.
- Works phone -> PC and PC -> PC (the Android sender answers `REPAIR` in `Core.kt`, `sendParity`). PC -> phone has no recovery data (the Android receiver does not ask for it) and still re-sends damaged blocks.
- Negotiated: a sender announces `"caps":["blk","rep"]`, the receiver answers `"rep":32` (the r10 build used `rec` with a different meaning; r11 ignores it, so mixed versions fall back to plain block re-send). Older apps on either side keep working unchanged.
- Not the .rar format: this is HotShare's own parity (XOR), so WinRAR cannot open or repair it.
- Android: on first launch the app now asks where received files should be saved (it used to use a fixed folder, hidden under Android/data on Android 8-9). "Receive into -> Change folder" changes it any time.
- Earlier r10 bug-check fixes (failed repair fetches only the bad block, legacy peers, taskbar retry) are included.
- r11b bug-check fixes: capability renamed to `rep` so an r10 phone build can never be answered with the new protocol; the receiver waits until everything received is on disk before it touches a partial file for repair (race); the REPAIR request is capped at 1500 files per round (the rest are re-fetched from their first bad block).
- r11c bug-check fixes: when a second damaged block lands in the same group of 32 blocks (recovery data cannot fix that) the PC stops at once and fetches from the first bad block instead of receiving the rest of the file first; files held for repair are cut back to their first damaged block whenever a connection fails, so the next connection continues from there instead of restarting the file.
- r11d bug-check fix: the PC kept no record of the files it sent (a statement had slipped into a comment), so the summary window never showed "Send N failed again" or the per-file "open location" buttons for sends from the PC. Both work now; the retry was tested by clicking it.
