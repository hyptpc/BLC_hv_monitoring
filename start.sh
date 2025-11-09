#!/bin/bash

# --- Configuration ---
SESSION_NAME="blc"  # The name of the tmux session this service will manage
TMUX_PATH="/usr/bin/tmux" # Full path to tmux (find with 'which tmux')

# Python virtual environment path
VENV_PYTHON_PATH="/home/sks/.venv/bin/python"
# Project working directory
WORKING_DIR="/home/sks/monitor_tools/BLC_hv_monitoring"

# Command to execute inside tmux
EXECUTE_COMMAND="$VENV_PYTHON_PATH ./monitor_caen.py; /bin/bash"
# ------------------


# Check if a session with the same name already exists
if $TMUX_PATH has-session -t $SESSION_NAME 2>/dev/null; then
    # If it exists:
    echo "Error: tmux session '$SESSION_NAME' is already running." >&2
    echo "This service cannot start." >&2
    exit 1 # <--- Notify systemd of the failure
else
    # If it does not exist:
    echo "Starting new tmux session: $SESSION_NAME"    
    $TMUX_PATH new-session -d -s $SESSION_NAME -c $WORKING_DIR "$EXECUTE_COMMAND"
    exit 0 # <--- Notify systemd of the success
fi
