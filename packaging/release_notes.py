"""Release notes for the standalone zips.  python packaging/release_notes.py DIR"""
import plistlib
import sys
import zipfile
from pathlib import Path


def _plist(zpath):
    with zipfile.ZipFile(zpath) as zf:
        names = [
            n for n in zf.namelist()
            if n.endswith("UkraineFront.app/Contents/Info.plist")
            and "/._" not in n and not Path(n).name.startswith("._")
        ]
        if not names:
            return {}
        return plistlib.loads(zf.read(names[0]))


def _mb(path):
    return f"{path.stat().st_size / 1e6:.0f} MB"


def main():
    folder = Path(sys.argv[1])
    lines = [
        "免安装版，不需要安装 Python。请先解压再运行，不要在压缩包里直接双击。",
        "",
        "## macOS",
        "",
        "软件没有 Apple 开发者签名，也没有公证。从浏览器下载后，macOS 会拦截第一次打开（这是正常的，不是文件坏了）：",
        "",
        "1. 在访达里 **按住 Control 点按** `UkraineFront.app` → 选“打开” → 在弹窗里再点“打开”。",
        "2. 如果弹窗里没有“打开”（macOS 15 / 26 上常见）：先双击一次让系统拦住它，然后打开 **系统设置 → 隐私与安全性**，点“仍要打开”。",
        "3. 只需做一次。若提示“已损坏”，在终端执行 `xattr -dr com.apple.quarantine` 加上 App 的路径，然后再打开。",
        "",
        "双击后会打开“终端”窗口并自动打开浏览器。使用期间不要关闭这个窗口，关掉就是退出。",
        "",
    ]
    mac = sorted(folder.glob("UkraineFront-macos-*.zip"))
    if not mac:
        lines.append("（这次没有 macOS 压缩包。）")
    for z in mac:
        minos = _plist(z).get("LSMinimumSystemVersion") or "见 App 的最低系统版本"
        if "arm64" in z.name:
            who = f"仅 Apple 芯片（M1/M2/M3/M4）。需要 macOS {minos} 或更高。不能在 Intel Mac 上运行。"
        elif "x64" in z.name:
            who = (f"Intel Mac，以及通过 Rosetta 2 运行的 Apple 芯片 Mac。"
                   f"需要 macOS {minos} 或更高。")
        else:
            who = f"需要 macOS {minos} 或更高。"
        lines.append(f"- `{z.name}`（{_mb(z)}）：{who}")
    lines += ["", "## Windows", ""]
    wins = sorted(folder.glob("UkraineFront-windows-*.zip"))
    if wins:
        for z in wins:
            lines.append(
                f"- `{z.name}`（{_mb(z)}）：Windows 10/11 64 位。"
                "SmartScreen 若提示“Windows 已保护你的电脑”，点“更多信息”→“仍要运行”。"
            )
    else:
        lines.append("Windows 压缩包会在 Windows 构建成功后附加到这个 Release。Windows 失败不会撤下上面的 macOS 包。")
    lines.append("")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
