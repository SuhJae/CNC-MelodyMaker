import mido
import json
import os
import glob
import math

def midi_to_cnc(midi_file_path, output_file_prefix, output_dir, cnc_axes=2):
    # Open the MIDI file
    mid = mido.MidiFile(midi_file_path)
    
    # Initialize data structures
    note_events = []  # Collect all note events
    tempo = 500000  # Default MIDI tempo (120 BPM)
    ticks_per_beat = mid.ticks_per_beat

    # Convert ticks to seconds
    def ticks_to_seconds(ticks):
        return mido.tick2second(ticks, ticks_per_beat, tempo)

    # Function to convert MIDI note number to note name
    def note_number_to_name(note_number):
        note_names = ['C', 'C#', 'D', 'D#', 'E', 'F',
                      'F#', 'G', 'G#', 'A', 'A#', 'B']
        octave = (note_number // 12) - 1
        note = note_names[note_number % 12]
        return f"{note}{octave}"

    # Process MIDI messages
    for track in mid.tracks:
        abs_time = 0
        for msg in track:
            time_delta = ticks_to_seconds(msg.time)
            abs_time += time_delta
            # Process tempo change
            if msg.type == 'set_tempo':
                tempo = msg.tempo  # Update tempo if there's a tempo change
            # Process note events
            if msg.type in ['note_on', 'note_off']:
                note_number = msg.note
                note_name = note_number_to_name(note_number)
                frequency = midi_note_to_freq(note_number)
                velocity = msg.velocity if msg.type == 'note_on' else 0
                # Add event to the list
                note_events.append({
                    'time': abs_time,
                    'type': 'on' if msg.type == 'note_on' and velocity > 0 else 'off',
                    'note_number': note_number,
                    'note_name': note_name,
                    'frequency': frequency,
                })

    # Sort events by time, and note_off before note_on at the same time
    note_events.sort(key=lambda x: (x['time'], 0 if x['type'] == 'off' else 1))

    # Build note intervals
    note_intervals = []
    active_notes = {}
    for event in note_events:
        time = event['time']
        event_type = event['type']
        note_number = event['note_number']
        note_name = event['note_name']
        frequency = event['frequency']

        if event_type == 'on':
            # Start new note
            active_notes[note_number] = {
                'note_name': note_name,
                'frequency': frequency,
                'start_time': time,
            }
        elif event_type == 'off':
            if note_number in active_notes:
                # End the note
                note_info = active_notes.pop(note_number)
                duration = time - note_info['start_time']
                if duration > 0:
                    note_intervals.append({
                        'note_number': note_number,
                        'note_name': note_info['note_name'],
                        'frequency': note_info['frequency'],
                        'start_time': note_info['start_time'],
                        'end_time': time,
                        'duration': duration,
                    })

    # Sort intervals by start time
    note_intervals.sort(key=lambda x: x['start_time'])

    # Determine maximum number of simultaneous notes
    times = sorted(set([n['start_time'] for n in note_intervals] + [n['end_time'] for n in note_intervals]))
    max_simultaneous_notes = 0
    for t in times:
        count = sum(1 for n in note_intervals if n['start_time'] <= t < n['end_time'])
        if count > max_simultaneous_notes:
            max_simultaneous_notes = count

    print(f"Maximum number of simultaneous notes: {max_simultaneous_notes}")

    # Calculate number of output files needed
    num_output_files = math.ceil(max_simultaneous_notes / cnc_axes)
    
    # Ask user if they want to split into multiple output files
    split_files = False
    if num_output_files > 1:
        user_input = input(f"Do you want to split into {num_output_files} output files? (y/n): ").strip().lower()
        if user_input == 'y':
            split_files = True
        else:
            num_output_files = 1  # If not splitting, set number of output files to 1

    # Prompt for speed multiplier
    try:
        speed_multiplier_input = input("Enter speed multiplier (e.g., 2 for double speed, 0.5 for half speed, default is 1): ").strip()
        speed_multiplier = float(speed_multiplier_input) if speed_multiplier_input else 1.0
    except ValueError:
        speed_multiplier = 1.0

    # Apply speed multiplier to timing
    for interval in note_intervals:
        interval['start_time'] /= speed_multiplier
        interval['end_time'] /= speed_multiplier
        interval['duration'] /= speed_multiplier

    if split_files:
        print(f"Splitting into {num_output_files} output files to accommodate {cnc_axes} axes.")

        # Assign intervals to files without exceeding cnc_axes overlaps
        output_files = assign_intervals_to_files(note_intervals, cnc_axes)

        # Assign axes within each file
        axes = ['X', 'Y']
        for file_idx, file_notes in enumerate(output_files):
            # Sort notes by start_time
            file_notes.sort(key=lambda x: x['start_time'])
            # Assign axes
            for interval in file_notes:
                # Find overlapping notes
                overlapping_notes = [n for n in file_notes if not (n['end_time'] <= interval['start_time'] or n['start_time'] >= interval['end_time'])]
                overlapping_notes.sort(key=lambda x: x['start_time'])
                # Assign axes based on overlap index
                for idx, note in enumerate(overlapping_notes):
                    note['axis'] = axes[idx % cnc_axes]  # Assign axis in a round-robin fashion

            # Save output file
            output_filename = os.path.join(output_dir, f"{output_file_prefix}_{file_idx+1}.json")
            output_data = []
            for note in file_notes:
                output_data.append({
                    'axis': note.get('axis', 'X'),  # Default to 'X' if not assigned
                    'note': note['note_name'],
                    'frequency': note['frequency'],
                    'start_time': note['start_time'],
                    'duration': note['duration'],
                })
            with open(output_filename, 'w') as f:
                json.dump(output_data, f, indent=4)
            print(f"Output saved to {output_filename}")

    else:
        # Process without splitting
        print("Processing without splitting into multiple files.")

        # Assign axes
        axes = ['X', 'Y']
        for interval in note_intervals:
            interval['axis'] = axes[0]  # Default to 'X'

        # Check for overlaps exceeding CNC capacity
        for t in times:
            overlaps = [n for n in note_intervals if n['start_time'] <= t < n['end_time']]
            if len(overlaps) > cnc_axes:
                print(f"Warning: At time {t}s, there are {len(overlaps)} overlapping notes, which exceeds the CNC's capacity.")

        # Save single output file
        output_filename = os.path.join(output_dir, f"{output_file_prefix}.json")
        output_data = []
        for note in note_intervals:
            output_data.append({
                'axis': note.get('axis', 'X'),  # Default to 'X'
                'note': note['note_name'],
                'frequency': note['frequency'],
                'start_time': note['start_time'],
                'duration': note['duration'],
            })
        with open(output_filename, 'w') as f:
            json.dump(output_data, f, indent=4)
        print(f"Output saved to {output_filename}")

def assign_intervals_to_files(note_intervals, cnc_axes):
    """Assign intervals to files ensuring no more than cnc_axes overlaps at any time."""
    output_files = []
    for interval in note_intervals:
        assigned = False
        # Try to assign interval to an existing file
        for file_intervals in output_files:
            # Check if adding this interval to the file exceeds cnc_axes overlaps at any time
            events = []
            for n in file_intervals:
                events.append((n['start_time'], 1))
                events.append((n['end_time'], -1))
            # Add current interval
            events.append((interval['start_time'], 1))
            events.append((interval['end_time'], -1))
            # Sort events by time
            events.sort()
            overlaps = 0
            max_overlaps = 0
            for event in events:
                overlaps += event[1]
                if overlaps > max_overlaps:
                    max_overlaps = overlaps
                if max_overlaps > cnc_axes:
                    break  # Cannot assign to this file
            if max_overlaps <= cnc_axes:
                # Can assign to this file
                file_intervals.append(interval)
                assigned = True
                break
        if not assigned:
            # Create a new file
            output_files.append([interval])
    return output_files

def midi_note_to_freq(midi_note):
    # Convert MIDI note number to frequency
    a4_note = 69
    a4_freq = 440.0
    return round(a4_freq * (2 ** ((midi_note - a4_note) / 12)), 2)

# Example usage
if __name__ == "__main__":
    # Scan midi/ directory for MIDI files
    midi_dir = 'midi'
    midi_files = glob.glob(os.path.join(midi_dir, '*.mid'))
    if not midi_files:
        print(f"No MIDI files found in the '{midi_dir}' directory.")
        exit()
    else:
        print("Available MIDI files:")
        for idx, filepath in enumerate(midi_files):
            filename = os.path.basename(filepath)
            print(f"{idx + 1}: {filename}")

        # Ask the user to select a MIDI file
        while True:
            try:
                choice = int(input("Enter the number of the MIDI file to convert: "))
                if 1 <= choice <= len(midi_files):
                    selected_file = midi_files[choice - 1]
                    break
                else:
                    print(f"Please enter a number between 1 and {len(midi_files)}.")
            except ValueError:
                print("Invalid input. Please enter a number.")

        # Ask for output file prefix
        default_output_prefix = os.path.splitext(os.path.basename(selected_file))[0]
        output_file_prefix = input(f"Enter the output file prefix (e.g., 'output' for output.json, default is '{default_output_prefix}'): ").strip()
        if not output_file_prefix:
            output_file_prefix = default_output_prefix

        # Output directory
        output_dir = 'music'
        os.makedirs(output_dir, exist_ok=True)

        # Run the conversion
        midi_to_cnc(selected_file, output_file_prefix, output_dir)
