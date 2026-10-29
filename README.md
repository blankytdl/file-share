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
  (for both sending and receiving). Six colour themes (Midnight, Ocean, Emerald, Sunset, Orchid, Daylight), sharp text on high-DPI screens,
  a title bar coloured to match the theme (Windows 10/11).
- **Android:** the same send list, Paste button, per-file progress and themes; Send bar stays on screen.

## Reliability: pause, resume, power cuts, cache
- **Pause / Resume** on every transfer card (play/pause button). Pausing keeps the partial data; Resume continues from the exact byte.
  The receiver can pause too: the sender then waits and carries on by itself when the receiver resumes.
- **Dropped connection / sleeping phone / Wi-Fi hiccup:** both apps reconnect automatically for 5 minutes (Settings > "Keep reconnecting for")
  and continue where they stopped. A transfer you already accepted is never asked about again on reconnect.
- **Power cut or crash:** unfinished sends are saved. On the next start they appear as **paused** cards with a Resume button.
  Android can resume files you picked with the file chooser or the folder chooser, and apps; things pasted from other apps cannot be reopened after a restart.
- **X** on a running transfer stops it and erases its cached data. **Cache** (partial files of unfinished transfers) is erased automatically
  as soon as a transfer succeeds, and also with the **Clear cache** button (Transfers header and Settings).
- **Android quits by itself:** with the app closed and nothing transferring, the background service stops after "Quit when idle" minutes
  (default 3; 0 = never) and the app exits. Open it again to receive.
- Faster: large network buffers, disk + hashing overlapped with sending on the PC, Wi-Fi high-performance lock on Android, light animations while a transfer runs.
- Files named with characters Windows refuses (`: ? " < > |`) are saved with `_` instead of failing; Windows antivirus locks no longer fail a finished file.

## If "Scan" finds nothing
1. Both devices on the same hotspot / Wi-Fi (or USB tether).
2. **Windows Firewall:** the first time HotShare.exe runs, allow it on *Private* networks (UDP 45454 + TCP 45455). If you clicked Cancel, re-allow it in
   Windows Security > Firewall > Allow an app.
3. Android now pins itself to the Wi-Fi network (phones otherwise send app traffic over mobile data when the Wi-Fi has no internet) and, if broadcasts
   are blocked, automatically probes every address on the network. You can also tap **Add IP** and type the PC's address (shown under the HotShare title).

## Using it
Put both devices on the same hotspot / Wi-Fi / USB-tether link, open the app on both, tap Refresh/Scan, select the other
device, then send files, a folder, an app (Android) or text. Optional: set the same PIN on both devices in Settings.
Interrupted transfers resume automatically; Android keeps receiving in the background (foreground service).

## Security note
Transfers are NOT encrypted (by design, for personal use). Files, text and the PIN travel as plain TCP, so anyone on the same
network could read them. Use your own hotspot or a network you trust. Every file is still checksum-verified on arrival.
