import numpy as np
import json
import time
import os
import glob
import re
import threading
import tkinter as tk
from tkinter import ttk
import pyaudio

# Function to calculate feed rate based on frequency (from CNC code)
def calculate_feed_rate(frequency):
    mm_per_step = 0.0375  # mm per step (calculated from machine specs)
    feed_rate = frequency * mm_per_step * 60 * 2  # in mm/min
    return feed_rate

# Function to calculate actual frequency from feed rate
def calculate_actual_frequency(feed_rate):
    mm_per_step = 0.0375  # mm per step (calculated from machine specs)
    frequency = feed_rate / (mm_per_step * 60)
    return frequency

def generate_sine_wave(frequency, duration, sample_rate=44100, volume=0.5, fade_duration=0.01):
    """
    Generate a sine wave with optional fade-in and fade-out to prevent popping sounds.
    """
    num_samples = int(sample_rate * duration)
    if num_samples == 0:
        return np.array([], dtype=np.int16)

    t = np.linspace(0, duration, num_samples, False)
    wave = np.sin(2 * np.pi * frequency * t)

    # Calculate fade-in and fade-out samples
    fade_in_samples = int(sample_rate * fade_duration)
    fade_out_samples = fade_in_samples  # Using the same duration for fade-out

    # Ensure fade durations do not exceed half the length of the wave
    max_fade_samples = num_samples // 2
    fade_in_samples = min(fade_in_samples, max_fade_samples)
    fade_out_samples = min(fade_out_samples, max_fade_samples)

    # Apply fade-in
    if fade_in_samples > 0:
        fade_in_envelope = np.linspace(0, 1, fade_in_samples)
        wave[:fade_in_samples] *= fade_in_envelope

    # Apply fade-out
    if fade_out_samples > 0:
        fade_out_envelope = np.linspace(1, 0, fade_out_samples)
        wave[-fade_out_samples:] *= fade_out_envelope

    audio = wave * (2**15 - 1) * volume
    return audio.astype(np.int16)

