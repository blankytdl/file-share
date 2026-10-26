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

## Using it
Put both devices on the same hotspot / Wi-Fi / USB-tether link, open the app on both, tap Refresh/Scan, select the other
device, then send files, a folder, an app (Android) or text. Optional: set the same PIN on both devices in Settings.
Interrupted transfers resume automatically; Android keeps receiving in the background (foreground service).

## Security note
Transfers are NOT encrypted (by design, for personal use). Files, text and the PIN travel as plain TCP, so anyone on the same
network could read them. Use your own hotspot or a network you trust. Every file is still checksum-verified on arrival.
