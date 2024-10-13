import abc

class CNCMachine(abc.ABC):
    @abc.abstractmethod
    def __init__(self):
        pass

    @abc.abstractmethod
    def connect(self):
        pass

    @abc.abstractmethod
    def disconnect(self):
        pass

    @abc.abstractmethod
    def initialize(self):
        pass

    @abc.abstractmethod
    def play_notes(self, notes):
        pass

    @abc.abstractmethod
    def send_gcode(self, command):
        pass

    @abc.abstractmethod
    def get_available_ports(self):
        pass

    @abc.abstractmethod
    def load_config(self):
        pass

    @abc.abstractmethod
    def save_config(self, port):
        pass
