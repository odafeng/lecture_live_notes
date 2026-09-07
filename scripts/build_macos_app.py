"""Rebuild the local macOS launcher with the system AppleScript tools."""

from pathlib import Path
import plistlib
import subprocess
import tempfile

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "Lecture.app"


def build():
    subprocess.run(["/usr/bin/osacompile", "-o", str(BUNDLE),
                    str(ROOT / "scripts" / "launch.applescript")], check=True)
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        original = temporary / "icon.png"
        with sync_playwright() as playwright:
            with playwright.chromium.launch() as browser:
                page = browser.new_page(viewport={"width": 1024, "height": 1024})
                page.set_content('<style>body { margin: 0 }</style>' +
                                 (ROOT / "scripts" / "icon.svg").read_text())
                page.screenshot(path=str(original), omit_background=True)
        iconset = temporary / "Lecture.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = f"icon_{size}x{size}" + ("@2x" if scale == 2 else "") + ".png"
                pixels = str(size * scale)
                subprocess.run(["/usr/bin/sips", "-z", pixels, pixels, str(original),
                                "--out", str(iconset / name)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o",
                        str(BUNDLE / "Contents" / "Resources" / "applet.icns")], check=True)

    plist_path = BUNDLE / "Contents" / "Info.plist"
    with plist_path.open("rb") as file:
        info = plistlib.load(file)
    info.update(CFBundleName="Lecture", CFBundleDisplayName="Lecture",
                CFBundleIdentifier="local.lecture.live-notes", CFBundleShortVersionString="1.0",
                LSUIElement=True)
    with plist_path.open("wb") as file:
        plistlib.dump(info, file)
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(BUNDLE)], check=True)
    print(BUNDLE)


if __name__ == "__main__":
    build()
