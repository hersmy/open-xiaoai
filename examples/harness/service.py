"""Install or control only the user LaunchAgent belonging to this example."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import time

ROOT = Path(__file__).resolve().parent
LABEL = "com.zhangyichi.xiaoai-harness"
DOMAIN = f"gui/{os.getuid()}"
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "start", "stop", "status"])
    parser.add_argument("--speaker-ip")
    args = parser.parse_args()
    if args.action == "install":
        if not args.speaker_ip:
            parser.error("install requires --speaker-ip")
        runtime = ROOT / ".runtime"
        runtime.mkdir(mode=0o700, exist_ok=True)
        config = {"Label": LABEL,
            "ProgramArguments": [str(ROOT / ".venv/bin/python"), "-u", str(ROOT / "bridge.py"),
                                 "--speaker-ip", args.speaker_ip],
            "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
            "ThrottleInterval": 10, "ProcessType": "Background",
            "StandardOutPath": str(runtime / "service.stdout"),
            "StandardErrorPath": str(runtime / "service.log"),
            "EnvironmentVariables": {"PATH": str(Path.home() / ".local/bin") +
                                     ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}}
        PLIST.parent.mkdir(exist_ok=True)
        subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{LABEL}"], capture_output=True)
        # bootout can return before launchd has removed the old registration.
        for _ in range(40):
            old = subprocess.run(["launchctl", "print", f"{DOMAIN}/{LABEL}"], capture_output=True)
            if old.returncode != 0:
                break
            time.sleep(.25)
        else:
            raise RuntimeError("Previous bridge service has not stopped")
        PLIST.write_bytes(plistlib.dumps(config))
        os.chmod(PLIST, 0o600)
        subprocess.run(["launchctl", "bootstrap", DOMAIN, str(PLIST)], check=True)
        print("Installed", PLIST)
    elif args.action == "stop":
        subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{LABEL}"], check=True)
    elif args.action == "start":
        subprocess.run(["launchctl", "bootstrap", DOMAIN, str(PLIST)], check=True)
    else:
        subprocess.run(["launchctl", "print", f"{DOMAIN}/{LABEL}"], check=True)


if __name__ == "__main__":
    main()
