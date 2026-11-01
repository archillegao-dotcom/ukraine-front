"""Entry point of the packaged (PyInstaller) app — also runnable from source: python launcher.py

* read-only assets (ukraine_front/web, seed data) come from the bundle (sys._MEIPASS when frozen);
* writable data lives in a per-user folder, seeded from the bundle on first run:
    macOS   ~/Library/Application Support/UkraineFront
    Windows %APPDATA%\\UkraineFront
    Linux   ~/.local/share/UkraineFront   ($XDG_DATA_HOME respected)
  (override with UF_HOME or --home);
* picks a free port (8765 preferred), starts the server, opens the default browser;
* a second launch while the app is already running just opens the browser again.

The normal source workflow (start.sh / start.bat -> python -m ukraine_front.app) is unchanged.
"""
import argparse
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "UkraineFront"
TITLE = "俄乌前线 OSINT 追踪器"
PREFERRED_PORT = 8765
FROZEN = getattr(sys, "frozen", False)
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _fix_stdio():
    # windowed builds may have no console at all; never crash on print()
    for name in ("stdout", "stderr"):
        st = getattr(sys, name)
        if st is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            continue
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def user_home(override=None):
    if override or os.environ.get("UF_HOME"):
        return Path(override or os.environ["UF_HOME"]).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME


def seed_dir():
    for p in (BUNDLE / "seed_data", BUNDLE / "build" / "seed_data"):
        if p.is_dir():
            return p
    return None


def copy_seed(seed, data):
    """Copy bundled seed files that the user folder does not have yet (never overwrites)."""
    n = 0
    for src in seed.rglob("*"):
        if not src.is_file():
            continue
        dst = data / src.relative_to(seed)
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".seedtmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        n += 1
    return n


def _is_our_server(port, timeout=1.5):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=timeout) as r:
            return r.status == 200 and "schedule" in json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


def _bind(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
    else:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(128)
        return s
    except OSError:
        s.close()
        return None


def bind_port(preferred, strict=False):
    """Bind the server socket ourselves (no race). Preferred port, then the next 20, then any."""
    cands = [preferred] if strict else [preferred] + list(range(preferred + 1, preferred + 21)) + [0]
    for p in cands:
        s = _bind(p)
        if s is not None:
            return s, s.getsockname()[1]
    raise SystemExit(f"[!] 端口 {preferred} 已被占用，无法启动。 / Port {preferred} is busy.")


def _open_browser_when_ready(url, port):
    for _ in range(240):
        if _is_our_server(port, timeout=1):
            break
        time.sleep(0.5)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


def _terminal_relaunch_script(executable, argv):
    """Shell script Terminal.app will actually execute.

    `open -a Terminal <mach-o>` does not reliably run a raw binary. A `.command` file does,
    and `exec` keeps the server in that window so closing the window quits the app.
    `--no-terminal` is passed through so the new process cannot relaunch itself again.
    """
    args = [x for x in argv if not str(x).startswith("-psn")]
    if "--no-terminal" not in args:
        args.append("--no-terminal")
    cmd = " ".join(shlex.quote(p) for p in [str(executable), *args])
    return "#!/bin/bash\nexec " + cmd + "\n"


def _mac_relaunch_in_terminal(home):
    """Finder starts a .app with no console. Hand the inner binary to Terminal so the user sees
    status messages and can quit by closing the window. Returns True if Terminal took over."""
    if not (FROZEN and sys.platform == "darwin" and ".app/Contents/MacOS/" in sys.executable):
        return False
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            return False  # already running inside a terminal (or the .command child)
    except Exception:  # noqa: BLE001
        pass
    marker = home / ".terminal_relaunch"
    try:
        if marker.exists() and time.time() - marker.stat().st_mtime < 20:
            return False  # loop guard: Terminal start did not give us a tty
        home.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(time.time()))
        script = home / "launch.command"
        script.write_text(_terminal_relaunch_script(sys.executable, sys.argv[1:]), encoding="utf-8")
        os.chmod(script, 0o755)
        return subprocess.run(["open", "-a", "Terminal", str(script)], timeout=30).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _pause_on_error():
    if FROZEN and os.name == "nt":
        try:
            input("按回车键关闭窗口 / Press Enter to close ...")
        except Exception:  # noqa: BLE001
            pass


def main():
    _fix_stdio()
    ap = argparse.ArgumentParser(description=TITLE)
    ap.add_argument("--port", type=int, default=None, help="端口（默认 8765，被占用时自动换一个）")
    ap.add_argument("--home", default=None, help="数据目录（默认按系统放在用户目录）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--no-terminal", action="store_true", help="macOS：不在终端窗口中重新打开")
    a, _unknown = ap.parse_known_args()   # ignore e.g. macOS -psn_* arguments

    home = user_home(a.home)
    info_file = home / "server.json"
    # A second click should only focus the browser, not open another Terminal window.
    try:
        old = json.loads(info_file.read_text(encoding="utf-8"))
        if _is_our_server(int(old["port"])):
            url = f"http://localhost:{old['port']}/"
            print(f"{TITLE} 已在运行：{url}（已为你打开浏览器）")
            if not a.no_browser:
                webbrowser.open(url)
            time.sleep(3)
            return
    except Exception:  # noqa: BLE001
        pass
    if not a.no_terminal and _mac_relaunch_in_terminal(home):
        return
    if a.no_terminal:
        # Smoke tests and the Terminal child take this path. The line is ASCII on purpose
        # so CI can grep the redirected log without depending on a locale.
        print(">> --no-terminal: staying in this process", flush=True)
    data = home / "data"
    data.mkdir(parents=True, exist_ok=True)
    os.environ["UF_DATA_DIR"] = str(data)
    os.environ.setdefault("UF_OUTPUT_DIR", str(home / "output"))

    seed = seed_dir()
    if seed is not None:
        n = copy_seed(seed, data)
        if n:
            print(f">> 首次运行：已将内置数据（{n} 个文件）复制到 {data}")
    else:
        print(">> 未找到内置种子数据（从源码运行？），数据将在线下载。")

    env_port = os.environ.get("UF_PORT")
    port_req = a.port or (int(env_port) if env_port else None)
    sock, port = bind_port(port_req or PREFERRED_PORT, strict=port_req is not None)
    url = f"http://localhost:{port}/"
    info_file.write_text(json.dumps({"port": port, "pid": os.getpid(), "started": time.time()}),
                         encoding="utf-8")

    print("=" * 60)
    print(f"  {TITLE} 已启动")
    print(f"  浏览器地址：{url}")
    print(f"  数据目录：{data}")
    print("  后台会自动更新数据；首次启动需联网下载最新快照，请稍候。")
    print("  ★ 关闭此窗口即可退出（或按 Ctrl+C）。")
    print("=" * 60, flush=True)

    if not a.no_browser:
        threading.Thread(target=_open_browser_when_ready, args=(url, port), daemon=True).start()

    import uvicorn
    from ukraine_front.app import app
    try:
        uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])
    finally:
        try:
            if json.loads(info_file.read_text(encoding="utf-8")).get("pid") == os.getpid():
                info_file.unlink()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        if e.code not in (None, 0):
            print(e.code if isinstance(e.code, str) else f"[!] exit {e.code}")
            _pause_on_error()
            sys.exit(1)
        raise
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, KeyboardInterrupt):
            sys.exit(0)
        import traceback
        traceback.print_exc()
        print(f"[!] 启动失败：{e}")
        _pause_on_error()
        sys.exit(1)
