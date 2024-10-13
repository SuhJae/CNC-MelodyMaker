import serial
import time
import serial.tools.list_ports
import json
import os
import threading


# Configuration file to save the last used port
config_file = 'arduino_config.json'



def save_config(port):
    """Save the last used port to a configuration file."""
    with open(config_file, 'w') as f:
        json.dump({'port': port}, f)

def load_config():
    """Load the last used port from the configuration file."""
    if os.path.exists(config_file):
        with open(config_file, 'r') as f:
            return json.load(f).get('port', None)
    return None

def detect_ports():
    """Detect available serial ports."""
    ports = list(serial.tools.list_ports.comports())
    if len(ports) == 0:
        print("No serial ports found. Please connect your device and try again.")
        return None
    return ports

def choose_port():
    """Allow the user to choose a serial port, using the last saved port if available."""
    ports = detect_ports()
    if ports is None:
        return None

    last_port = load_config()
    if last_port and any(port.device == last_port for port in ports):
        print(f"\nLast used port: {last_port}")
        use_last = input("Do you want to use the last used port? (y/n): ").strip().lower()
        if use_last == 'y':
            return last_port

    print("\nAvailable serial ports:")
    for i, port in enumerate(ports):
        print(f"{i}: {port.device} - {port.description}")

    try:
        choice = int(input("Select the port number: "))
        selected_port = ports[choice].device
        save_config(selected_port)
        return selected_port
    except (ValueError, IndexError):
        print("Invalid selection. Please run the script again.")
        return None

def move_to_start_position(ser, lock):
    """Move the machine to the starting position (100, 100)."""
    # Set to absolute positioning
    send_gcode(ser, "G90", lock)
    # Move to (100, 100) at a safe feed rate
    send_gcode(ser, "G0 X100 Y100 F8000", lock)
    time.sleep(1)
    send_gcode(ser, "G0 X100 Y700 F8000", lock)
    time.sleep(1)
    send_gcode(ser, "G0 X700 Y700 F8000", lock)
    time.sleep(1)
    send_gcode(ser, "G0 X0 Y0 F8000", lock)
    time.sleep(1)
    send_gcode(ser, "G0 X700 Y700 F8000", lock)
    time.sleep(1)
    send_gcode(ser, "G0 X0 Y0 F8000", lock)
    time.sleep(1)
    # Set to relative positioning
    send_gcode(ser, "G91", lock)


def send_gcode(ser, command, lock):
    """Send a G-code command to GRBL and wait for the response."""
    with lock:
        try:
            command = command.strip()  # Remove any whitespace
            ser.write(f"{command}\n".encode())  # Send command
            while True:
                grbl_out = ser.readline().decode().strip()  # Wait for response
                if grbl_out == '':
                    continue
                print(f"Sent: {command} | Received: {grbl_out}")
                if grbl_out == 'ok':
                    break
                elif grbl_out.startswith('error'):
                    print(f"GRBL Error: {grbl_out}")
                    break
                elif grbl_out.startswith('ALARM'):
                    print(f"GRBL Alarm: {grbl_out}")
                    break
                elif grbl_out.startswith('Grbl'):
                    print(f"GRBL Reset Detected: {grbl_out}")
                    break
            return grbl_out
        except Exception as e:
            print(f"Error sending G-code '{command}': {e}")
            raise

# Main script
if __name__ == "__main__":
    # First, select the serial port
    selected_port = choose_port()
    if selected_port is None:
        print("No port selected. Exiting.")
        exit()


    # Connect to GRBL
    ser = serial.Serial(selected_port, 115200, timeout=1)  # Use the selected port

    # Wake up GRBL
    ser.write("\r\n\r\n".encode())
    time.sleep(2)   # Wait for GRBL to initialize
    ser.flushInput()  # Flush startup text in serial input

    # Read any initial messages
    while ser.in_waiting:
        print(ser.readline().decode().strip())

    # Ask if user wants to perform homing
    perform_homing = input("Do you want to perform homing cycle? (y/n): ").strip().lower()
    if perform_homing == 'y':
        # Homing sequence
        print("Starting homing cycle...")
        send_gcode(ser, "$H", threading.Lock())
        time.sleep(2)  # Wait for homing to complete

        # Move to starting position
        move_to_start_position(ser, threading.Lock())
    else:
        # Unlock GRBL to clear alarm state
        print("WARNING: Skipping homing cycle and unlocking GRBL can be unsafe.")
        print("Ensure that the machine's position is known and safe to operate.")
        user_confirm = input("Type 'yes' to confirm and proceed: ").strip().lower()
        if user_confirm == 'yes':
            print("Unlocking GRBL...")
            send_gcode(ser, "$X", threading.Lock())
            time.sleep(0.1)
        else:
            print("Operation cancelled. Please restart the script if you wish to proceed.")
            ser.close()
            exit()

        # Move to starting position
        move_to_start_position(ser, threading.Lock())
