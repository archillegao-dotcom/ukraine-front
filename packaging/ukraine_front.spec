# PyInstaller spec — build with:   python tools/make_seed.py && pyinstaller packaging/ukraine_front.spec --noconfirm
# (or simply: python packaging/build.py)
#
# onedir (not onefile): starts in ~1-3 s instead of unpacking ~200 MB to a temp dir on every launch,
# and on macOS the whole .app bundle is approved once by Gatekeeper (right-click > Open).
# Output:  dist/UkraineFront/            (Windows/Linux folder, run UkraineFront[.exe])
#          dist/UkraineFront.app         (macOS only)
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
SEED = ROOT / "build" / "seed_data"
if not (SEED / "daily_metrics.csv").exists():
    raise SystemExit("build/seed_data missing - run: python tools/make_seed.py")
VERSION = os.environ.get("UF_VERSION", "0.0.0").lstrip("v") or "0.0.0"

datas = [
    (str(ROOT / "ukraine_front" / "web"), "ukraine_front/web"),
    (str(SEED), "seed_data"),
]
# GDAL/PROJ support files shipped inside the pyogrio wheel (not collected by the stock hook);
# without them GDAL warns "Could not detect GDAL data files" and cannot resolve .prj CRSs.
import pyogrio
_pg = Path(pyogrio.__file__).parent
for sub in ("gdal_data", "proj_data"):
    if (_pg / sub).is_dir():
        datas.append((str(_pg / sub), f"pyogrio/{sub}"))
hiddenimports = (collect_submodules("uvicorn") + collect_submodules("ukraine_front")
                 + ["pyogrio._geometry", "pyogrio._io", "pyogrio._ogr", "pyogrio._vsi", "pyogrio._err",
                    # Windows has no system zoneinfo; zoneinfo imports tzdata only when it needs it.
                    "tzdata",
                    # BeautifulSoup(..., "lxml") selects the parser by name, which modulegraph misses.
                    "lxml", "lxml.etree",
                    "openpyxl", "certifi"])
# only the CLI daily report (render.py) uses matplotlib; the web app does not need it
excludes = ["ukraine_front.render", "ukraine_front.daily", "matplotlib", "tkinter", "_tkinter",
            "IPython", "jupyter", "notebook", "pytest", "PyQt5", "PyQt6", "PySide2", "PySide6",
            "PIL", "folium", "mapclassify", "scipy", "sqlalchemy", "psycopg2", "pyarrow"]
hiddenimports = [h for h in hiddenimports if not any(h == e or h.startswith(e + ".") for e in excludes)]

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
is_mac = sys.platform == "darwin"
icon = None
for cand in (ROOT / "packaging" / ("icon.icns" if is_mac else "icon.ico"),):
    if cand.exists():
        icon = str(cand)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="UkraineFront",
    console=not is_mac,          # Windows/Linux: console window shows status, closing it quits.
                                 # macOS: .app relaunches itself in Terminal (see launcher.py).
    upx=False,                   # UPX breaks codesigning on macOS and triggers AV false positives
    strip=False,
    argv_emulation=False,
    target_arch=None,            # native arch of the runner (arm64 on macos-14+, x86_64 on macos-13)
    codesign_identity=None,      # PyInstaller ad-hoc signs on macOS (required on Apple Silicon)
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="UkraineFront")

if is_mac:
    app = BUNDLE(
        coll,
        name="UkraineFront.app",
        icon=icon,
        bundle_identifier="org.ukrainefront.tracker",
        version=VERSION,
        info_plist={
            "CFBundleName": "UkraineFront",
            "CFBundleDisplayName": "俄乌前线追踪器",
            "CFBundleShortVersionString": VERSION,
            "LSUIElement": True,                 # no Dock icon for the short-lived launcher process
            "NSHighResolutionCapable": True,
            # Placeholder only. packaging/build.py rewrites this to the highest
            # minimum OS actually required by the linked binaries, then re-signs.
            "LSMinimumSystemVersion": "11.0",
        },
    )
