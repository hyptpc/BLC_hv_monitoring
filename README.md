# CAEN HV Logger

This script monitors specified channels on a CAEN HV power supply (SY1527) and logs their status (Voltage, Current, ON/OFF, Trip) to a local SQLite database.

## Setup

1.  **Install Python Libraries:**
    ```bash
    pip install pyyaml caen-libs
    ```

2.  **Install CAEN C Libraries:**
    This script requires the underlying CAEN C libraries (`libcaenhvwrapper.so`) to be installed on your system.
    * Download and install the **CAEN HV Wrapper Library** from the CAEN website.
    * Ensure all dependencies are met (e.g., `compat-openssl11` or `openssl1.1-libs` for `libcrypto.so.1.1` on modern Linux systems).
    * Run `sudo ldconfig` after installation.

## Configuration

Create a `config.yml` file in the same directory as the script. This file defines the HV crate connection, database settings, and which channels to monitor.

**Example `config.yml`:**

```yaml
caen_connection:
  host: '192.168.20.51'
  systype: 'SY1527'
  linktype: 'TCPIP'

database:
  db_file: 'hv_monitoring.db'
  logging_interval_sec: 60

# --- Specify which slots and channels to log ---
monitoring_targets:
  # Log ALL channels in Slot 4
  4: "ALL"
  
  # Log only specific channels in Slot 8
  8:
    - 1
    - 2
    - 3
    - 4
    - 6
    - 7
    - 8
    # ... (list all channels to monitor)
```

## Private login file

The command line is unchanged. The logger automatically reads
`credentials.local.yml` from the same directory as the configuration file.
No environment variables or extra arguments are needed.

On a new installation, copy the example, restrict its permissions, then edit
`username` and `password` (quote both YAML values):

```bash
cp -n param/credentials.example.yml param/credentials.local.yml
chmod 600 param/credentials.local.yml
```

For `python3 monitor_caen.py param/conf.yml`, the login file is
`param/credentials.local.yml`. On monitor2 the existing login has already been
migrated; keep using the existing command or service controls.

The login file is excluded from Git and must be owned by the service user with
no group/other permissions. Missing or invalid login files stop the logger
before any database or device connection. Example credentials are blank.
This removes credentials from the current source; it does not erase earlier
Git history or change the device password.

Offline verification (uses fake CAEN and database objects):

```bash
python3 tests/test_monitor_credentials.py
```

## Usage

Run the script from your terminal. It will connect to the HV crate and the database, then begin logging at the specified interval.

```bash
python3 monitor_caen.py /path/to/your/config.yml
```
Press `Ctrl+C` to stop the logger.

## Database Output
The script will create or append to the SQLite database file specified in the config (e.g., `hv_monitoring.db`).

The data is stored in the `measurements` table with the following structure:

* `id` (INTEGER): Primary Key
* `timestamp` (DATETIME): Time of the reading
* `port_id` (INTEGER): Unique ID (Slot * 100 + Channel)
* `voltage_set` (FLOAT): Target voltage (V0Set)
* `voltage_mon` (FLOAT): Measured voltage (VMon)
* `current` (FLOAT): Measured current (IMon)
* `is_hv_on` (BOOLEAN): 1 (ON) or 0 (OFF)
* `status_raw` (INTEGER): The raw status bitmask from the device
* `is_overcurrent_protection_active` (BOOLEAN): 1 if OVC bit (8) is set
* `is_current_out_of_spec` (BOOLEAN): 1 if IMon > I0Set

## Service control

Use [daemon/](daemon/README.md): `start.sh` starts the service and enables automatic
startup, `stop.sh` stops it and disables automatic startup, and `monitor.sh` shows
its state. sudo asks for your password.

```bash
./daemon/monitor.sh
./daemon/stop.sh
```

Before first use of the updated units, stop the target and install its definition
with `sudo /usr/bin/python3 daemon/configure.py TARGET`. Installation backs up old
units and reloads systemd without starting anything. See the daemon README for
all targets and the bounded automatic-restart policy.
