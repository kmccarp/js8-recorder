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

from database import Database
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
        grid_columns = ("callsign", "qrz", "grid")
        self.grids_tree = ttk.Treeview(grids_frame, columns=grid_columns, show="headings")

        self.grids_tree.heading("callsign", text="Callsign")
        self.grids_tree.heading("qrz", text="QRZ")
        self.grids_tree.heading("grid", text="Grid Square")

        self.grids_tree.column("callsign", width=100)
        self.grids_tree.column("qrz", width=50)
        self.grids_tree.column("grid", width=100)

        # Scrollbar for grids
        grid_scroll = ttk.Scrollbar(grids_frame, orient=tk.VERTICAL, command=self.grids_tree.yview)
        self.grids_tree.configure(yscrollcommand=grid_scroll.set)

        self.grids_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        grid_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Bind double-click on grids tree
        self.grids_tree.bind("<Double-1>", self._on_grids_tree_double_click)

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

        self.callsign_var.set(callsign)
        self.host_var.set(host)
        self.port_var.set(port)

    def _save_settings(self):
        """Save settings to database."""
        self.db.set_setting("callsign", self.callsign_var.get())
        self.db.set_setting("host", self.host_var.get())
        self.db.set_setting("port", self.port_var.get())

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
                msg["my_snr_of_them"],
                msg["their_snr_of_me"],
                msg["message"]
            ))

        # Clear and reload grids
        for item in self.grids_tree.get_children():
            self.grids_tree.delete(item)

        for grid_entry in self.db.get_all_grids():
            self.grids_tree.insert("", tk.END, values=(
                grid_entry["callsign"],
                "Link",
                grid_entry["grid"]
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
                        data["my_snr_of_them"],
                        data["their_snr_of_me"],
                        data["message"]
                    ))
                    # Update count
                    count = self.db.get_message_count()
                    self.count_var.set(f"{count} message{'s' if count != 1 else ''}")

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

        for grid_entry in self.db.get_all_grids():
            self.grids_tree.insert("", tk.END, values=(
                grid_entry["callsign"],
                "Link",
                grid_entry["grid"]
            ))

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
