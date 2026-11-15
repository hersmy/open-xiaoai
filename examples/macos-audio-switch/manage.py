"""Build and manage the independent Mac headphone output LaunchAgent."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parent
HOME_DIR = Path.home()
INSTALL = HOME_DIR / "Library/Application Support/XiaoaiAudioSwitch"
PLIST = HOME_DIR / "Library/LaunchAgents/com.zhangyichi.headphone-output.plist"
LABEL = "com.zhangyichi.headphone-output"
DOMAIN = f"gui/{os.getuid()}"
SERVICE = f"{DOMAIN}/{LABEL}"


def loaded():
    return subprocess.run(["launchctl", "print", SERVICE], capture_output=True, text=True)


def stop():
    if loaded().returncode == 0:
        subprocess.run(["launchctl", "bootout", SERVICE], check=True)
        for _ in range(40):
            if loaded().returncode != 0:
                return
            time.sleep(0.1)
        raise RuntimeError("Old service did not exit")


def install(target):
    build = ROOT / ".runtime"
    build.mkdir(exist_ok=True)
    binary = build / "headphone-output"
    subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-O", str(ROOT / "main.swift"),
                    "-o", str(binary)], check=True)
    subprocess.run([str(binary), "--self-test"], check=True)
    stop()
    INSTALL.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(INSTALL, 0o700)
    shutil.copy2(binary, INSTALL / "headphone-output")
    os.chmod(INSTALL / "headphone-output", 0o700)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    spec = {
        "Label": LABEL,
        "ProgramArguments": [str(INSTALL / "headphone-output"), "--target", target,
                             "--state", str(INSTALL / "state.json")],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(INSTALL / "service.log"),
        "StandardErrorPath": str(INSTALL / "service.log"),
    }
    PLIST.write_bytes(plistlib.dumps(spec))
    os.chmod(PLIST, 0o600)
    subprocess.run(["plutil", "-lint", str(PLIST)], check=True)
    subprocess.run(["launchctl", "bootstrap", DOMAIN, str(PLIST)], check=True)
    print(f"Installed {LABEL}; target={target}")
    print(f"Log: {INSTALL / 'service.log'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "status", "stop", "start", "uninstall"])
    parser.add_argument("--target", default="HUAWEI FreeBuds Pro 5")
    args = parser.parse_args()
    if args.action == "install":
        install(args.target)
    elif args.action == "stop":
        stop()
    elif args.action == "start":
        if loaded().returncode != 0:
            subprocess.run(["launchctl", "bootstrap", DOMAIN, str(PLIST)], check=True)
    elif args.action == "uninstall":
        stop()
        PLIST.unlink(missing_ok=True)
        # Keep state/logs for review; only remove this service's executable.
        (INSTALL / "headphone-output").unlink(missing_ok=True)
        print("Uninstalled; state and logs retained")
    else:
        result = loaded()
        for line in result.stdout.splitlines():
            if any(key in line for key in ("state =", "pid =", "last exit code =", "program =")):
                print(line.strip())
        if result.returncode:
            print("Service is not loaded")
        binary = INSTALL / "headphone-output"
        if binary.exists():
            subprocess.run([str(binary), "--list"], check=True)


if __name__ == "__main__":
    main()
