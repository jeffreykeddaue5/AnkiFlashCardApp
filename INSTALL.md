# Running Anki Drill on another computer

Written for: you, setting this up fresh on a machine that has never seen this code.

There is no build step and no installer. Unzip it, install Qt, run one file.

## 1. Check your Python

You need Python 3.6 or newer. Any 3.x from 3.6 to 3.13 works.

```bash
python3 --version        # macOS / Linux
py --version             # Windows
```

If Windows says nothing is installed, get it from [python.org](https://www.python.org/downloads/)
and **tick "Add python.exe to PATH"** in the installer.

## 2. Unzip and enter the folder

```bash
unzip anki-drill.zip
cd anki-drill
```

## 3. Install Qt

A virtual environment keeps this off your system Python. Recommended but not required.

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell)**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell refuses to run the activate script, either use `.venv\Scripts\activate.bat`
from `cmd.exe`, or skip the venv entirely and just run `py -m pip install -r requirements.txt`.

### On Linux, prefer your package manager

`pip install PyQtWebEngine` sometimes fails to build on Linux. Distro packages are easier:

```bash
sudo apt install python3-pyqt5 python3-pyqt5.qtwebengine      # Debian / Ubuntu / Mint
sudo dnf install python3-qt5 python3-qt5-webengine            # Fedora
sudo pacman -S python-pyqt5 python-pyqtwebengine              # Arch
```

Then run without a venv (or create it with `python3 -m venv --system-site-packages .venv`
so it can see the distro packages).

## 4. Run it

```bash
python3 run.py           # macOS / Linux
py run.py                # Windows
```

## Finding your collection

The app searches the usual Anki locations on all three platforms:

| | |
|---|---|
| Linux | `~/.local/share/Anki2`, `~/.var/app/net.ankiweb.Anki/data/Anki2` (Flatpak), `~/snap/anki/...` |
| Windows | `%APPDATA%\Anki2` |
| macOS | `~/Library/Application Support/Anki2` |

Every profile it finds appears in the **Profile** dropdown. If yours is somewhere unusual,
click **Open…** and pick your `collection.anki2` by hand.

**Your home Anki does not need to be the same collection as anywhere else.** The app reads
whatever is on that machine. If you sync through AnkiWeb, sync in Anki first so the local
file is current, then hit `F5` here.

Anki can stay open while you use this — the app copies the collection to a temp file and
opens it read-only, so it cannot disturb Anki or corrupt anything.

## If it does not start

**`ModuleNotFoundError: No module named 'PyQt5'`** — step 3 did not take effect, or you are
running a different Python than the one you installed into. Re-activate the venv.

**The window opens but the card area is blank or the app crashes on launch** — QtWebEngine
is struggling with your graphics setup. Try:

```bash
QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-sandbox" python3 run.py
```

On Windows:

```powershell
$env:QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu"; py run.py
```

**`No module named 'PyQt5.QtWebEngineWidgets'`** — PyQtWebEngine is missing. The app will
still run in its reduced fallback mode, but install it for the real card.

**Korean, Japanese or Chinese text shows as boxes** — install a CJK font
(`sudo apt install fonts-noto-cjk`, or the Noto Sans CJK family from Google Fonts). Windows
and macOS ship with these already.

## Checking it works

```bash
python3 -m pytest test/ -q
```

148 tests. The GUI ones skip themselves automatically if Qt is unavailable.
