import threading
import queue
import time
import math
import pygame
from cnc import CNCMachine


class VirtualCNC(CNCMachine):
    def __init__(self):
        # Machine dimensions in mm
        self.bed_width_mm = 750
        self.bed_height_mm = 750

        # Pygame window dimensions
        self.window_width = 800
        self.window_height = 800

        # Scaling factors to convert mm to pixels
        self.scale_x = self.window_width / self.bed_width_mm
        self.scale_y = self.window_height / self.bed_height_mm

        # Starting position at the center of the workspace
        self.axis_positions = {'X': self.bed_width_mm / 2, 'Y': self.bed_height_mm / 2}
        self.movement_directions = {'X': 1, 'Y': 1}  # Start moving in positive direction

        self.command_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.worker_thread = None
        self.display_thread = None
        self.positions = [self.axis_positions.copy()]
        self.screen = None
        self.clock = None
        self.running = False

    def connect(self):
        # No physical connection needed
        self.worker_thread = threading.Thread(target=self.serial_worker)
        self.display_thread = threading.Thread(target=self.display_loop)
        self.running = True
        self.worker_thread.start()
        self.display_thread.start()
        return True

    def disconnect(self):
        self.stop_event.set()
        self.running = False
        if self.worker_thread:
            self.worker_thread.join()
        if self.display_thread:
            self.display_thread.join()
        pygame.quit()

    def get_available_ports(self):
        return []

    def load_config(self):
        pass

    def save_config(self, port):
        pass

    def initialize(self, perform_homing=True):
        pass  # No initialization needed for virtual CNC

    def send_gcode(self, command):
        with self.lock:
            print(f"Virtual CNC received command: {command}")
            # Simulate G-code parsing
            if command.startswith('G1'):
                parts = command.split()
                for part in parts:
                    if part.startswith('X'):
                        self.axis_positions['X'] += float(part[1:])
                    elif part.startswith('Y'):
                        self.axis_positions['Y'] += float(part[1:])
                # Clamp positions to machine limits
                self.axis_positions['X'] = max(0, min(self.axis_positions['X'], self.bed_width_mm))
                self.axis_positions['Y'] = max(0, min(self.axis_positions['Y'], self.bed_height_mm))
                self.positions.append(self.axis_positions.copy())

    def serial_worker(self):
        while not self.stop_event.is_set() or not self.command_queue.empty():
            try:
                command = self.command_queue.get(timeout=0.1)
                self.send_gcode(command)
                self.command_queue.task_done()
            except queue.Empty:
                continue

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

                # Adjust movement distance and direction
                adjusted_distance = self.adjust_movement_distance(axis, note_distance)

                # Update current position
                self.axis_positions[axis] += adjusted_distance

                # Store calculated values
                distances[axis] = adjusted_distance
                required_feed_rates[axis] = note_feed_rate

            # Calculate combined feed rate for synchronized movement
            combined_feed_rate = self.calculate_combined_feed_rate(distances, required_feed_rates)

            # Prepare combined movement command
            axes_moves = ' '.join(f"{axis}{distances[axis]:.4f}" for axis in distances)
            movement_command = f"G1 {axes_moves} F{combined_feed_rate:.2f}"

            self.command_queue.put(movement_command)

            # Calculate movement time based on acceleration
            actual_movement_time = self.calculate_movement_time(distances, combined_feed_rate, acceleration_settings)

            # Wait for the movement to complete
            time.sleep(actual_movement_time)

    def calculate_feed_rate(self, frequency):
        mm_per_step = 0.0375  # mm per step (same as in XCarve)
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

    def adjust_movement_distance(self, axis, desired_distance):
        current_position = self.axis_positions[axis]
        direction = self.movement_directions[axis]
        desired_distance *= direction  # Apply current direction

        next_position = current_position + desired_distance

        # Check workspace limits (0 to bed_width_mm or bed_height_mm)
        if axis == 'X':
            max_limit = self.bed_width_mm
        else:
            max_limit = self.bed_height_mm

        if next_position > max_limit:
            # Exceeds upper limit
            adjusted_distance = max_limit - current_position
            self.movement_directions[axis] = -1  # Reverse direction
        elif next_position < 0.0:
            # Exceeds lower limit
            adjusted_distance = -current_position
            self.movement_directions[axis] = 1  # Reverse direction
        else:
            adjusted_distance = desired_distance

        return adjusted_distance

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

    def get_acceleration_settings(self):
        return {'X': 1000, 'Y': 1000}

    def display_loop(self):
        pygame.init()
        self.screen = pygame.display.set_mode((self.window_width, self.window_height))
        pygame.display.set_caption("Virtual CNC Movement")
        self.clock = pygame.time.Clock()

        path_color = (0, 255, 0)  # Green path
        machine_color = (255, 0, 0)  # Red dot
        background_color = (30, 30, 30)  # Dark background
        axis_color = (200, 200, 200)  # Gray color for axes
        crosshair_color = (255, 255, 0)  # Yellow color for crosshairs

        font = pygame.font.SysFont('Arial', 12)

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    self.stop_event.set()

            self.screen.fill(background_color)

            # Draw axes with markings
            self.draw_axes(font, axis_color)

            # Convert positions to screen coordinates
            scaled_positions = [
                (
                    self.axis_to_screen_x(pos['X']),
                    self.axis_to_screen_y(pos['Y'])
                )
                for pos in self.positions
            ]

            # Draw the path
            if len(scaled_positions) > 1:
                pygame.draw.lines(self.screen, path_color, False, scaled_positions, 2)

            # Draw the machine's current position
            current_pos = scaled_positions[-1]
            pygame.draw.circle(self.screen, machine_color, (int(current_pos[0]), int(current_pos[1])), 5)

            # Draw crosshairs
            self.draw_crosshairs(current_pos, crosshair_color)

            pygame.display.flip()
            self.clock.tick(60)  # Limit to 60 FPS

    def axis_to_screen_x(self, x_mm):
        return int(x_mm * self.scale_x)

    def axis_to_screen_y(self, y_mm):
        # Invert Y-axis to have (0,0) at bottom-left corner
        return int(self.window_height - (y_mm * self.scale_y))

    def draw_axes(self, font, axis_color):
        # Draw X-axis
        pygame.draw.line(self.screen, axis_color, (0, self.window_height), (self.window_width, self.window_height), 2)
        # Draw Y-axis
        pygame.draw.line(self.screen, axis_color, (0, 0), (0, self.window_height), 2)

        # Draw tick marks and labels
        tick_interval_mm = 50  # Every 50 mm
        tick_length = 5

        # X-axis ticks
        for x_mm in range(0, int(self.bed_width_mm) + 1, tick_interval_mm):
            x_px = self.axis_to_screen_x(x_mm)
            pygame.draw.line(self.screen, axis_color, (x_px, self.window_height),
                             (x_px, self.window_height - tick_length))
            label = font.render(str(x_mm), True, axis_color)
            self.screen.blit(label,
                             (x_px - label.get_width() // 2, self.window_height - label.get_height() - tick_length))

        # Y-axis ticks
        for y_mm in range(0, int(self.bed_height_mm) + 1, tick_interval_mm):
            y_px = self.axis_to_screen_y(y_mm)
            pygame.draw.line(self.screen, axis_color, (0, y_px), (tick_length, y_px))
            label = font.render(str(y_mm), True, axis_color)
            self.screen.blit(label, (tick_length + 2, y_px - label.get_height() // 2))

    def draw_crosshairs(self, current_pos, crosshair_color):
        x, y = current_pos
        # Vertical line
        pygame.draw.line(self.screen, crosshair_color, (x, 0), (x, self.window_height))
        # Horizontal line
        pygame.draw.line(self.screen, crosshair_color, (0, y), (self.window_width, y))
