"""Deliver a single media request after the native app finishes launching."""
import json
import subprocess
import time

APP = '/Applications/NeteaseMusic.app'
READY_SCRIPT = '''ObjC.import("AppKit");
var apps=$.NSRunningApplication.runningApplicationsWithBundleIdentifier("com.netease.163music");
var app=Number(apps.count) > 0 ? apps.objectAtIndex(0) : null;
JSON.stringify({running:!!app,ready:app ? Number(app.finishedLaunching) === 1:false});'''


def player_state():
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', READY_SCRIPT],
                            capture_output=True, text=True, timeout=5)
    if result.returncode != 0:
        raise OSError('NetEase readiness query failed')
    try:
        state = json.loads(result.stdout)
    except ValueError as exc:
        raise OSError('Invalid NetEase readiness response') from exc
    if not isinstance(state, dict):
        raise ValueError('Invalid NetEase readiness state')
    return state


def open_media(args, capture_output=True, text=True, timeout=25, *,
               state_reader=player_state, run=subprocess.run, sleep=time.sleep):
    state = state_reader()
    starting = not state.get('ready')
    if not state.get('running'):
        # Do not send media while Electron/native playback handlers are loading.
        launched = run(['/usr/bin/open', '-g', '-a', APP], capture_output=True, text=True, timeout=25)
        if launched.returncode != 0:
            return launched
    if starting:
        for _ in range(20):
            if state_reader().get('ready'):
                break
            sleep(.5)
        else:
            raise OSError('NetEase did not finish launching')
        # AppKit launch completion precedes this client's media handler setup.
        # Give that handler time before its first (and only) media URL/file.
        sleep(2)
    return run(args, capture_output=capture_output, text=text, timeout=timeout)
