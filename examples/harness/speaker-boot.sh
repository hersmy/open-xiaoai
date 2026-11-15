#!/bin/sh
# The client retries while Wi-Fi or the Mac is unavailable.
WORK_DIR=/data/open-xiaoai
PID_FILE=/tmp/open-xiaoai-client.pid
if [ -r "$PID_FILE" ]; then
    old_pid=$(cat "$PID_FILE")
    if ps | awk -v pid="$old_pid" '$1 == pid && /open-xiaoai\/client/ { found=1 } END { exit !found }'; then
        exit 0
    fi
fi
[ -x "$WORK_DIR/client" ] && [ -r "$WORK_DIR/server.txt" ] || exit 1
server=$(cat "$WORK_DIR/server.txt")
"$WORK_DIR/client" "$server" > /tmp/open-xiaoai-client.log 2>&1 &
echo "$!" > "$PID_FILE"
