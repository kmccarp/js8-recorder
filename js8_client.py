"""
JS8Call API client with threading support.
"""

import socket
import json
import threading
from datetime import datetime
from typing import Callable, Optional


def _parse_snr(value) -> str:
    """Parse SNR to integer string. '+02' -> '2', '-10' -> '-10'"""
    if value is None or value == "":
        return ""
    try:
        return str(int(str(value).replace("+", "")))
    except ValueError:
        return ""


class JS8Client:
    def __init__(self, host: str = "127.0.0.1", port: int = 2442, my_callsign: str = ""):
        self.host = host
        self.port = port
        self.my_callsign = my_callsign.upper() if my_callsign else ""
        self.sock: Optional[socket.socket] = None
        self.running = False
        self.thread: Optional[threading.Thread] = None

        # Callbacks
        self.on_message: Optional[Callable[[dict], None]] = None
        self.on_grid: Optional[Callable[[str, str], None]] = None
        self.on_status: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None

    def set_config(self, host: str, port: int, my_callsign: str):
        """Update connection configuration."""
        self.host = host
        self.port = port
        self.my_callsign = my_callsign.upper() if my_callsign else ""

    def start(self) -> bool:
        """Start listening in a background thread. Returns True if started successfully."""
        if self.running:
            return True

        if not self.my_callsign:
            if self.on_error:
                self.on_error("Callsign is required")
            return False

        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        """Stop listening."""
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def _connect(self) -> bool:
        """Connect to JS8Call API."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)  # Connection timeout
            self.sock.connect((self.host, self.port))
            self.sock.settimeout(1.0)  # Read timeout for periodic checks
            if self.on_status:
                self.on_status(f"Connected to {self.host}:{self.port}")
            return True
        except socket.error as e:
            if self.on_error:
                self.on_error(f"Connection failed: {e}")
            return False

    def _send_command(self, msg_type: str, params: dict = None):
        """Send a command to JS8Call API."""
        if not self.sock:
            return
        try:
            cmd = {"type": msg_type, "params": params or {}}
            self.sock.send((json.dumps(cmd) + "\n").encode())
        except socket.error:
            pass

    def _parse_message(self, data: str) -> Optional[dict]:
        """Parse incoming JSON message from JS8Call."""
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None

    def _process_directed(self, msg: dict):
        """Process an RX.DIRECTED message."""
        params = msg.get("params", {})
        value = msg.get("value", "")

        # Filter to only messages directed at me
        to_call = params.get("TO", "").upper()
        if self.my_callsign and to_call != self.my_callsign:
            return

        # Extract fields
        callsign = params.get("FROM", "")
        grid = params.get("GRID", "").strip()
        snr = _parse_snr(params.get("SNR", ""))

        # UTC timestamp
        utc = params.get("UTC", "")
        if utc:
            timestamp = datetime.utcfromtimestamp(utc / 1000).strftime("%Y-%m-%d %H:%M:%S")
        else:
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # Try to extract their reading of our signal from message text
        their_snr_of_me = ""
        if "SNR" in value.upper():
            parts = value.upper().split("SNR")
            if len(parts) > 1:
                snr_part = parts[1].strip().split()[0] if parts[1].strip() else ""
                their_snr_of_me = _parse_snr(snr_part)

        record = {
            "callsign": callsign,
            "timestamp": timestamp,
            "my_snr_of_them": snr,
            "their_snr_of_me": their_snr_of_me,
            "message": value,
            "grid": grid
        }

        if self.on_message:
            self.on_message(record)

        # Track grid square
        if callsign and grid and self.on_grid:
            self.on_grid(callsign, grid)

    def _run(self):
        """Main loop - runs in background thread."""
        if not self._connect():
            self.running = False
            return

        # Request current call activity to get initial grid data
        self._send_command("RX.GET_CALL_ACTIVITY")

        buffer = ""

        while self.running:
            try:
                data = self.sock.recv(4096).decode("utf-8")
                if not data:
                    if self.on_status:
                        self.on_status("Connection closed by server")
                    break

                buffer += data

                # Process complete JSON messages (newline-delimited)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue

                    msg = self._parse_message(line)
                    if not msg:
                        continue

                    msg_type = msg.get("type", "")

                    if msg_type == "RX.DIRECTED":
                        self._process_directed(msg)
                    elif msg_type == "RX.CALL_ACTIVITY":
                        # Extract grid info from call activity
                        params = msg.get("params", {})
                        for call, info in params.items():
                            if isinstance(info, dict) and info.get("GRID") and self.on_grid:
                                self.on_grid(call, info["GRID"].strip())

            except socket.timeout:
                continue
            except socket.error as e:
                if self.running:
                    if self.on_error:
                        self.on_error(f"Connection error: {e}")
                break

        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        if self.on_status:
            self.on_status("Disconnected")

    @property
    def is_running(self) -> bool:
        return self.running
