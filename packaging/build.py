"""One-command local build:  python packaging/build.py   (needs: pip install -r requirements.txt pyinstaller)

Produces dist/UkraineFront/ (+ dist/UkraineFront.app on macOS) and a zip in dist/:
  UkraineFront-<os>-<arch>.zip

On macOS the zip is made with ditto so the .app's symlinks and code signature survive.
LSMinimumSystemVersion is set from the binaries that were actually linked in, then the
bundle is ad-hoc signed again (editing Info.plist invalidates the signature PyInstaller made).
"""
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
}


def run(*cmd):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def _ver_tuple(text):
    parts = []
    for piece in text.split("."):
        if not piece.isdigit():
            return None
        parts.append(int(piece))
    return tuple(parts) if parts else None


def _norm(parts):
    parts = tuple(parts[:3])
    return parts + (0,) * (3 - len(parts))


def format_minos(parts):
    """'11.0', '10.15', or '14.1.2' — always at least major.minor."""
    major, minor, patch = _norm(parts)
    if patch:
        return f"{major}.{minor}.{patch}"
    return f"{major}.{minor}"


def parse_otool_minos(text):
    """Minimum OS versions declared by one Mach-O (every slice).

    LC_BUILD_VERSION's `version` field is the linker version, not the OS.
    Only `minos` counts there. Older binaries use LC_VERSION_MIN_MACOSX.
    """
    mins = []
    cmd = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("cmd "):
            cmd = stripped.split(None, 1)[1]
            continue
        if cmd == "LC_BUILD_VERSION" and stripped.startswith("minos "):
            parsed = _ver_tuple(stripped.split(None, 1)[1])
        elif cmd == "LC_VERSION_MIN_MACOSX" and stripped.startswith("version "):
            parsed = _ver_tuple(stripped.split(None, 1)[1])
        else:
            continue
        if parsed and parsed[0] > 0:
            mins.append(parsed)
    return mins


def scan_minos(app):
    """Return (version_tuple, relative_path) for the binary with the highest minimum OS."""
    best = None
    best_path = None
    ranked = []
    for path in app.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            magic = path.read_bytes()[:4]
        except OSError:
            continue
        if magic not in _MACHO_MAGICS:
            continue
        proc = subprocess.run(["otool", "-l", str(path)], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            continue
        found = parse_otool_minos(proc.stdout)
        if not found:
            continue
        top = max(found, key=_norm)
        rel = str(path.relative_to(app))
        ranked.append((_norm(top), rel))
        if best is None or _norm(top) > _norm(best):
            best, best_path = top, rel
    if best is None:
        raise SystemExit("could not read a minimum macOS version from the .app (otool found no Mach-O minos)")
    ranked.sort(reverse=True)
    print("highest minimum macOS versions in the bundle:", flush=True)
    for ver, rel in ranked[:8]:
        print(f"  {format_minos(ver):8} {rel}", flush=True)
    return best, best_path


def set_minimum_system_version(app):
    version, source = scan_minos(app)
    text = format_minos(version)
    plist_path = app / "Contents" / "Info.plist"
    with plist_path.open("rb") as fh:
        plist = plistlib.load(fh)
    plist["LSMinimumSystemVersion"] = text
    with plist_path.open("wb") as fh:
        plistlib.dump(plist, fh)
    print(f"LSMinimumSystemVersion={text} required by {source}", flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"- LSMinimumSystemVersion **{text}** (required by `{source}`)\n")
    return text


def main():
    extra = sys.argv[1:]
    if sys.platform == "darwin":
        # Don't let a from-source wheel inherit the runner's macOS (macos-latest is 26)
        # as its deployment target. Prebuilt wheels keep the version they were built with;
        # the scan below reports the real requirement.
        os.environ.setdefault("MACOSX_DEPLOYMENT_TARGET", "11.0")
    run(sys.executable, "tools/make_seed.py", *extra)
    run(sys.executable, "-m", "PyInstaller", "packaging/ukraine_front.spec", "--noconfirm", "--clean")
    osname = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower())
    dist = ROOT / "dist"
    out = dist / f"UkraineFront-{osname}-{arch}.zip"
    out.unlink(missing_ok=True)
    if sys.platform == "darwin":
        app = dist / "UkraineFront.app"
        set_minimum_system_version(app)
        # Info.plist was rewritten; the previous ad-hoc signature is now invalid.
        # Apple Silicon refuses to start an unsigned/broken-signature bundle.
        run("codesign", "--force", "--deep", "--sign", "-", str(app))
        run("codesign", "--verify", "--deep", "--verbose=2", str(app))
        # ditto keeps the symlinks and code signature inside the .app intact (zip -r would not)
        run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(out))
    else:
        shutil.make_archive(str(out.with_suffix("")), "zip", dist, "UkraineFront")
    print(f"built {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
