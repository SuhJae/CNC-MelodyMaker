import os
import json
import serial
import threading
import queue
import time
import math
import serial.tools.list_ports

from cnc import CNCMachine


class XCarve(CNCMachine):
    def __init__(self):
        self.config_file = 'x_carve_config.json'
        self.ser = None
        self.lock = threading.Lock()
        self.command_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.axis_positions = {'X': 100.0, 'Y': 100.0}  # Start at (100,100)
        self.center_position = {'X': 100.0, 'Y': 100.0}
        self.max_travel = {'X': 50.0, 'Y': 50.0}  # Maximum travel distance from center for each axis

    def get_available_ports(self):
        """Detect available serial ports."""
        ports = list(serial.tools.list_ports.comports())
        if len(ports) == 0:
            print("No serial ports found. Please connect your device and try again.")
            return None
        return ports

    def load_config(self):
        """Load the last used port from the configuration file."""
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                return json.load(f).get('port', None)
        return None

    def save_config(self, port):
        """Save the last used port to a configuration file."""
        with open(self.config_file, 'w') as f:
            json.dump({'port': port}, f)

    def choose_port(self):
        """Allow the user to choose a serial port, using the last saved port if available."""
        ports = self.get_available_ports()
        if ports is None:
            return None

        last_port = self.load_config()
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
            self.save_config(selected_port)
            return selected_port
        except (ValueError, IndexError):
            print("Invalid selection. Please run the script again.")
            return None

    def connect(self):
        selected_port = self.choose_port()
        if selected_port is None:
            print("No port selected. Exiting.")
            return False
        try:
            self.ser = serial.Serial(selected_port, 115200, timeout=1)
            # Start serial worker thread
            self.worker_thread = threading.Thread(target=self.serial_worker)
            self.worker_thread.start()

            # Wake up GRBL
            self.ser.write("\r\n\r\n".encode())
            time.sleep(2)  # Wait for GRBL to initialize
            self.ser.flushInput()  # Flush startup text in serial input

            # Read any initial messages
            while self.ser.in_waiting:
                print(self.ser.readline().decode().strip())
            return True
        except serial.SerialException as e:
            print(f"Serial communication error: {e}")
            return False

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.stop_event.set()
            print("Serial connection closed.")

    def initialize(self):
        # Ask the user whether to perform the homing cycle
        homing_cycle = input("Do you want to perform homing cycle? (y/n): ").strip().lower()
        if homing_cycle == 'y':
            self.send_gcode("$H")  # Send homing command
            time.sleep(2)  # Wait for homing to complete

        # Set to relative positioning
        self.send_gcode("G91")

    def send_gcode(self, command):
        with self.lock:
            try:
                command = command.strip()  # Remove any whitespace
                self.ser.write(f"{command}\n".encode())  # Send command
                while True:
                    grbl_out = self.ser.readline().decode().strip()  # Wait for response
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

    def serial_worker(self):
        """Thread function to send commands from the queue to GRBL."""
        while not self.stop_event.is_set() or not self.command_queue.empty():
            try:
                command = self.command_queue.get(timeout=0.1)
                response = self.send_gcode(command)
                self.command_queue.task_done()
                if response.startswith('error'):
                    print(f"GRBL Error: {response}")
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Serial worker error: {e}")
                self.stop_event.set()

    # Function to calculate feed rate based on frequency
    def calculate_feed_rate(self, frequency):
        mm_per_step = 0.0375  # mm per step (calculated from machine specs)
        feed_rate = frequency * mm_per_step * 60  # in mm/min
        return feed_rate

    def calculate_combined_feed_rate(self, distances, required_feed_rates):
        """Calculate the combined feed rate to maintain per-axis feed rates during diagonal movements."""
        # Calculate total movement distance (Euclidean distance)
        total_distance = math.sqrt(sum(d ** 2 for d in distances.values()))

        # Calculate required combined feed rates for each axis
        combined_feed_rates = {}
        for axis in distances:
            if distances[axis] == 0:
                continue  # Avoid division by zero
            combined_feed_rate = required_feed_rates[axis] * (total_distance / abs(distances[axis]))
            combined_feed_rates[axis] = combined_feed_rate

        if not combined_feed_rates:
            return 0

        # Set combined feed rate to the maximum of the required combined feed rates
        combined_feed_rate = max(combined_feed_rates.values())

        return combined_feed_rate

    def choose_direction(self, axis, movement_distance):
        current_position = self.axis_positions[axis]
        center_position = self.center_position[axis]
        max_travel = self.max_travel[axis]
        # Decide direction based on which way brings us closer to center_position
        if current_position > center_position:
            return -1  # Move towards center_position
        elif current_position < center_position:
            return 1  # Move towards center_position
        else:
            # At center_position, choose the direction that doesn't exceed limits
            if current_position + movement_distance <= center_position + max_travel:
                return 1
            else:
                return -1

    def adjust_movement_distance(self, axis, desired_distance):
        current_position = self.axis_positions[axis]
        center_position = self.center_position[axis]
        max_travel = self.max_travel[axis]
        # Calculate potential new position
        new_position = current_position + desired_distance
        if new_position > center_position + max_travel:
            # Adjust the distance to not exceed max_travel
            adjusted_distance = (center_position + max_travel) - current_position
            return adjusted_distance
        elif new_position < center_position - max_travel:
            adjusted_distance = (center_position - max_travel) - current_position
            return adjusted_distance
        else:
            return desired_distance

    def calculate_movement_time(self, distances, feed_rate, acceleration_settings):
        """
        Calculate the actual time required for the movement, considering acceleration and deceleration.
        """
        total_distance = math.sqrt(sum(d ** 2 for d in distances.values()))
        feed_rate_mm_per_s = feed_rate / 60.0  # Convert feed rate to mm/s

        # Calculate time to accelerate to feed rate
        max_acceleration = min(acceleration_settings.values())  # Use the lowest acceleration among axes
        t_accel = feed_rate_mm_per_s / max_acceleration
        d_accel = 0.5 * max_acceleration * t_accel ** 2

        if total_distance < 2 * d_accel:
            # Triangular profile (did not reach full feed rate)
            t_total = 2 * math.sqrt(total_distance / max_acceleration)
        else:
            # Trapezoidal profile
            d_constant = total_distance - 2 * d_accel
            t_constant = d_constant / feed_rate_mm_per_s
            t_total = 2 * t_accel + t_constant

        return t_total

    def play_notes(self, notes):
        # Initialize variables
        self.play_notes_start_time = time.time()
        acceleration_settings = self.get_acceleration_settings()

        # Create a list of all event times (start and end times)
        event_times = set()
        for note in notes:
            event_times.add(note['start_time'])
            event_times.add(note['start_time'] + note['duration'])
        event_times = sorted(event_times)

        # Build intervals between event times
        intervals = []
        for i in range(len(event_times) - 1):
            intervals.append((event_times[i], event_times[i + 1]))

        # For each interval, determine active notes
        for interval_start, interval_end in intervals:
            interval_duration = interval_end - interval_start
            if interval_duration <= 0:
                continue  # Skip zero-length intervals

            # Wait until interval_start
            current_time = time.time()
            elapsed_time = current_time - self.play_notes_start_time
            wait_time = interval_start - elapsed_time
            if wait_time > 0:
                time.sleep(wait_time)

            # Determine active notes during this interval
            active_notes = []
            for note in notes:
                note_start = note['start_time']
                note_end = note_start + note['duration']
                if note_start < interval_end and note_end > interval_start:
                    active_notes.append(note)

            if not active_notes:
                continue  # No active notes during this interval

            # Calculate movements for active notes
            distances = {}
            required_feed_rates = {}
            movement_durations = {}

            for note in active_notes:
                axis = note['axis']
                frequency = note['frequency']
                max_feed_rate = 8000  # Maximum allowed feed rate

                # Calculate feed rate for this note
                note_feed_rate = self.calculate_feed_rate(frequency)
                if note_feed_rate > max_feed_rate:
                    note_feed_rate = max_feed_rate
                    print(f"Feed rate too high for {note['note']} ({frequency} Hz), limiting to {max_feed_rate} mm/min")

                # Calculate movement distance for this note
                note_distance = note_feed_rate * (interval_duration / 60.0)

                # Adjust direction based on current position
                direction = self.choose_direction(axis, note_distance)
                note_distance *= direction

                # Adjust movement distance to stay within limits
                adjusted_distance = self.adjust_movement_distance(axis, note_distance)

                # Update current position
                self.axis_positions[axis] += adjusted_distance

                # Store calculated values
                distances[axis] = adjusted_distance
                required_feed_rates[axis] = note_feed_rate
                movement_durations[axis] = interval_duration

            # Calculate combined feed rate for synchronized movement
            combined_feed_rate = self.calculate_combined_feed_rate(distances, required_feed_rates)

            # Prepare combined movement command
            axes_moves = ' '.join(f"{axis}{distances[axis]:.4f}" for axis in distances)
            movement_command = f"G1 {axes_moves} F{combined_feed_rate:.2f}"

            # Limit command queue size
            while self.command_queue.qsize() >= 15:
                time.sleep(0.1)

            self.command_queue.put(movement_command)

            # Calculate movement time based on acceleration
            actual_movement_time = self.calculate_movement_time(distances, combined_feed_rate, acceleration_settings)

            # Wait for the movement to complete
            time.sleep(actual_movement_time)

    def get_acceleration_settings(self):
        """Retrieve the acceleration settings from GRBL."""
        settings = {'X': 1000, 'Y': 1000}  # Default to 1000 mm/s² if unknown
        self.send_gcode("$$")
        # For now, return default settings
        return settings