class AudioPlayer:
    def __init__(self, json_files):
        self.json_files = json_files  # List of JSON file paths
        self.num_files = len(json_files)
        self.notes_per_file = []  # List of notes per file
        self.audio_buffers_per_file = []  # List of audio buffers per file
        self.channels_enabled = [True] * self.num_files  # List indicating if each channel is enabled
        self.axes_enabled = {'X': True, 'Y': True}  # Dict indicating if axis is enabled
        self.total_duration = 0
        self.sample_rate = 44100
        self.chunk_size = 1024  # Number of frames per buffer
        self.playback_thread = None
        self.playback_event = threading.Event()
        self.pause_event = threading.Event()
        self.lock = threading.Lock()
        self.current_time = 0
        self.seek_time = None  # Time to seek to

        # Prepare audio data
        self.prepare_audio()

    def prepare_audio(self):
        # Load notes from JSON files
        max_duration = 0
        for idx, json_file in enumerate(self.json_files):
            with open(json_file, 'r') as f:
                notes = json.load(f)
            self.notes_per_file.append(notes)
            # Determine total duration
            if notes:
                file_duration = max(note['start_time'] + note['duration'] for note in notes)
                if file_duration > max_duration:
                    max_duration = file_duration

        self.total_duration = max_duration

        # Now that we have total_duration, we can generate audio buffers
        for idx, notes in enumerate(self.notes_per_file):
            # Prepare audio buffer for this file
            audio_buffer = self.generate_audio_buffer(notes)
            self.audio_buffers_per_file.append(audio_buffer)


    def generate_audio_buffer(self, notes):
        total_samples = int(self.sample_rate * self.total_duration)
        audio_buffer = np.zeros((total_samples, 2), dtype=np.float32)
        max_feed_rate = 8000  # Maximum allowed feed rate

        for note in notes:
            axis = note['axis']
            original_frequency = note['frequency'] / 2
            duration = note['duration']
            start_sample = int(note['start_time'] * self.sample_rate)
            end_sample = start_sample + int(duration * self.sample_rate)
            volume = 0.5  # Adjust volume as needed

            # Skip notes with non-positive duration
            if duration <= 0:
                continue

            # Correct start_sample and end_sample if necessary
            start_sample = max(0, start_sample)
            end_sample = max(start_sample, end_sample)

            # Calculate feed rate
            feed_rate = calculate_feed_rate(original_frequency)

            # Limit feed rate to max_feed_rate
            if feed_rate > max_feed_rate:
                feed_rate = max_feed_rate

            # Calculate actual frequency based on feed rate
            actual_frequency = calculate_actual_frequency(feed_rate)

            # Generate the sine wave for the note using the actual frequency with fade-in/out
            wave = generate_sine_wave(actual_frequency, duration, self.sample_rate, volume)

            # Ensure we don't exceed the buffer length
            total_samples = audio_buffer.shape[0]
            if end_sample > total_samples:
                wave = wave[:total_samples - start_sample]
                end_sample = total_samples

            # Ensure wave length matches the buffer segment
            buffer_length = end_sample - start_sample
            wave_length = len(wave)
            if wave_length < buffer_length:
                # Pad the wave with zeros if it's shorter
                wave = np.pad(wave, (0, buffer_length - wave_length), 'constant')

            # Create stereo channels
            if axis == 'X':
                # Left channel
                audio_buffer[start_sample:end_sample, 0] += wave[:buffer_length]
            elif axis == 'Y':
                # Right channel
                audio_buffer[start_sample:end_sample, 1] += wave[:buffer_length]
            else:
                # Both channels
                audio_buffer[start_sample:end_sample, 0] += wave[:buffer_length] * 0.5
                audio_buffer[start_sample:end_sample, 1] += wave[:buffer_length] * 0.5

        # Normalize audio buffer
        max_value = np.max(np.abs(audio_buffer))
        if max_value > 0:
            audio_buffer = audio_buffer / max_value

        return audio_buffer

    def play(self):
        self.playback_event.clear()
        self.pause_event.clear()
        self.playback_thread = threading.Thread(target=self.playback_loop)
        self.playback_thread.start()

    def stop(self):
        self.playback_event.set()
        if self.playback_thread:
            self.playback_thread.join()

    def playback_loop(self):
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paInt16,
                        channels=2,
                        rate=self.sample_rate,
                        output=True,
                        frames_per_buffer=self.chunk_size)

        total_samples = int(self.sample_rate * self.total_duration)
        current_sample = int(self.current_time * self.sample_rate)

        while current_sample < total_samples and not self.playback_event.is_set():
            # Handle pause
            if self.pause_event.is_set():
                time.sleep(0.1)
                continue

            # Handle seeking
            if self.seek_time is not None:
                with self.lock:
                    current_sample = int(self.seek_time * self.sample_rate)
                    self.seek_time = None

            # Calculate end sample for this chunk
            end_sample = min(current_sample + self.chunk_size, total_samples)

            # Mix audio from enabled channels
            mixed_buffer = np.zeros((end_sample - current_sample, 2), dtype=np.float32)
            with self.lock:
                for idx, buffer in enumerate(self.audio_buffers_per_file):
                    if self.channels_enabled[idx]:
                        mixed_buffer += buffer[current_sample:end_sample]

                # Apply axes enabled
                if not self.axes_enabled['X']:
                    mixed_buffer[:, 0] = 0
                if not self.axes_enabled['Y']:
                    mixed_buffer[:, 1] = 0

            # Normalize mixed buffer to prevent clipping
            max_value = np.max(np.abs(mixed_buffer))
            if max_value > 0:
                mixed_buffer = mixed_buffer / max_value * 0.8  # Adjust volume as needed

            # Convert to int16
            mixed_buffer = (mixed_buffer * 32767).astype(np.int16)

            # Interleave channels and write to stream
            interleaved = np.empty((mixed_buffer.size,), dtype=np.int16)
            interleaved[0::2] = mixed_buffer[:, 0]
            interleaved[1::2] = mixed_buffer[:, 1]
            stream.write(interleaved.tobytes())

            # Update current time
            current_sample = end_sample
            self.current_time = current_sample / self.sample_rate

        stream.stop_stream()
        stream.close()
        p.terminate()

    def toggle_channel(self, idx):
        with self.lock:
            self.channels_enabled[idx] = not self.channels_enabled[idx]

    def toggle_axis(self, axis):
        with self.lock:
            self.axes_enabled[axis] = not self.axes_enabled[axis]

    def pause_playback(self):
        if self.pause_event.is_set():
            # Resume playback
            self.pause_event.clear()
        else:
            # Pause playback
            self.pause_event.set()

    def seek(self, time_position):
        with self.lock:
            self.seek_time = time_position
            self.current_time = time_position

    def get_progress(self):
        return self.current_time, self.total_duration

