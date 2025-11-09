#!/bin/bash

# --- Configuration ---
SESSION_NAME="blc"  # The name of the tmux session this service will manage
TMUX_PATH="/usr/bin/tmux" # Full path to tmux (find with 'which tmux')

# Python virtual environment path
VENV_PYTHON_PATH="/home/sks/.venv/bin/python"
# Project working directory
WORKING_DIR="/home/sks/monitor_tools/BLC_hv_monitoring"

# Command to execute inside tmux
EXECUTE_COMMAND="while true; do $VENV_PYTHON_PATH ./monitor_caen.py param/conf.yml ; sleep 1; done; /bin/bash"
# ------------------


# Check if a session with the same name already exists
if $TMUX_PATH has-session -t $SESSION_NAME 2>/dev/null; then
    # If it exists:
    echo "tmux session '$SESSION_NAME' is already running."
    exit 0 # <--- Notify systemd of success (already running)
else
    # If it does not exist:
    echo "Starting new tmux session: $SESSION_NAME"
    $TMUX_PATH new-session -d -s $SESSION_NAME -c $WORKING_DIR "$EXECUTE_COMMAND"
    exit 0 # <--- Notify systemd of success (started)
fi
