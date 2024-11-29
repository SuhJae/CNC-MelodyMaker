# main.py

import glob
import json
import os
import sys

from profiles.virtual_cnc import VirtualCNC
# Import profiles
from profiles.x_carve import XCarve


def choose_machine():
    machines = {
        'x_carve': XCarve,
        'virtual_cnc': VirtualCNC,
    }
    print("Available CNC machines:")
    for i, key in enumerate(machines.keys()):
        print(f"{i}: {key}")
    choice = input("Select the machine number: ").strip()
    try:
        choice = int(choice)
        machine_name = list(machines.keys())[choice]
        return machines[machine_name]()
    except (ValueError, IndexError):
        print("Invalid selection.")
        sys.exit()


def choose_song():
    json_files = glob.glob("music/*.json")
    if not json_files:
        print("No JSON files found in the 'music' folder.")
        return None

    print("\nAvailable JSON files:")
    for i, file in enumerate(json_files):
        print(f"{i}: {os.path.basename(file)}")
    choice = input("Select the file number to play: ").strip()
    try:
        choice = int(choice)
        if 0 <= choice < len(json_files):
            json_file = json_files[choice]
            with open(json_file, 'r') as f:
                notes = json.load(f)
            return notes
        else:
            print("Invalid selection.")
            return None
    except ValueError:
        print("Invalid input.")
        return None


def main():
    cnc_machine = choose_machine()
    if not cnc_machine.connect():
        print("Failed to connect to the machine.")
        return

    try:
        cnc_machine.initialize()
        notes = choose_song()
        if notes:
            if isinstance(cnc_machine, VirtualCNC):
                cnc_machine.run(notes)
            else:
                cnc_machine.play_notes(notes)
        else:
            print("No song selected.")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        cnc_machine.disconnect()


if __name__ == "__main__":
    main()