def get_songs_in_music_folder(music_folder):
    json_files = glob.glob(os.path.join(music_folder, '*.json'))

    song_dict = {}  # key: base name, value: list of files

    for filepath in json_files:
        filename = os.path.basename(filepath)
        # Use regex to extract base name
        match = re.match(r'(.*?)(?:_(\d+))?\.json$', filename)
        if match:
            base_name = match.group(1)
            index = match.group(2)
            if base_name not in song_dict:
                song_dict[base_name] = []
            song_dict[base_name].append(filepath)
    return song_dict

def main():
    # Directory containing JSON files
    music_folder = 'music'

    # Get songs in music folder
    song_dict = get_songs_in_music_folder(music_folder)

    if not song_dict:
        print(f"No JSON files found in the '{music_folder}' directory.")
        return
    else:
        print("Available songs:")
        song_list = []
        for idx, (base_name, file_list) in enumerate(sorted(song_dict.items())):
            if len(file_list) > 1:
                print(f"{idx + 1}: {base_name} (multi-channel)")
            else:
                print(f"{idx + 1}: {base_name}")
            song_list.append((base_name, file_list))

        # Ask the user to select a song to play
        while True:
            try:
                choice = int(input("Enter the number of the song you want to play: "))
                if 1 <= choice <= len(song_list):
                    selected_base_name, selected_files = song_list[choice - 1]
                    break
                else:
                    print(f"Please enter a number between 1 and {len(song_list)}.")
            except ValueError:
                print("Invalid input. Please enter a number.")

        print(f"Playing '{selected_base_name}'...")
        player = AudioPlayer(selected_files)

        # Start GUI
        root = tk.Tk()
        root.title(f"Playing: {selected_base_name}")

        # Progress bar and time labels
        progress_var = tk.DoubleVar()
        progress_bar = ttk.Scale(root, variable=progress_var, from_=0, to=player.total_duration, orient='horizontal', length=400)
        progress_bar.pack(pady=5)

        time_frame = tk.Frame(root)
        time_frame.pack()
        time_label = tk.Label(time_frame, text="00:00")
        time_label.pack(side='left')
        time_separator = tk.Label(time_frame, text=" / ")
        time_separator.pack(side='left')
        total_time_label = tk.Label(time_frame, text=time.strftime('%M:%S', time.gmtime(player.total_duration)))
        total_time_label.pack(side='left')

        # Buttons
        button_frame = tk.Frame(root)
        button_frame.pack(pady=5)
        pause_button = tk.Button(button_frame, text="Pause", command=player.pause_playback)
        pause_button.pack(side='left', padx=5)

        # Axis controls
        control_frame = tk.Frame(root)
        control_frame.pack()

        # Channel controls
        channel_frame = tk.Frame(root)
        channel_frame.pack()

        axes = ['X', 'Y']
        axis_vars = {}
        for axis in axes:
            var = tk.IntVar(value=1)
            axis_vars[axis] = var
            check = tk.Checkbutton(control_frame, text=f"Axis {axis}", variable=var,
                                   command=lambda axis=axis, var=var: player.toggle_axis(axis))
            check.pack(side='left', padx=5)

        channel_vars = []
        for idx, json_file in enumerate(player.json_files):
            var = tk.IntVar(value=1)
            channel_vars.append(var)
            channel_name = f"Channel {idx + 1}"
            check = tk.Checkbutton(channel_frame, text=channel_name, variable=var,
                                   command=lambda idx=idx, var=var: player.toggle_channel(idx))
            check.pack(side='left', padx=5)

        # Update progress bar and time labels
        def update_progress():
            current_time, total_duration = player.get_progress()
            progress_var.set(current_time)
            time_label.config(text=time.strftime('%M:%S', time.gmtime(current_time)))
            if current_time < total_duration:
                root.after(100, update_progress)
            else:
                root.destroy()

        # Handle progress bar drag
        def on_progress_drag(event):
            new_time = progress_var.get()
            player.seek(new_time)

        progress_bar.bind("<ButtonRelease-1>", on_progress_drag)

        # Start playback and GUI
        player.play()
        update_progress()
        root.mainloop()
        player.stop()

if __name__ == "__main__":
    main()
