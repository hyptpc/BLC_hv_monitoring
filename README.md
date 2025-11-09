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
