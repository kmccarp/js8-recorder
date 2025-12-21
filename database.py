"""
SQLite database layer for JS8 Recorder.
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

try:
    from openpyxl import Workbook
except ImportError:
    Workbook = None


class Database:
    def __init__(self, db_path: str = "js8_log.db"):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        """Create database tables if they don't exist."""
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS directed_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                callsign TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                my_snr_of_them TEXT,
                their_snr_of_me TEXT,
                message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS callsign_grids (
                callsign TEXT PRIMARY KEY,
                grid TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        self.conn.commit()

    def add_message(self, callsign: str, timestamp: str, my_snr_of_them: str,
                    their_snr_of_me: str, message: str) -> int:
        """Add a directed message to the database. Returns the row ID."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO directed_messages (callsign, timestamp, my_snr_of_them, their_snr_of_me, message)
            VALUES (?, ?, ?, ?, ?)
        """, (callsign, timestamp, my_snr_of_them, their_snr_of_me, message))
        self.conn.commit()
        return cursor.lastrowid

    def add_grid(self, callsign: str, grid: str):
        """Add or update a callsign-grid mapping."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO callsign_grids (callsign, grid, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(callsign) DO UPDATE SET grid = ?, updated_at = ?
        """, (callsign, grid, datetime.utcnow().isoformat(),
              grid, datetime.utcnow().isoformat()))
        self.conn.commit()

    def get_all_messages(self) -> list:
        """Get all directed messages."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT id, callsign, timestamp, my_snr_of_them, their_snr_of_me, message
            FROM directed_messages
            ORDER BY timestamp DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

    def get_all_grids(self) -> list:
        """Get all callsign-grid mappings."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT callsign, grid
            FROM callsign_grids
            ORDER BY callsign
        """)
        return [dict(row) for row in cursor.fetchall()]

    def get_message_count(self) -> int:
        """Get total message count."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM directed_messages")
        return cursor.fetchone()[0]

    def get_setting(self, key: str, default: str = "") -> str:
        """Get a setting value."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        """Set a setting value."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = ?
        """, (key, value, value))
        self.conn.commit()

    def export_to_excel(self, output_path: str) -> bool:
        """Export database to Excel file. Returns True on success."""
        if Workbook is None:
            raise ImportError("openpyxl is required for Excel export. Install with: pip install openpyxl")

        wb = Workbook()

        # Sheet 1: Directed Messages
        ws1 = wb.active
        ws1.title = "Directed Messages"
        ws1.append(["Callsign", "QRZ", "Timestamp (UTC)", "My SNR of Them", "Their SNR of Me", "Message"])

        for msg in self.get_all_messages():
            callsign = msg["callsign"]
            qrz_url = f"https://www.qrz.com/db/{callsign}" if callsign else ""
            ws1.append([
                callsign,
                qrz_url,
                msg["timestamp"],
                msg["my_snr_of_them"],
                msg["their_snr_of_me"],
                msg["message"]
            ])

        ws1.column_dimensions["A"].width = 12
        ws1.column_dimensions["B"].width = 35
        ws1.column_dimensions["C"].width = 20
        ws1.column_dimensions["D"].width = 15
        ws1.column_dimensions["E"].width = 15
        ws1.column_dimensions["F"].width = 50

        # Sheet 2: Callsign Grids
        ws2 = wb.create_sheet("Callsign Grids")
        ws2.append(["Callsign", "QRZ", "Grid Square"])

        for grid_entry in self.get_all_grids():
            callsign = grid_entry["callsign"]
            qrz_url = f"https://www.qrz.com/db/{callsign}" if callsign else ""
            ws2.append([callsign, qrz_url, grid_entry["grid"]])

        ws2.column_dimensions["A"].width = 12
        ws2.column_dimensions["B"].width = 35
        ws2.column_dimensions["C"].width = 12

        wb.save(output_path)
        return True

    def close(self):
        """Close the database connection."""
        self.conn.close()
