# JS8 Recorder

A desktop application for recording and logging JS8Call RX.DIRECTED messages.

## Features

- Connects to JS8Call's TCP API to capture directed messages
- Filters messages to only those directed at your callsign
- Stores all data in a local SQLite database
- Displays messages and callsign-grid mappings in a tabbed interface
- Double-click to open QRZ.com page for any callsign
- Export to Excel (.xlsx) format
- Settings persist between sessions
- Interactive map showing contact locations (color-coded by SNR quality)
- Lookup contacts by grid square with adjacent grid support

## Requirements

- Python 3.7+
- JS8Call with TCP API enabled
- tkinter (included with Python on Windows, `sudo apt install python3-tk` on Linux)
- openpyxl (for Excel export)
- matplotlib (optional, for map display)
- cartopy (optional, for map backgrounds with coastlines/borders)

## Installation

```bash
# Required
sudo apt install python3-tk

# For Excel export
pip install openpyxl

# For map display (optional)
sudo apt install python3-matplotlib

# For map backgrounds (optional)
sudo apt install python3-cartopy
```

## Usage

```bash
python js8_recorder.py
```

1. Enter your callsign
2. Enter the JS8Call API host (default: 127.0.0.1) and port (default: 2442)
3. Click "Start Listening"
4. Messages directed at you will appear in the table
5. Click "Export to Excel" to save a spreadsheet

## JS8Call API Setup

In JS8Call:
1. Go to `File > Settings > Reporting > API`
2. Enable "TCP Server API"
3. Note the port (default 2442)
4. If connecting from another machine, ensure the API is bound to 0.0.0.0

## Files

- `js8_recorder.py` - Main GUI application
- `js8_client.py` - JS8Call TCP API client
- `database.py` - SQLite database layer
- `js8_log.db` - Database file (created on first run)

## License

MIT
