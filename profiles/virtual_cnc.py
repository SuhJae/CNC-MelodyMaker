# profiles/virtual_cnc.py

import math
import queue
import threading
import time

import numpy as np
import pyaudio
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

        # Margin in pixels
        self.margin = 20

        # Scaling factors to convert mm to pixels, accounting for margins
        drawable_width = self.window_width - 2 * self.margin
        drawable_height = self.window_height - 2 * self.margin
        self.scale_x = drawable_width / self.bed_width_mm
        self.scale_y = drawable_height / self.bed_height_mm

        # Starting position at (0, 0) mm
        self.axis_positions = {'X': 0.0, 'Y': 0.0}

        # Define working bounds similar to XCarve
        self.working_bound_1 = {'X': 100, 'Y': 100}  # Lower bounds
        self.working_bound_2 = {'X': 300, 'Y': 300}  # Upper bounds

        # Direction attribute to manage movement direction for each axis
        self.direction = {'X': 1, 'Y': 1}  # Default direction for each axis

        # Maximum travel distance from the lower bound for each axis
        self.max_travel = {'X': 50.0, 'Y': 50.0}

        self.command_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.worker_thread = None
        self.play_notes_thread = None
        self.positions = [self.axis_positions.copy()]
        self.screen = None
        self.clock = None
        self.running = False

        # Colors and font initialized in run() after pygame.init()
        self.path_color = (0, 255, 0)  # Green path
        self.machine_color = (255, 0, 0)  # Red dot
        self.background_color = (30, 30, 30)  # Dark background
        self.axis_color = (200, 200, 200)  # Gray color for axes
        self.crosshair_color = (255, 255, 0)  # Yellow color for crosshairs

        # Audio settings
        self.sample_rate = 44100
        self.chunk_size = 1024
        self.pyaudio_instance = pyaudio.PyAudio()
        self.audio_stream = self.pyaudio_instance.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            output=True,
            frames_per_buffer=self.chunk_size
        )
        self.audio_queue = queue.Queue()

    def connect(self):
        # No physical connection needed
        return True

    def get_available_ports(self):
        pass

    def load_config(self):
        pass

    def save_config(self):
        pass

    def disconnect(self):
        self.stop_event.set()
        self.running = False
        if self.worker_thread:
            self.worker_thread.join()
        if self.play_notes_thread:
            self.play_notes_thread.join()
        if self.audio_stream:
            self.audio_stream.stop_stream()
            self.audio_stream.close()
            self.pyaudio_instance.terminate()
        pygame.quit()

    def initialize(self):
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

    def audio_worker(self):
        while not self.stop_event.is_set():
            try:
                audio_data = self.audio_queue.get(timeout=0.1)
                self.audio_stream.write(audio_data)
                self.audio_queue.task_done()
            except queue.Empty:
                continue

    def play_notes(self, notes):
        # Initialize variables
        self.play_notes_start_time = time.time()
        acceleration_settings = self.get_acceleration_settings()

        # Start audio worker thread
        self.audio_thread = threading.Thread(target=self.audio_worker)
        self.audio_thread.start()

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

                # Adjust direction based on current position and working bounds
                self.choose_direction(axis, note_distance)
                note_distance *= self.direction[axis]

                # Update current position
                self.axis_positions[axis] += note_distance

                # Store calculated values
                distances[axis] = note_distance
                required_feed_rates[axis] = note_feed_rate
                movement_durations[axis] = interval_duration

                # Generate tone for the note over its overlap duration
                overlap_duration = min(note['duration'], interval_duration)
                self.play_tone(frequency, overlap_duration)

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

        # Signal that playback is complete
        self.running = False
        self.stop_event.set()
        self.audio_thread.join()

    def play_tone(self, frequency, duration):
        # Generate sine wave
        sample_count = int(self.sample_rate * duration)
        t = np.linspace(0, duration, sample_count, False)
        wave = np.sin(2 * np.pi * frequency * t)
        audio = wave * (2 ** 15 - 1) * 0.5  # volume adjustment
        audio = audio.astype(np.int16).tobytes()
        self.audio_queue.put(audio)

    def calculate_feed_rate(self, frequency):
        mm_per_step = 0.0375  # mm per step (same as in XCarve)
        feed_rate = frequency * mm_per_step * 60  # in mm/min
        return feed_rate

    def calculate_combined_feed_rate(self, distances, required_feed_rates):
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

    def calculate_movement_time(self, distances, combined_feed_rate, acceleration_settings):
        """
        Calculate the time required to complete the movement based on distances,
        combined feed rate, and acceleration settings.

        Parameters:
        - distances (dict): Movement distances for each axis.
        - combined_feed_rate (float): The combined feed rate in mm/min.
        - acceleration_settings (dict): Acceleration settings for each axis.

        Returns:
        - float: Time in seconds required to complete the movement.
        """
        if combined_feed_rate == 0:
            return 0

        # Convert feed rate from mm/min to mm/s
        feed_rate_mm_per_sec = combined_feed_rate / 60.0

        # Calculate the total distance using Euclidean distance
        total_distance = math.sqrt(sum(d ** 2 for d in distances.values()))

        # Calculate time based on feed rate and total distance
        # Time = Distance / Speed
        movement_time = total_distance / feed_rate_mm_per_sec

        # Optional: Adjust time based on acceleration settings
        # This is a simplified approach; for more accurate simulation,
        # consider acceleration and deceleration phases.

        return movement_time

    def choose_direction(self, axis, movement_distance):
        """
        Adjust the movement direction based on working bounds.
        Similar to XCarve's choose_direction method.
        """
        current_position = self.axis_positions[axis]
        min_bound = self.working_bound_1[axis]
        max_bound = self.working_bound_2[axis]
        proposed_position = current_position + (movement_distance * self.direction[axis])

        # Check if the proposed position is within bounds
        if min_bound <= proposed_position <= max_bound:
            # If the proposed movement is within bounds, retain the current direction
            return
        else:
            # If the proposed position goes out of bounds, reverse direction
            if proposed_position < min_bound:
                # If it's below the lower bound, set direction to move positively
                self.direction[axis] = 1
            elif proposed_position > max_bound:
                # If it's above the upper bound, set direction to move negatively
                self.direction[axis] = -1

    def get_acceleration_settings(self):
        return {'X': 1000, 'Y': 1000}

    def run(self, notes):
        # Initialize Pygame in main thread
        pygame.init()
        self.screen = pygame.display.set_mode((self.window_width, self.window_height))
        pygame.display.set_caption("Virtual CNC Movement")
        self.clock = pygame.time.Clock()

        self.font = pygame.font.SysFont('Arial', 12)

        # Start the worker thread that processes G-code commands
        self.worker_thread = threading.Thread(target=self.serial_worker)
        self.worker_thread.start()

        # Start the thread that plays notes
        self.play_notes_thread = threading.Thread(target=self.play_notes, args=(notes,))
        self.play_notes_thread.start()

        # Main event loop
        self.running = True

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    self.stop_event.set()

            self.screen.fill(self.background_color)

            # Draw axes with markings
            self.draw_axes(self.font, self.axis_color)

            # Convert positions to screen coordinates
            with self.lock:
                scaled_positions = [
                    (
                        self.axis_to_screen_x(pos['X']),
                        self.axis_to_screen_y(pos['Y'])
                    )
                    for pos in self.positions
                ]
                # Draw the path
                if len(scaled_positions) > 1:
                    pygame.draw.lines(self.screen, self.path_color, False, scaled_positions, 2)

                # Draw the machine's current position
                current_pos = scaled_positions[-1]
                pygame.draw.circle(self.screen, self.machine_color, (int(current_pos[0]), int(current_pos[1])), 5)

                # Draw crosshairs
                self.draw_crosshairs(current_pos, self.crosshair_color)

            pygame.display.flip()
            self.clock.tick(60)  # Limit to 60 FPS

        # Wait for threads to finish
        self.play_notes_thread.join()
        self.worker_thread.join()
        pygame.quit()

    def axis_to_screen_x(self, x_mm):
        """
        Convert X axis position in mm to screen X coordinate in pixels,
        accounting for the left margin.
        """
        return int(x_mm * self.scale_x) + self.margin

    def axis_to_screen_y(self, y_mm):
        """
        Convert Y axis position in mm to screen Y coordinate in pixels,
        accounting for the bottom margin.
        """
        return int(self.window_height - (y_mm * self.scale_y)) - self.margin

    def draw_axes(self, font, axis_color):
        # Draw X-axis
        pygame.draw.line(
            self.screen,
            axis_color,
            (self.margin, self.window_height - self.margin),
            (self.window_width - self.margin, self.window_height - self.margin),
            2
        )
        # Draw Y-axis
        pygame.draw.line(
            self.screen,
            axis_color,
            (self.margin, self.margin),
            (self.margin, self.window_height - self.margin),
            2
        )

        # Draw tick marks and labels
        tick_interval_mm = 50  # Every 50 mm
        tick_length = 5

        # X-axis ticks
        for x_mm in range(0, int(self.bed_width_mm) + 1, tick_interval_mm):
            x_px = self.axis_to_screen_x(x_mm)
            pygame.draw.line(
                self.screen,
                axis_color,
                (x_px, self.window_height - self.margin),
                (x_px, self.window_height - self.margin - tick_length),
                1
            )
            label = font.render(str(x_mm), True, axis_color)
            label_rect = label.get_rect(
                center=(x_px, self.window_height - self.margin + tick_length + label.get_height() // 2))
            self.screen.blit(label, label_rect)

        # Y-axis ticks
        for y_mm in range(0, int(self.bed_height_mm) + 1, tick_interval_mm):
            y_px = self.axis_to_screen_y(y_mm)
            pygame.draw.line(
                self.screen,
                axis_color,
                (self.margin, y_px),
                (self.margin + tick_length, y_px),
                1
            )
            label = font.render(str(y_mm), True, axis_color)
            label_rect = label.get_rect(midleft=(self.margin + tick_length + 2, y_px))
            self.screen.blit(label, label_rect)

    def draw_crosshairs(self, current_pos, crosshair_color):
        x, y = current_pos
        # Vertical line
        pygame.draw.line(self.screen, crosshair_color, (x, self.margin), (x, self.window_height - self.margin), 1)
        # Horizontal line
        pygame.draw.line(self.screen, crosshair_color, (self.margin, y), (self.window_width - self.margin, y), 1)
