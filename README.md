# HotShare — PC <-> Android file sharing over hotspot / Wi-Fi / USB tethering / Ethernet

    HotShare/
      .github/workflows/build.yaml   one workflow that builds BOTH apps
      windows/                       PC app (Python + tkinter): hotshare_core.py + hotshare_desktop.py
      android/                       Android app (Kotlin, Android Studio / Gradle project)

## Build both on GitHub (no installs needed)
1. Create a GitHub repo and upload everything in this folder (keep the `.github` folder).
2. Open the repo's **Actions** tab -> *Build HotShare* -> **Run workflow** (it also runs on every push).
3. When it turns green, open the run and download **HotShare-windows-exe** and **HotShare-android-apk** from *Artifacts*.
4. Android: copy the APK to the phone and allow "install unknown apps". Windows: run HotShare.exe and allow it through the firewall (private networks).

## Run without building
- PC: `python windows/hotshare_desktop.py` (Python 3.12+, no extra packages)
- Android: open the `android` folder in Android Studio and press Run.

## Using it
Put both devices on the same hotspot / Wi-Fi / USB-tether link, open the app on both, tap Refresh/Scan, select the other
device, then send files, a folder, an app (Android) or text. Optional: set the same PIN on both devices in Settings.
Interrupted transfers resume automatically; Android keeps receiving in the background (foreground service).

## Security note
Transfers are NOT encrypted (by design, for personal use). Files, text and the PIN travel as plain TCP, so anyone on the same
network could read them. Use your own hotspot or a network you trust. Every file is still checksum-verified on arrival.
