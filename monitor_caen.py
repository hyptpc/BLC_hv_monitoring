#!/usr/bin/env python3
import caen_libs.caenhvwrapper as hv
import sys
import time
import datetime
import sqlite3
import yaml       
import argparse   

# --- CAEN Parameter Names ---
PARAM_V_SET = 'V0Set' # Target/Set Voltage
PARAM_V_MON = 'VMon'  # Measured/Monitor Voltage
PARAM_I_MON = 'IMon'  # Measured/Monitor Current
PARAM_I_SET = 'I0Set' # Target/Set Current

# --- CAEN Status Bit Flags ---
STATUS_ON      = 1  # (1 << 0) Power ON
STATUS_OVC     = 8  # (1 << 3) Over Current (Current Trip)

#______________________________________________________________________________
def create_database_table(conn):
    """
    Create the database table if it does not already exist.
    Uses separate columns for set and monitor voltage.
    """
    
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS measurements (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME,
        port_id INTEGER,
        voltage_set FLOAT,
        voltage_mon FLOAT,
        current FLOAT,
        is_hv_on BOOLEAN,
        status_raw INTEGER,
        is_overcurrent_protection_active BOOLEAN,
        is_current_out_of_spec BOOLEAN
    );
    """
    try:
        cursor = conn.cursor()
        cursor.execute(create_table_sql)
        conn.commit()
    except sqlite3.Error as e:
        print(f"[DB Error] Failed to create table: {e}", file=sys.stderr)
        raise

#______________________________________________________________________________
def log_hv_status(device, conn, targets):
    """
    Read status only for the slots/channels specified in 'targets'
    and insert the data into the database.
    """
    print(f"Logging data for specified targets at {datetime.datetime.now()}...")
    
    try:
        # Get the full crate map to verify board presence
        slots_map = device.get_crate_map()
        cursor = conn.cursor()
        
        # Iterate through the TARGETS defined in config.yml
        for slot_id, channel_config in targets.items():
            
            # Ensure the slot_id from config is valid
            try:
                slot_id = int(slot_id) # Ensure key is integer for list indexing
                board = slots_map[slot_id]
            except (ValueError, IndexError):
                print(f"  Skipping Slot {slot_id}: Invalid slot ID or slot out of range.")
                continue
            except TypeError:
                print(f"  Skipping Slot {slot_id}: 'monitoring_targets' keys must be integers.")
                continue

            if not board:
                print(f"  Skipping Slot {slot_id}: Found no board (EMPTY).")
                continue # Skip empty slots
            
            channels_to_log = []
            if isinstance(channel_config, str) and channel_config.upper() == "ALL":
                # Config specified "ALL", so log all channels for this board
                channels_to_log = list(range(board.n_channel))
            elif isinstance(channel_config, list):
                # Config specified a list of channels
                channels_to_log = channel_config
            else:
                print(f"  Skipping Slot {slot_id}: Invalid channel config '{channel_config}'.")
                continue

            # Log only the specified channels for this slot
            for ch in channels_to_log:
                if ch >= board.n_channel:
                    print(f"  Skipping Slot {slot_id} Ch {ch}: Channel number too high for this board (Max: {board.n_channel - 1}).")
                    continue

                # Convert datetime to ISO 8601 string to avoid Python 3.12 DeprecationWarning
                timestamp_str = datetime.datetime.now().isoformat()
                
                # Create a unique ID from slot and channel
                port_id = (board.slot * 100) + ch

                try:
                    # Get all required data from the device
                    v_set = device.get_ch_param(board.slot, [ch], PARAM_V_SET)[0]
                    v_mon = device.get_ch_param(board.slot, [ch], PARAM_V_MON)[0]
                    i_mon = device.get_ch_param(board.slot, [ch], PARAM_I_MON)[0]
                    i_set = device.get_ch_param(board.slot, [ch], PARAM_I_SET)[0]
                    status_raw = device.get_ch_param(board.slot, [ch], 'Status')[0]

                    # Map raw status bits to boolean columns
                    is_hv_on = (status_raw & STATUS_ON) != 0
                    is_overcurrent = (status_raw & STATUS_OVC) != 0
                    
                    # Check if measured current exceeds the set limit
                    is_current_out_of_spec = (i_mon > i_set) if i_set > 0 else False
                    
                    # Prepare data tuple for SQL insertion
                    data_tuple = (
                        timestamp_str,
                        port_id,
                        v_set,
                        v_mon,
                        i_mon,
                        is_hv_on,
                        status_raw,
                        is_overcurrent,
                        is_current_out_of_spec
                    )

                    # Execute the SQL INSERT command
                    insert_sql = """
                    INSERT INTO measurements (
                        timestamp, port_id, voltage_set, voltage_mon, current, 
                        is_hv_on, status_raw, 
                        is_overcurrent_protection_active, is_current_out_of_spec
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """
                    cursor.execute(insert_sql, data_tuple)

                except hv.Error as e:
                    # Skip this channel if a parameter read fails (e.g., 'I0Set' not found)
                    print(f"  Skipping Slot {board.slot} Ch {ch}: {e}")
                
        # Commit the transaction after logging all target channels
        conn.commit()
        print("  Log complete.")

    except hv.Error as e:
        print(f"\n[CAEN HV Error] {e}", file=sys.stderr)
    except sqlite3.Error as e:
        print(f"\n[DB Error] {e}", file=sys.stderr)
        conn.rollback() # Rollback changes on database error
    except Exception as e:
        print(f"\n[Error] An error occurred: {e}", file=sys.stderr)

#______________________________________________________________________________
def main():
    """
    Main function to load config, connect to devices, and start the logging loop.
    This version connects and disconnects inside the loop.
    """
    
    # Parse command-line arguments to find the config file
    parser = argparse.ArgumentParser(description="CAEN HV Logger")
    
    # Use a positional argument for the config file path
    parser.add_argument(
        "config_file", 
        help="Path to the configuration YAML file (e.g., config.yml)"
    )
    args = parser.parse_args()

    # Load the YAML configuration file
    try:
        with open(args.config_file, 'r') as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"[Fatal Error] Config file not found: {args.config_file}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"[Fatal Error] Error parsing YAML file {args.config_file}: {e}", file=sys.stderr)
        sys.exit(1)

    # Assign configuration values to variables
    try:
        caen_cfg = config['caen_connection']
        db_cfg = config['database']
        monitoring_targets = config['monitoring_targets']
        
        DB_FILE = db_cfg['db_file']
        LOGGING_INTERVAL_SEC = db_cfg['logging_interval_sec']
        
        host = caen_cfg['host']
        systype = caen_cfg['systype']
        linktype = caen_cfg['linktype']

    except KeyError as e:
        print(f"[Fatal Error] Config file {args.config_file} is missing key: {e}", file=sys.stderr)
        sys.exit(1)

    # Exit if no targets are specified
    if not monitoring_targets:
        print(f"[Fatal Error] 'monitoring_targets' in {args.config_file} is empty. Nothing to do.", file=sys.stderr)
        sys.exit(1)
    
    # Connect to the database (remains connected)
    db_conn = None
    try:
        print(f"Connecting to database: {DB_FILE}")
        db_conn = sqlite3.connect(DB_FILE)
        create_database_table(db_conn)
        
        print(f"Connection successful. Starting logger (Interval: {LOGGING_INTERVAL_SEC}s)...")
        print(f"Monitoring targets: {monitoring_targets}")
        
        # Start the main logging loop
        while True:
            hv_device = None
            try:
                # 1. Connect to CAEN HV (INSIDE the loop)
                print(f"Connecting to CAEN HV at {host}...")
                hv_device = hv.Device.open(hv.SystemType[systype], hv.LinkType[linktype],
                                          host, 'admin', 'admin')
                
                # 2. Pass targets to the logging function
                log_hv_status(hv_device, db_conn, monitoring_targets) 
            
            except hv.Error as e:
                # Catch connection errors (e.g., "Device Busy")
                print(f"[CAEN HV Error] Failed to connect or log: {e}", file=sys.stderr)
            except KeyboardInterrupt:
                print("\nStopping logger.")
                break # Exit the while loop
            except Exception as e:
                print(f"\n[Fatal Error] {e}", file=sys.stderr)
            finally:
                # 3. Disconnect from CAEN HV (INSIDE the loop)
                if hv_device:
                    hv_device.close()
                    print("CAEN HV connection closed.")
            
            # 4. Wait for the next interval
            print(f"Sleeping for {LOGGING_INTERVAL_SEC} seconds...")
            try:
                time.sleep(LOGGING_INTERVAL_SEC)
            except KeyboardInterrupt:
                print("\nStopping logger.")
                break # Exit the while loop

    except Exception as e:
        print(f"\n[Fatal Error during setup] {e}", file=sys.stderr)
    finally:
        # Clean up database connection on exit
        if db_conn:
            print("Closing database connection.")
            db_conn.close()

#______________________________________________________________________________
if __name__ == '__main__':
  main()
