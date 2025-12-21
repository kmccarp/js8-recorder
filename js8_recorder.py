#!/usr/bin/env python3
"""
JS8Call RX.DIRECTED message recorder with GUI.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import queue
import subprocess
import platform
from pathlib import Path

from database import Database, format_snr, format_age, get_adjacent_grids
from js8_client import JS8Client


class JS8RecorderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("JS8 Recorder")
        self.root.geometry("900x600")
        self.root.minsize(700, 400)

        # Initialize database
        self.db = Database()

        # Initialize JS8 client
        self.client = JS8Client()

        # Message queue for thread-safe GUI updates
        self.msg_queue = queue.Queue()

        # Set up client callbacks
        self.client.on_message = self._on_message
        self.client.on_grid = self._on_grid
        self.client.on_status = self._on_status
        self.client.on_error = self._on_error

        # Build UI
        self._create_widgets()
        self._load_settings()
        self._refresh_tables()

        # Start processing message queue
        self._process_queue()

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Auto-start if enabled and callsign is configured
        if self.autostart_var.get() and self.callsign_var.get().strip():
            self.root.after(500, self._auto_start)

    def _auto_start(self):
        """Attempt to auto-start listening on launch."""
        self._start_listening()
        # If connection failed, show a warning
        if not self.client.is_running:
            self.root.after(1000, lambda: messagebox.showwarning(
                "Auto-start Failed",
                f"Could not connect to JS8Call at {self.host_var.get()}:{self.port_var.get()}\n\n"
                "Make sure JS8Call is running with the API enabled."
            ))

    def _create_widgets(self):
        """Create all GUI widgets."""
        # Top frame - Configuration
        config_frame = ttk.Frame(self.root, padding="10")
        config_frame.pack(fill=tk.X)

        # Callsign
        ttk.Label(config_frame, text="Callsign:").pack(side=tk.LEFT)
        self.callsign_var = tk.StringVar()
        self.callsign_entry = ttk.Entry(config_frame, textvariable=self.callsign_var, width=12)
        self.callsign_entry.pack(side=tk.LEFT, padx=(5, 15))

        # Host
        ttk.Label(config_frame, text="Host:").pack(side=tk.LEFT)
        self.host_var = tk.StringVar(value="127.0.0.1")
        self.host_entry = ttk.Entry(config_frame, textvariable=self.host_var, width=15)
        self.host_entry.pack(side=tk.LEFT, padx=(5, 15))

        # Port
        ttk.Label(config_frame, text="Port:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value="2442")
        self.port_entry = ttk.Entry(config_frame, textvariable=self.port_var, width=6)
        self.port_entry.pack(side=tk.LEFT, padx=(5, 15))

        # Button frame
        button_frame = ttk.Frame(self.root, padding="5 0 10 10")
        button_frame.pack(fill=tk.X)

        # Start/Stop button
        self.start_button = ttk.Button(button_frame, text="▶ Start Listening", command=self._toggle_listening)
        self.start_button.pack(side=tk.LEFT, padx=5)

        # Export button
        self.export_button = ttk.Button(button_frame, text="Export to Excel", command=self._export_excel)
        self.export_button.pack(side=tk.LEFT, padx=5)

        # Auto-start checkbox
        self.autostart_var = tk.BooleanVar(value=False)
        self.autostart_check = ttk.Checkbutton(
            button_frame, text="Start on launch",
            variable=self.autostart_var, command=self._save_settings
        )
        self.autostart_check.pack(side=tk.LEFT, padx=15)

        # Notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))

        # Messages tab
        messages_frame = ttk.Frame(self.notebook)
        self.notebook.add(messages_frame, text="Directed Messages")

        # Messages treeview
        msg_columns = ("callsign", "qrz", "timestamp", "my_snr", "their_snr", "message")
        self.messages_tree = ttk.Treeview(messages_frame, columns=msg_columns, show="headings")

        self.messages_tree.heading("callsign", text="Callsign")
        self.messages_tree.heading("qrz", text="QRZ")
        self.messages_tree.heading("timestamp", text="Timestamp (UTC)")
        self.messages_tree.heading("my_snr", text="My SNR of Them")
        self.messages_tree.heading("their_snr", text="Their SNR of Me")
        self.messages_tree.heading("message", text="Message")

        self.messages_tree.column("callsign", width=80)
        self.messages_tree.column("qrz", width=50)
        self.messages_tree.column("timestamp", width=140)
        self.messages_tree.column("my_snr", width=100)
        self.messages_tree.column("their_snr", width=100)
        self.messages_tree.column("message", width=300)

        # Scrollbar for messages
        msg_scroll = ttk.Scrollbar(messages_frame, orient=tk.VERTICAL, command=self.messages_tree.yview)
        self.messages_tree.configure(yscrollcommand=msg_scroll.set)

        self.messages_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        msg_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Bind double-click on QRZ column
        self.messages_tree.bind("<Double-1>", self._on_tree_double_click)

        # Grids tab
        grids_frame = ttk.Frame(self.notebook)
        self.notebook.add(grids_frame, text="Callsign Grids")

        # Grids treeview
        grid_columns = ("callsign", "qrz", "grid", "max_my", "min_my", "max_their", "min_their", "last")
        self.grids_tree = ttk.Treeview(grids_frame, columns=grid_columns, show="headings")

        self.grids_tree.heading("callsign", text="Callsign")
        self.grids_tree.heading("qrz", text="QRZ")
        self.grids_tree.heading("grid", text="Grid")
        self.grids_tree.heading("max_my", text="Max My SNR")
        self.grids_tree.heading("min_my", text="Min My SNR")
        self.grids_tree.heading("max_their", text="Max Their SNR")
        self.grids_tree.heading("min_their", text="Min Their SNR")
        self.grids_tree.heading("last", text="Last Contact")

        self.grids_tree.column("callsign", width=80)
        self.grids_tree.column("qrz", width=40)
        self.grids_tree.column("grid", width=60)
        self.grids_tree.column("max_my", width=85)
        self.grids_tree.column("min_my", width=85)
        self.grids_tree.column("max_their", width=95)
        self.grids_tree.column("min_their", width=95)
        self.grids_tree.column("last", width=80)

        # Scrollbar for grids
        grid_scroll = ttk.Scrollbar(grids_frame, orient=tk.VERTICAL, command=self.grids_tree.yview)
        self.grids_tree.configure(yscrollcommand=grid_scroll.set)

        self.grids_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        grid_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Bind double-click on grids tree
        self.grids_tree.bind("<Double-1>", self._on_grids_tree_double_click)

        # Lookup tab
        lookup_frame = ttk.Frame(self.notebook)
        self.notebook.add(lookup_frame, text="Lookup")

        # Search controls
        search_frame = ttk.Frame(lookup_frame, padding="10")
        search_frame.pack(fill=tk.X)

        ttk.Label(search_frame, text="Grid Square:").pack(side=tk.LEFT)
        self.lookup_grid_var = tk.StringVar()
        self.lookup_entry = ttk.Entry(search_frame, textvariable=self.lookup_grid_var, width=10)
        self.lookup_entry.pack(side=tk.LEFT, padx=(5, 10))
        self.lookup_entry.bind("<Return>", lambda e: self._do_lookup())

        ttk.Button(search_frame, text="Search", command=self._do_lookup).pack(side=tk.LEFT)

        # Lookup results treeview
        lookup_columns = ("callsign", "qrz", "grid", "avg_snr", "max_snr", "contacts", "last")
        self.lookup_tree = ttk.Treeview(lookup_frame, columns=lookup_columns, show="headings")

        self.lookup_tree.heading("callsign", text="Callsign")
        self.lookup_tree.heading("qrz", text="QRZ")
        self.lookup_tree.heading("grid", text="Grid")
        self.lookup_tree.heading("avg_snr", text="Avg Their SNR")
        self.lookup_tree.heading("max_snr", text="Max Their SNR")
        self.lookup_tree.heading("contacts", text="Contacts")
        self.lookup_tree.heading("last", text="Last Contact")

        self.lookup_tree.column("callsign", width=80)
        self.lookup_tree.column("qrz", width=40)
        self.lookup_tree.column("grid", width=60)
        self.lookup_tree.column("avg_snr", width=100)
        self.lookup_tree.column("max_snr", width=100)
        self.lookup_tree.column("contacts", width=70)
        self.lookup_tree.column("last", width=80)

        # Scrollbar for lookup
        lookup_scroll = ttk.Scrollbar(lookup_frame, orient=tk.VERTICAL, command=self.lookup_tree.yview)
        self.lookup_tree.configure(yscrollcommand=lookup_scroll.set)

        self.lookup_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lookup_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Bind double-click on lookup tree
        self.lookup_tree.bind("<Double-1>", self._on_lookup_tree_double_click)

        # Configure tag for exact match highlighting
        self.lookup_tree.tag_configure("exact", background="#d4edda")

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        self.count_var = tk.StringVar(value="0 messages")

        status_frame = ttk.Frame(self.root, padding="5 5 10 5")
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)

        ttk.Label(status_frame, textvariable=self.status_var).pack(side=tk.LEFT)
        ttk.Separator(status_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=10, fill=tk.Y)
        ttk.Label(status_frame, textvariable=self.count_var).pack(side=tk.LEFT)

    def _load_settings(self):
        """Load settings from database."""
        callsign = self.db.get_setting("callsign", "")
        host = self.db.get_setting("host", "127.0.0.1")
        port = self.db.get_setting("port", "2442")
        autostart = self.db.get_setting("autostart", "0") == "1"

        self.callsign_var.set(callsign)
        self.host_var.set(host)
        self.port_var.set(port)
        self.autostart_var.set(autostart)

    def _save_settings(self):
        """Save settings to database."""
        self.db.set_setting("callsign", self.callsign_var.get())
        self.db.set_setting("host", self.host_var.get())
        self.db.set_setting("port", self.port_var.get())
        self.db.set_setting("autostart", "1" if self.autostart_var.get() else "0")

    def _refresh_tables(self):
        """Refresh both treeviews from database."""
        # Clear and reload messages
        for item in self.messages_tree.get_children():
            self.messages_tree.delete(item)

        for msg in self.db.get_all_messages():
            self.messages_tree.insert("", 0, values=(
                msg["callsign"],
                "Link",
                msg["timestamp"],
                format_snr(msg["my_snr_of_them"]),
                format_snr(msg["their_snr_of_me"]),
                msg["message"]
            ))

        # Clear and reload grids
        for item in self.grids_tree.get_children():
            self.grids_tree.delete(item)

        for entry in self.db.get_grids_with_snr_stats():
            self.grids_tree.insert("", tk.END, values=(
                entry["callsign"],
                "Link",
                entry["grid"],
                format_snr(entry["max_my_snr"]),
                format_snr(entry["min_my_snr"]),
                format_snr(entry["max_their_snr"]),
                format_snr(entry["min_their_snr"]),
                format_age(entry["last_contact"])
            ))

        # Update count
        count = self.db.get_message_count()
        self.count_var.set(f"{count} message{'s' if count != 1 else ''}")

    def _toggle_listening(self):
        """Toggle start/stop listening."""
        if self.client.is_running:
            self._stop_listening()
        else:
            self._start_listening()

    def _start_listening(self):
        """Start listening for messages."""
        callsign = self.callsign_var.get().strip()
        if not callsign:
            messagebox.showwarning("Missing Callsign", "Please enter your callsign.")
            return

        host = self.host_var.get().strip() or "127.0.0.1"
        try:
            port = int(self.port_var.get().strip() or "2442")
        except ValueError:
            messagebox.showwarning("Invalid Port", "Port must be a number.")
            return

        # Save settings
        self._save_settings()

        # Configure and start client
        self.client.set_config(host, port, callsign)
        if self.client.start():
            self.start_button.configure(text="⏹ Stop Listening")
            self._set_entries_state(tk.DISABLED)
            self.status_var.set(f"Connecting to {host}:{port}...")

    def _stop_listening(self):
        """Stop listening for messages."""
        self.client.stop()
        self.start_button.configure(text="▶ Start Listening")
        self._set_entries_state(tk.NORMAL)
        self.status_var.set("Stopped")

    def _set_entries_state(self, state):
        """Enable or disable config entry fields."""
        self.callsign_entry.configure(state=state)
        self.host_entry.configure(state=state)
        self.port_entry.configure(state=state)

    def _export_excel(self):
        """Export database to Excel file."""
        filepath = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
            initialfile="js8_log.xlsx"
        )
        if filepath:
            try:
                self.db.export_to_excel(filepath)
                messagebox.showinfo("Export Complete", f"Exported to {filepath}")
            except ImportError as e:
                messagebox.showerror("Export Error", str(e))
            except Exception as e:
                messagebox.showerror("Export Error", f"Failed to export: {e}")

    def _open_qrz(self, callsign: str):
        """Open QRZ page for callsign, handling WSL properly."""
        url = f"https://www.qrz.com/db/{callsign}"

        # Check if running in WSL
        is_wsl = "microsoft" in platform.uname().release.lower()

        if is_wsl:
            # Use Windows browser via cmd.exe
            try:
                subprocess.run(["cmd.exe", "/c", "start", url], check=False)
            except Exception:
                # Fallback: copy to clipboard
                self._copy_to_clipboard(url)
                self.status_var.set(f"URL copied to clipboard: {url}")
        else:
            import webbrowser
            webbrowser.open(url)

    def _copy_to_clipboard(self, text: str):
        """Copy text to clipboard."""
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def _on_tree_double_click(self, event):
        """Handle double-click on messages treeview."""
        item = self.messages_tree.identify_row(event.y)
        column = self.messages_tree.identify_column(event.x)

        if item and column == "#2":  # QRZ column
            values = self.messages_tree.item(item, "values")
            if values and values[0]:
                self._open_qrz(values[0])

    def _on_grids_tree_double_click(self, event):
        """Handle double-click on grids treeview."""
        item = self.grids_tree.identify_row(event.y)
        column = self.grids_tree.identify_column(event.x)

        if item and column == "#2":  # QRZ column
            values = self.grids_tree.item(item, "values")
            if values and values[0]:
                self._open_qrz(values[0])

    def _on_lookup_tree_double_click(self, event):
        """Handle double-click on lookup treeview."""
        item = self.lookup_tree.identify_row(event.y)
        column = self.lookup_tree.identify_column(event.x)

        if item and column == "#2":  # QRZ column
            values = self.lookup_tree.item(item, "values")
            if values and values[0]:
                self._open_qrz(values[0])

    def _do_lookup(self):
        """Perform grid lookup search including adjacent grid squares."""
        grid = self.lookup_grid_var.get().strip().upper()
        if not grid:
            return

        if len(grid) < 4:
            self.status_var.set("Grid must be at least 4 characters (e.g., EM48)")
            return

        # Clear existing results
        for item in self.lookup_tree.get_children():
            self.lookup_tree.delete(item)

        # Get results for grid prefix match
        exact_results = self.db.lookup_by_grid(grid)

        # Get results for adjacent grids
        adjacent_grids = get_adjacent_grids(grid)
        adjacent_results = []
        for adj_grid in adjacent_grids:
            adjacent_results.extend(self.db.lookup_by_grid(adj_grid))

        # Sort adjacent results by avg SNR (descending)
        adjacent_results.sort(key=lambda x: x["avg_their_snr"] or -999, reverse=True)

        # Insert exact matches first (highlighted)
        for entry in exact_results:
            avg_snr = entry["avg_their_snr"]
            avg_formatted = format_snr(int(round(avg_snr))) if avg_snr is not None else ""
            self.lookup_tree.insert("", tk.END, values=(
                entry["callsign"],
                "Link",
                entry["grid"],
                avg_formatted,
                format_snr(entry["max_their_snr"]),
                entry["contact_count"],
                format_age(entry["last_contact"])
            ), tags=("exact",))

        # Insert adjacent results
        for entry in adjacent_results:
            avg_snr = entry["avg_their_snr"]
            avg_formatted = format_snr(int(round(avg_snr))) if avg_snr is not None else ""
            self.lookup_tree.insert("", tk.END, values=(
                entry["callsign"],
                "Link",
                entry["grid"],
                avg_formatted,
                format_snr(entry["max_their_snr"]),
                entry["contact_count"],
                format_age(entry["last_contact"])
            ))

        total = len(exact_results) + len(adjacent_results)
        self.status_var.set(f"Found {len(exact_results)} in {grid}* + {len(adjacent_results)} adjacent = {total} callsign(s)")

    # Thread-safe callbacks using queue
    def _on_message(self, record: dict):
        """Called from client thread when a message is received."""
        self.msg_queue.put(("message", record))

    def _on_grid(self, callsign: str, grid: str):
        """Called from client thread when a grid is received."""
        self.msg_queue.put(("grid", (callsign, grid)))

    def _on_status(self, status: str):
        """Called from client thread for status updates."""
        self.msg_queue.put(("status", status))

    def _on_error(self, error: str):
        """Called from client thread for errors."""
        self.msg_queue.put(("error", error))

    def _process_queue(self):
        """Process messages from the queue (runs on main thread)."""
        try:
            while True:
                msg_type, data = self.msg_queue.get_nowait()

                if msg_type == "message":
                    # Add to database
                    self.db.add_message(
                        data["callsign"],
                        data["timestamp"],
                        data["my_snr_of_them"],
                        data["their_snr_of_me"],
                        data["message"]
                    )
                    # Add to treeview at top
                    self.messages_tree.insert("", 0, values=(
                        data["callsign"],
                        "Link",
                        data["timestamp"],
                        format_snr(data["my_snr_of_them"]),
                        format_snr(data["their_snr_of_me"]),
                        data["message"]
                    ))
                    # Update count
                    count = self.db.get_message_count()
                    self.count_var.set(f"{count} message{'s' if count != 1 else ''}")
                    # Refresh grids table to update SNR stats and last contact
                    self._refresh_grids_table()

                elif msg_type == "grid":
                    callsign, grid = data
                    self.db.add_grid(callsign, grid)
                    # Refresh grids tab (simple approach)
                    self._refresh_grids_table()

                elif msg_type == "status":
                    self.status_var.set(data)

                elif msg_type == "error":
                    self.status_var.set(f"Error: {data}")
                    if not self.client.is_running:
                        self.start_button.configure(text="▶ Start Listening")
                        self._set_entries_state(tk.NORMAL)

        except queue.Empty:
            pass

        # Schedule next check
        self.root.after(100, self._process_queue)

    def _refresh_grids_table(self):
        """Refresh just the grids treeview."""
        for item in self.grids_tree.get_children():
            self.grids_tree.delete(item)

        for entry in self.db.get_grids_with_snr_stats():
            self.grids_tree.insert("", tk.END, values=(
                entry["callsign"],
                "Link",
                entry["grid"],
                format_snr(entry["max_my_snr"]),
                format_snr(entry["min_my_snr"]),
                format_snr(entry["max_their_snr"]),
                format_snr(entry["min_their_snr"]),
                format_age(entry["last_contact"])
            ))
        self.grids_tree.update_idletasks()

    def _on_close(self):
        """Handle window close."""
        self.client.stop()
        self.db.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = JS8RecorderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
