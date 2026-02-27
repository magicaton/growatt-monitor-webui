# Growatt Monitor

Real-time web dashboard for Growatt inverters via Modbus RTU (RS-232/USB).  
Built with [NiceGUI](https://nicegui.io/) + pySerial.

> **Note:** This project is almost entirely vibe-coded with AI.

> **Note:** Data is only polled from the inverter while at least one browser tab has the dashboard open.

> **Warning:** This project is designed for use on a trusted local network (LAN). There is no authentication or access control — do not expose it to the internet.

## Requirements

- Python 3.11+
- Growatt inverter connected via USB-to-RS232 adapter
- Tested on Windows only; should work on any OS where Python and pySerial/nicegui are available.

## Installation

```powershell
git clone https://github.com/magicaton/growatt-monitor-webui.git
cd growatt-monitor-webui

python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt
```

## Configuration

Copy the template and edit it:

```powershell
cp config_templates\config.yaml config.yaml
cp config_templates\dashboard.yaml dashboard.yaml
```

### `config.yaml`

| Section      | Key                | Default        | Description                                |
|--------------|--------------------|--------------  |--------------------------------------------|
| `server`     | `host`             | `"0.0.0.0"`    | Bind address                               |
| `server`     | `port`             | `8080`         | Web UI port                                |
| `server`     | `storage_secret`   | `"secret.."`   | Secret key for NiceGUI session storage     |
| `modbus`     | `com_port`         | `"auto"`       | COM port or `"auto"` for auto-detection    |
| `modbus`     | `baudrate`         | `9600`         | Serial baud rate                           |
| `modbus`     | `slave_id`         | `1`            | Modbus slave ID                            |
| `modbus`     | `update_interval`  | `3.0`          | Seconds between register reads             |
| `modbus`     | `max_chunk_size`   | `40`           | Max registers per single Modbus read       |
| `modbus`     | `opt_max_gap`      | `20`           | Max address gap when merging read chunks   |
| `logging`    | `console_level`    | `"INFO"`       | Console log level                          |
| `logging`    | `file_level`       | `"WARNING"`    | File log level                             |
| `ui`         | `show_fs_btn`      | `false`        | Show Fullscreen button in header           |
| `ui`         | `show_dev_btns`    | `true`         | Show Inspector/Logs buttons in header      |
| `inspector`  | `inspector_chunks` | `[[0,40],..]`  | Address ranges for the Inspector page      |

### `dashboard.yaml`

Defines which registers to display and how. Widget types: `RegisterCard`, `StripCard`, `MathCard`, `EnergyStackWidget`.

The default template includes register mappings for the **Growatt SPF 6000 ES Plus**. For other inverter models, you can use the `/inspector` page to explore raw register values — set the desired address ranges in `inspector_chunks` and determine the correct mapping experimentally, or look up the Modbus register map for your model online.

## Usage

```powershell
python main.py
```

Open `http://localhost:8080` (or the configured host/port).

### Pages

| Page          | URL           | Description                         |
|---------------|---------------|-------------------------------------|
| Dashboard     | `/`           | Main widget grid with live data     |
| Inspector     | `/inspector`  | Raw register viewer for debugging   |
| Logs          | `/logs`       | Live application log viewer         |

Inspector and Logs are visible when `show_dev_btns: true` or by adding `?dev=1` to the URL.

### API Endpoints

- `GET /shutdown` — gracefully stop the server
- `GET /restart` — restart the process (Windows only)

### Running as a Scheduled Task (Windows)

Create a scheduled task automatically by running PowerShell **as Administrator**:

```powershell
.\TaskManager.ps1 -Create -PythonExe "venv\Scripts\python.exe"
```

Then start the task via Task Scheduler UI, or from PowerShell:

```powershell
Start-ScheduledTask growatt_monitor_webui
```

When running as a task, the `--scheduled-task` flag is passed automatically, enabling task-aware restart logic.

> **Note:** To stop the background process, use the `/shutdown` endpoint or the Shutdown button in the UI, rather than forcefully stopping the task in Windows.

To remove the task:

```powershell
.\TaskManager.ps1 -Delete
```

## License

Zero-Clause BSD
