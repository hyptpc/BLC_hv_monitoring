#!/usr/bin/env bash
set -u

WORKING_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
case "${WORKING_DIR##*/}" in
    BLC_hv_monitoring) SESSION_NAME=blc; MONITOR_NAME=BLC ;;
    CVC_hv_monitoring) SESSION_NAME=ftof; MONITOR_NAME=FTOF ;;
    *) printf 'Use a BLC_hv_monitoring or CVC_hv_monitoring checkout directory.\n' >&2; exit 2 ;;
esac
TMUX_PATH=/usr/bin/tmux
VENV_PYTHON_PATH=/home/sks/.venv/bin/python

case "${1:-}" in
    --run)
        # Keep failures visible in the pane and bound automatic retries.
        starts=()
        trap 'exit 143' TERM HUP
        while true; do
            now=$SECONDS
            recent=()
            for started in "${starts[@]}"; do
                if (( now - started < 3600 )); then recent+=("$started"); fi
            done
            starts=("${recent[@]}")
            if (( ${#starts[@]} >= 5 )); then
                printf '\n%s stopped: 5 starts within one hour. Fix the cause, then stop/start the service.\n' "$MONITOR_NAME"
                break
            fi
            starts+=("$now")
            "$VENV_PYTHON_PATH" -u ./monitor_caen.py param/conf.yml
            result=$?
            printf '\n%s monitor exited (status %s).\n' "$MONITOR_NAME" "$result"
            if (( ${#starts[@]} >= 5 && SECONDS - starts[0] < 3600 )); then
                continue
            fi
            printf 'Retrying in 30 seconds. Use daemon/stop.sh to stop the service.\n'
            sleep 30
        done
        # Leave the final error visible for a person attaching to the session.
        exec /bin/bash
        ;;
    --stop)
        if "$TMUX_PATH" has-session -t "=$SESSION_NAME" 2>/dev/null; then
            exec "$TMUX_PATH" kill-session -t "=$SESSION_NAME"
        fi
        exit 0
        ;;
    '')
        if "$TMUX_PATH" has-session -t "=$SESSION_NAME" 2>/dev/null; then
            printf "Error: tmux session '%s' already exists; stop it before starting the service.\n" "$SESSION_NAME" >&2
            exit 1
        fi
        printf -v command 'exec /usr/bin/bash %q --run' "$WORKING_DIR/start.sh"
        printf "Starting tmux session: %s\n" "$SESSION_NAME"
        exec "$TMUX_PATH" new-session -d -s "$SESSION_NAME" -c "$WORKING_DIR" "$command"
        ;;
    *) printf 'Usage: start.sh\n' >&2; exit 2 ;;
esac
