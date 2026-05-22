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

from database import Database, format_snr, format_age, get_adjacent_grids, grid_to_latlon
from js8_client import JS8Client

try:
    import tkintermapview
    HAS_MAP = True
except ImportError:
    HAS_MAP = False


# Map marker color tiers, ordered from strongest SNR to weakest.
# Each tier colors any SNR >= min_snr that didn't match a higher tier.
# The final tier acts as the catch-all (use a very low min_snr).
# Future: this list will be user-editable; keep it as plain data so an
# edit dialog can mutate it and call _build_legend + _refresh_map.
DEFAULT_SNR_THRESHOLDS = [
    {"min_snr": 0,    "color": "green",  "label": "Strong (≥ 0 dB)"},
    {"min_snr": -10,  "color": "yellow", "label": "Good (-1 to -10 dB)"},
    {"min_snr": -20,  "color": "orange", "label": "Marginal (-11 to -20 dB)"},
    {"min_snr": -999, "color": "red",    "label": "Weak (< -20 dB)"},
]
NO_DATA_COLOR = "gray"
NO_DATA_LABEL = "No SNR data"


class JS8RecorderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("JS8 Recorder")
        self.root.geometry("900x600")
        self.root.minsize(700, 400)

        # Initialize database
        self.db = Database()

        # Per-instance copy so a future edit dialog can mutate without
        # touching the module default.
        self.snr_thresholds = [dict(t) for t in DEFAULT_SNR_THRESHOLDS]

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
        if HAS_MAP:
            self._refresh_map()

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

        # Container for treeview and scrollbar
        msg_tree_frame = ttk.Frame(messages_frame)
        msg_tree_frame.pack(fill=tk.BOTH, expand=True)

        # Messages treeview
        msg_columns = ("callsign", "qrz", "timestamp", "band", "my_snr", "their_snr", "message")
        self.messages_tree = ttk.Treeview(msg_tree_frame, columns=msg_columns, show="headings")

        self.messages_tree.heading("callsign", text="Callsign")
        self.messages_tree.heading("qrz", text="QRZ")
        self.messages_tree.heading("timestamp", text="Timestamp (UTC)")
        self.messages_tree.heading("band", text="Band")
        self.messages_tree.heading("my_snr", text="My SNR of Them")
        self.messages_tree.heading("their_snr", text="Their SNR of Me")
        self.messages_tree.heading("message", text="Message")

        self.messages_tree.column("callsign", width=80)
        self.messages_tree.column("qrz", width=50)
        self.messages_tree.column("timestamp", width=140)
        self.messages_tree.column("band", width=50)
        self.messages_tree.column("my_snr", width=100)
        self.messages_tree.column("their_snr", width=100)
        self.messages_tree.column("message", width=280)

        # Scrollbar for messages
        msg_scroll = ttk.Scrollbar(msg_tree_frame, orient=tk.VERTICAL, command=self.messages_tree.yview)
        self.messages_tree.configure(yscrollcommand=msg_scroll.set)

        self.messages_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        msg_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Button frame for messages tab
        msg_button_frame = ttk.Frame(messages_frame, padding="5")
        msg_button_frame.pack(fill=tk.X)
        ttk.Button(msg_button_frame, text="Delete Selected", command=self._delete_selected_messages).pack(side=tk.LEFT)

        # Context menu for messages
        self.messages_menu = tk.Menu(self.root, tearoff=0)
        self.messages_menu.add_command(label="Delete Selected", command=self._delete_selected_messages)

        # Bind events
        self.messages_tree.bind("<Double-1>", self._on_tree_double_click)
        self.messages_tree.bind("<Button-3>", self._show_messages_menu)
        self.messages_tree.bind("<Delete>", lambda e: self._delete_selected_messages())

        # Tag for messages received during this session
        self.messages_tree.tag_configure("session", background="#d4edda")

        # Grids tab
        grids_frame = ttk.Frame(self.notebook)
        self.notebook.add(grids_frame, text="Callsign Grids")

        # Container for treeview and scrollbar
        grid_tree_frame = ttk.Frame(grids_frame)
        grid_tree_frame.pack(fill=tk.BOTH, expand=True)

        # Grids treeview
        grid_columns = ("callsign", "qrz", "grid", "bands", "max_my", "min_my", "max_their", "min_their", "last")
        self.grids_tree = ttk.Treeview(grid_tree_frame, columns=grid_columns, show="headings")

        self.grids_tree.heading("callsign", text="Callsign")
        self.grids_tree.heading("qrz", text="QRZ")
        self.grids_tree.heading("grid", text="Grid")
        self.grids_tree.heading("bands", text="Bands")
        self.grids_tree.heading("max_my", text="Max My SNR")
        self.grids_tree.heading("min_my", text="Min My SNR")
        self.grids_tree.heading("max_their", text="Max Their SNR")
        self.grids_tree.heading("min_their", text="Min Their SNR")
        self.grids_tree.heading("last", text="Last Contact")

        self.grids_tree.column("callsign", width=80)
        self.grids_tree.column("qrz", width=40)
        self.grids_tree.column("grid", width=60)
        self.grids_tree.column("bands", width=80)
        self.grids_tree.column("max_my", width=80)
        self.grids_tree.column("min_my", width=80)
        self.grids_tree.column("max_their", width=90)
        self.grids_tree.column("min_their", width=90)
        self.grids_tree.column("last", width=80)

        # Scrollbar for grids
        grid_scroll = ttk.Scrollbar(grid_tree_frame, orient=tk.VERTICAL, command=self.grids_tree.yview)
        self.grids_tree.configure(yscrollcommand=grid_scroll.set)

        self.grids_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        grid_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Button frame for grids tab
        grid_button_frame = ttk.Frame(grids_frame, padding="5")
        grid_button_frame.pack(fill=tk.X)
        ttk.Button(grid_button_frame, text="Delete Selected", command=self._delete_selected_grids).pack(side=tk.LEFT)

        # Context menu for grids
        self.grids_menu = tk.Menu(self.root, tearoff=0)
        self.grids_menu.add_command(label="Delete Selected", command=self._delete_selected_grids)

        # Bind events
        self.grids_tree.bind("<Double-1>", self._on_grids_tree_double_click)
        self.grids_tree.bind("<Button-3>", self._show_grids_menu)
        self.grids_tree.bind("<Delete>", lambda e: self._delete_selected_grids())

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

        # Map tab (only if tkintermapview is available)
        self.map_widget = None
        self.map_markers = []
        self.grid_polygons = []
        self.grid_labels = []
        self._grid_refresh_after_id = None
        self._grid_label_icon = None  # lazy: 1x1 transparent so markers render as text-only
        self.show_grids_var = tk.BooleanVar(value=True)
        self.grid_zoom_hint_var = tk.StringVar(value="")
        if HAS_MAP:
            map_frame = ttk.Frame(self.notebook)
            self.notebook.add(map_frame, text="Map")

            # Map controls
            map_controls = ttk.Frame(map_frame, padding="5")
            map_controls.pack(fill=tk.X)

            ttk.Button(map_controls, text="Refresh Map", command=self._refresh_map).pack(side=tk.LEFT)
            ttk.Checkbutton(
                map_controls,
                text="Show grid squares",
                variable=self.show_grids_var,
                command=self._refresh_grid_overlays,
            ).pack(side=tk.LEFT, padx=(10, 0))
            # Shown only when grids are enabled but suppressed by zoom level.
            ttk.Label(
                map_controls,
                textvariable=self.grid_zoom_hint_var,
                foreground="#888888",
            ).pack(side=tk.LEFT, padx=(4, 0))

            # Legend lives in its own container so _build_legend can
            # clear and rebuild it when thresholds are edited.
            self.legend_frame = ttk.Frame(map_controls)
            self.legend_frame.pack(side=tk.LEFT, padx=(15, 0))
            self._build_legend()

            # Map widget
            self.map_widget = tkintermapview.TkinterMapView(map_frame, corner_radius=0)
            self.map_widget.pack(fill=tk.BOTH, expand=True)
            self.map_widget.set_position(39.8283, -98.5795)  # Center of US
            self.map_widget.set_zoom(4)

            # Redraw grid overlays when the user pans, zooms, or the canvas
            # is resized. Debounced so a drag doesn't trigger one redraw per
            # pixel. <Configure> covers initial layout too.
            canvas = self.map_widget.canvas
            for seq in ("<ButtonRelease-1>", "<MouseWheel>", "<Button-4>",
                        "<Button-5>", "<Configure>"):
                canvas.bind(seq, self._schedule_grid_refresh, add="+")

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
            # Store the database ID as the item ID for deletion support
            self.messages_tree.insert("", 0, iid=str(msg["id"]), values=(
                msg["callsign"],
                "Link",
                msg["timestamp"],
                msg.get("band", ""),
                format_snr(msg["my_snr_of_them"]),
                format_snr(msg["their_snr_of_me"]),
                msg["message"]
            ))

        # Clear and reload grids
        for item in self.grids_tree.get_children():
            self.grids_tree.delete(item)

        for entry in self.db.get_grids_with_snr_stats():
            # Format bands - filter out empty strings and join
            bands = entry.get("bands", "") or ""
            bands_list = [b for b in bands.split(",") if b]
            bands_formatted = ", ".join(sorted(set(bands_list)))
            self.grids_tree.insert("", tk.END, values=(
                entry["callsign"],
                "Link",
                entry["grid"],
                bands_formatted,
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

        if not item:
            return

        values = self.grids_tree.item(item, "values")
        if not values:
            return

        if column == "#2":  # QRZ column
            if values[0]:
                self._open_qrz(values[0])
        elif column == "#3":  # Grid column
            callsign = values[0]
            current_grid = values[2] if values[2] else ""
            new_grid = self._edit_grid_dialog(callsign, current_grid)
            if new_grid is not None:  # None means cancelled, empty string means clear
                self.db.update_grid(callsign, new_grid)
                self._refresh_grids_table()
                self.status_var.set(f"Updated grid for {callsign}")

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
        self.status_var.set(f"Found {len(exact_results)} in {grid}* + {len(adjacent_results)} adjacent = {total} {'callsign' if total == 1 else 'callsigns'}")

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
                    # Add to database and get the new ID
                    msg_id = self.db.add_message(
                        data["callsign"],
                        data["timestamp"],
                        data["my_snr_of_them"],
                        data["their_snr_of_me"],
                        data["message"],
                        data.get("band", "")
                    )
                    # Ensure callsign is in grids table (updates grid if provided)
                    self.db.add_grid(data["callsign"], data.get("grid", ""))
                    # Add to treeview at top (highlighted as session message)
                    # Use the database ID as the item ID for deletion support
                    self.messages_tree.insert("", 0, iid=str(msg_id), values=(
                        data["callsign"],
                        "Link",
                        data["timestamp"],
                        data.get("band", ""),
                        format_snr(data["my_snr_of_them"]),
                        format_snr(data["their_snr_of_me"]),
                        data["message"]
                    ), tags=("session",))
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
            # Format bands - filter out empty strings and join
            bands = entry.get("bands", "") or ""
            bands_list = [b for b in bands.split(",") if b]
            bands_formatted = ", ".join(sorted(set(bands_list)))
            self.grids_tree.insert("", tk.END, values=(
                entry["callsign"],
                "Link",
                entry["grid"],
                bands_formatted,
                format_snr(entry["max_my_snr"]),
                format_snr(entry["min_my_snr"]),
                format_snr(entry["max_their_snr"]),
                format_snr(entry["min_their_snr"]),
                format_age(entry["last_contact"])
            ))
        self.grids_tree.update_idletasks()

    def _snr_to_color(self, snr) -> str:
        """Map an SNR value to a marker color using the current thresholds."""
        if snr is None:
            return NO_DATA_COLOR
        for tier in self.snr_thresholds:
            if snr >= tier["min_snr"]:
                return tier["color"]
        return self.snr_thresholds[-1]["color"]

    def _build_legend(self):
        """(Re)build the map color legend from self.snr_thresholds."""
        if not getattr(self, "legend_frame", None):
            return

        for child in self.legend_frame.winfo_children():
            child.destroy()

        ttk.Label(self.legend_frame, text="Legend:").pack(side=tk.LEFT, padx=(0, 6))

        entries = list(self.snr_thresholds) + [
            {"color": NO_DATA_COLOR, "label": NO_DATA_LABEL}
        ]
        for tier in entries:
            item = ttk.Frame(self.legend_frame)
            item.pack(side=tk.LEFT, padx=(0, 10))
            # tk.Label (not ttk) so we can set a background color swatch.
            tk.Label(
                item,
                background=tier["color"],
                width=2,
                relief="solid",
                borderwidth=1,
            ).pack(side=tk.LEFT, padx=(0, 4))
            ttk.Label(item, text=tier["label"]).pack(side=tk.LEFT)

    def _refresh_map(self):
        """Refresh the map with current contact locations."""
        if not self.map_widget:
            return

        # Clear existing markers
        for marker in self.map_markers:
            marker.delete()
        self.map_markers.clear()

        entries = self.db.get_grids_with_snr_stats()

        for entry in entries:
            grid = entry["grid"]
            if not grid:
                continue

            coords = grid_to_latlon(grid)
            if not coords:
                continue

            lat, lon = coords
            callsign = entry["callsign"]
            max_their_snr = entry["max_their_snr"]
            contact_count = entry["contact_count"] or 0

            # Determine marker color based on SNR (their reading of us)
            color = self._snr_to_color(max_their_snr)

            # Create marker
            marker = self.map_widget.set_marker(
                lat, lon,
                text=callsign,
                marker_color_circle=color,
                marker_color_outside=color
            )
            self.map_markers.append(marker)

        self._refresh_grid_overlays()

        self.status_var.set(f"Map updated with {len(self.map_markers)} locations")

    # Grid overlays are gated by on-screen size, not zoom — at the same
    # zoom a wide window shows much larger squares than a tall one. Below
    # this rendered width, labels overlap and outlines turn to noise.
    GRID_MIN_PIXELS_PER_SQUARE = 50
    GRID_NEUTRAL_COLOR = "#888888"
    GRID_MAX_DRAW = 1000  # hard safety cap

    def _schedule_grid_refresh(self, event=None):
        """Debounce grid-overlay redraws while the user is panning/zooming."""
        if self._grid_refresh_after_id is not None:
            try:
                self.root.after_cancel(self._grid_refresh_after_id)
            except Exception:
                pass
        self._grid_refresh_after_id = self.root.after(150, self._refresh_grid_overlays)

    def _refresh_grid_overlays(self):
        """Draw Maidenhead grid squares visible in the current viewport."""
        self._grid_refresh_after_id = None
        if not self.map_widget:
            return

        # Clear everything first so toggling off removes them and panning
        # doesn't leave stale shapes from the previous viewport.
        for poly in self.grid_polygons:
            poly.delete()
        self.grid_polygons.clear()
        for label in self.grid_labels:
            label.delete()
        self.grid_labels.clear()

        if not self.show_grids_var.get():
            self.grid_zoom_hint_var.set("")
            return

        canvas = self.map_widget.canvas
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 10 or h < 10:
            # Canvas not realized yet; <Configure> will retry.
            return

        try:
            nw_lat, nw_lon = self.map_widget.convert_canvas_coords_to_decimal_coords(0, 0)
            se_lat, se_lon = self.map_widget.convert_canvas_coords_to_decimal_coords(w, h)
        except Exception:
            return

        lat_min = min(nw_lat, se_lat)
        lat_max = max(nw_lat, se_lat)
        lon_min = min(nw_lon, se_lon)
        lon_max = max(nw_lon, se_lon)

        # A grid square is 2 deg wide. If its rendered width drops below
        # GRID_MIN_PIXELS_PER_SQUARE, suppress the overlay (but leave the
        # checkbox state alone so it comes back when the user zooms in).
        lon_span = lon_max - lon_min
        if lon_span <= 0:
            return
        pixels_per_square = (w / lon_span) * 2.0
        if pixels_per_square < self.GRID_MIN_PIXELS_PER_SQUARE:
            self.grid_zoom_hint_var.set("(zoom in to see)")
            return
        self.grid_zoom_hint_var.set("")

        # Best max_their_snr per grid that has callsigns — these get
        # SNR-colored outlines so they pop against the neutral graticule.
        best_snr_by_grid = {}
        for entry in self.db.get_grids_with_snr_stats():
            g = entry["grid"]
            if not g:
                continue
            snr = entry["max_their_snr"]
            if g not in best_snr_by_grid:
                best_snr_by_grid[g] = snr
            elif snr is not None and (best_snr_by_grid[g] is None or snr > best_snr_by_grid[g]):
                best_snr_by_grid[g] = snr

        if self._grid_label_icon is None:
            self._grid_label_icon = tk.PhotoImage(width=1, height=1)

        # Maidenhead 4-char: lon index 0..179 (each 2 deg from -180),
        # lat index 0..179 (each 1 deg from -90).
        lat_idx_start = max(0, int(lat_min + 90))
        lat_idx_end = min(179, int(lat_max + 90))
        lon_idx_start = max(0, int((lon_min + 180) / 2))
        lon_idx_end = min(179, int((lon_max + 180) / 2))

        drawn = 0
        for lat_idx in range(lat_idx_start, lat_idx_end + 1):
            if drawn >= self.GRID_MAX_DRAW:
                break
            for lon_idx in range(lon_idx_start, lon_idx_end + 1):
                if drawn >= self.GRID_MAX_DRAW:
                    break

                sw_lat = lat_idx - 90
                sw_lon = lon_idx * 2 - 180
                corners = [
                    (sw_lat, sw_lon),
                    (sw_lat, sw_lon + 2),
                    (sw_lat + 1, sw_lon + 2),
                    (sw_lat + 1, sw_lon),
                ]

                grid = (
                    f"{chr(ord('A') + lon_idx // 10)}"
                    f"{chr(ord('A') + lat_idx // 10)}"
                    f"{lon_idx % 10}{lat_idx % 10}"
                )

                if grid in best_snr_by_grid:
                    color = self._snr_to_color(best_snr_by_grid[grid])
                    border = 2
                else:
                    color = self.GRID_NEUTRAL_COLOR
                    border = 1

                poly = self.map_widget.set_polygon(
                    corners,
                    outline_color=color,
                    fill_color=None,
                    border_width=border,
                )
                self.grid_polygons.append(poly)

                label = self.map_widget.set_marker(
                    sw_lat + 0.5, sw_lon + 1,
                    text=grid,
                    icon=self._grid_label_icon,
                )
                self.grid_labels.append(label)

                drawn += 1

    def _show_messages_menu(self, event):
        """Show context menu for messages tree."""
        # Select item under cursor if not already selected
        item = self.messages_tree.identify_row(event.y)
        if item:
            if item not in self.messages_tree.selection():
                self.messages_tree.selection_set(item)
            self.messages_menu.tk_popup(event.x_root, event.y_root)

    def _show_grids_menu(self, event):
        """Show context menu for grids tree."""
        # Select item under cursor if not already selected
        item = self.grids_tree.identify_row(event.y)
        if item:
            if item not in self.grids_tree.selection():
                self.grids_tree.selection_set(item)
            self.grids_menu.tk_popup(event.x_root, event.y_root)

    def _delete_selected_messages(self):
        """Delete selected messages from the database."""
        selected = self.messages_tree.selection()
        if not selected:
            self.status_var.set("No messages selected")
            return

        count = len(selected)
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Delete {count} selected message{'s' if count != 1 else ''}?"
        ):
            return

        # Get IDs (item IDs are the database IDs as strings)
        message_ids = [int(item_id) for item_id in selected]

        # Delete from database
        self.db.delete_messages(message_ids)

        # Remove from treeview
        for item_id in selected:
            self.messages_tree.delete(item_id)

        # Update count and refresh grids (SNR stats may have changed)
        count = self.db.get_message_count()
        self.count_var.set(f"{count} message{'s' if count != 1 else ''}")
        self._refresh_grids_table()
        self.status_var.set(f"Deleted {len(message_ids)} {'message' if len(message_ids) == 1 else 'messages'}")

    def _delete_selected_grids(self):
        """Delete selected callsign grids from the database."""
        selected = self.grids_tree.selection()
        if not selected:
            self.status_var.set("No callsigns selected")
            return

        # Get callsigns from selected items
        callsigns = []
        for item_id in selected:
            values = self.grids_tree.item(item_id, "values")
            if values:
                callsigns.append(values[0])  # First column is callsign

        if not callsigns:
            return

        # Get total message count for these callsigns
        total_messages = sum(self.db.get_message_count_for_callsign(cs) for cs in callsigns)

        # Ask user what to delete
        if total_messages > 0:
            result = self._show_delete_dialog(len(callsigns), total_messages)

            if result == "cancel":
                return
            elif result == "all":
                for callsign in callsigns:
                    self.db.delete_callsign_with_messages(callsign)
                self.status_var.set(f"Deleted {len(callsigns)} {'callsign' if len(callsigns) == 1 else 'callsigns'} and {total_messages} {'message' if total_messages == 1 else 'messages'}")
            elif result == "grids_only":
                for callsign in callsigns:
                    self.db.delete_callsign_grid(callsign)
                self.status_var.set(f"Deleted {len(callsigns)} grid {'entry' if len(callsigns) == 1 else 'entries'} (messages kept)")
        else:
            # No messages, just confirm grid deletion
            if not messagebox.askyesno(
                "Confirm Delete",
                f"Delete {len(callsigns)} callsign grid {'entry' if len(callsigns) == 1 else 'entries'}?"
            ):
                return
            for callsign in callsigns:
                self.db.delete_callsign_grid(callsign)
            self.status_var.set(f"Deleted {len(callsigns)} grid {'entry' if len(callsigns) == 1 else 'entries'}")

        # Refresh both tables
        self._refresh_tables()

    def _edit_grid_dialog(self, callsign: str, current_grid: str) -> str:
        """Show dialog to edit grid for a callsign. Returns new grid value or None if cancelled."""
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Edit Grid for {callsign}")
        dialog.transient(self.root)

        # Set initial geometry before adding widgets (fixes Linux rendering)
        dialog.geometry("200x120")
        dialog.update()

        dialog.grab_set()

        result = {"value": None}

        # Main frame to hold all content
        main_frame = ttk.Frame(dialog, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Label
        ttk.Label(main_frame, text="Grid Square:").pack(anchor=tk.W)

        # Entry field
        grid_var = tk.StringVar(value=current_grid)
        entry = tk.Entry(main_frame, textvariable=grid_var, width=12)
        entry.pack(anchor=tk.W, pady=(5, 15))

        def save():
            new_grid = grid_var.get().strip().upper()
            # Optional validation: 4+ alphanumeric chars or empty
            if new_grid and (len(new_grid) < 4 or not new_grid[:4].isalnum()):
                messagebox.showwarning("Invalid Grid", "Grid must be at least 4 alphanumeric characters (e.g., EM48)")
                return
            result["value"] = new_grid
            dialog.destroy()

        def cancel():
            dialog.destroy()

        # Buttons frame
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="Save", command=save).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Cancel", command=cancel).pack(side=tk.LEFT)

        # Set minimum size and let it auto-size
        dialog.update_idletasks()
        dialog.minsize(dialog.winfo_width(), dialog.winfo_height())
        dialog.resizable(False, False)

        # Bind Enter key to save
        entry.bind("<Return>", lambda e: save())
        entry.select_range(0, tk.END)
        entry.focus_set()
        dialog.bind("<Escape>", lambda e: cancel())

        # Center dialog on parent
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        # Handle window close button
        dialog.protocol("WM_DELETE_WINDOW", cancel)

        self.root.wait_window(dialog)
        return result["value"]

    def _show_delete_dialog(self, num_callsigns: int, num_messages: int) -> str:
        """Show custom delete dialog with descriptive buttons. Returns 'all', 'grids_only', or 'cancel'."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Delete Options")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        result = {"value": "cancel"}

        # Message
        cs_word = "callsign" if num_callsigns == 1 else "callsigns"
        msg_word = "message" if num_messages == 1 else "messages"
        ttk.Label(
            dialog,
            text=f"Delete {num_callsigns} {cs_word} with {num_messages} associated {msg_word}.",
            padding="15 15 15 5"
        ).pack()

        # Buttons frame
        btn_frame = ttk.Frame(dialog, padding="10")
        btn_frame.pack(fill=tk.X)

        def set_result(val):
            result["value"] = val
            dialog.destroy()

        ttk.Button(
            btn_frame,
            text="Delete grids AND messages",
            command=lambda: set_result("all")
        ).pack(fill=tk.X, pady=2)

        ttk.Button(
            btn_frame,
            text="Delete grids only (keep messages)",
            command=lambda: set_result("grids_only")
        ).pack(fill=tk.X, pady=2)

        ttk.Button(
            btn_frame,
            text="Cancel",
            command=lambda: set_result("cancel")
        ).pack(fill=tk.X, pady=2)

        # Center dialog on parent
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        # Handle window close button
        dialog.protocol("WM_DELETE_WINDOW", lambda: set_result("cancel"))

        self.root.wait_window(dialog)
        return result["value"]

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
