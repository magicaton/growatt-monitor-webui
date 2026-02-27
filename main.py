import os
import sys
import shutil
import asyncio
import logging
import atexit
import subprocess
import argparse
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

import serial

from nicegui import ui, app, run, Client
from fastapi import Request
from fastapi.responses import PlainTextResponse
from datetime import datetime

from core.log_service import configure_logging
from core.config import Config
from core.modbus_core import build_optimized_chunks, read_chunk_sync, auto_detect_com_port
from core.dashboard_config import load_dashboard_config, create_widgets_from_config

from ui import layout
from ui import layout_inspector
from ui import layout_logs


# --- GLOBALS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "growatt_monitor_webui.log")
CONFIG_FILE = os.path.join(BASE_DIR, "config.yaml")
DASHBOARD_FILE = os.path.join(BASE_DIR, "dashboard.yaml")

conf = Config()

running_as_task: bool = False


@dataclass
class AppState:
    dashboard_clients_count: int = 0
    inspector_clients_count: int = 0
    clients_count: int = 0
    latest_data: dict[int, int] = field(default_factory=dict)
    serial_obj: Any = None
    is_running: bool = True
    cleanup_done: bool = False
    chunks: list[tuple[int, int]] = field(default_factory=list)
    inspector_chunks: list[tuple[int, int]] = field(default_factory=list)
    dashboard_required_ids: set[int] = field(default_factory=set)
    active_connections: dict = field(default_factory=dict)
    log_handler: Any = None


state = AppState()

state_lock = Lock()


# --- CLI ARGUMENTS ---
def is_running_as_task() -> bool:
    parser = argparse.ArgumentParser(description="Growatt Monitor WebUI")
    parser.add_argument(
        "--scheduled-task",
        action="store_true",
        dest="scheduled_task",
        help="Indicates the program was launched from Windows Task Scheduler"
    )

    args = parser.parse_args()
    return args.scheduled_task

# --- CHUNKS ---
def initialize_chunks():
    config = load_dashboard_config(DASHBOARD_FILE)
    
    required_ids = config.required_ids
    state.dashboard_required_ids = required_ids
    logging.info("Dashboard: %d widgets, %d registers: %s",
             len(config.card_definitions), len(required_ids), sorted(required_ids))

    # Build optimized chunks from required registers
    state.chunks = build_optimized_chunks(
        required_ids,
        max_count=conf.max_chunk_size,
        max_gap=conf.opt_max_gap,
    )
    logging.info("Optimized into %d chunks: %s", len(state.chunks), state.chunks)

    # Validate inspector chunks against size limits
    validated_inspector_chunks = []
    for start, count in conf.inspector_chunks:
        if count <= 0:
            continue
        if count > conf.max_chunk_size:
            logging.warning(
                "Inspector chunk [%d..%d] exceeds max_chunk_size %d, truncating",
                start, start + count - 1,
                conf.max_chunk_size,
            )
            count = conf.max_chunk_size
        validated_inspector_chunks.append((start, count))
    state.inspector_chunks = validated_inspector_chunks
    logging.info("Inspector chunks: %s", state.inspector_chunks)


# --- CLIENT CONNECTIONS ---
def register_client_activity(client, page_name, request):
    with state_lock:
        if client.id not in state.active_connections:
            state.active_connections[client.id] = {
                "ip": request.client.host if request.client else "Unknown",
                "page": page_name,
                "connected_at": datetime.now(),
                "last_seen": datetime.now(),
            }

            if page_name == "Dashboard":
                state.dashboard_clients_count += 1
            elif page_name == "Inspector":
                state.inspector_clients_count += 1
            else:
                state.clients_count += 1

            logging.info(
                "Client %s connected (%s). Active: %d, Inspector: %d, Other: %d",
                client.id,
                page_name,
                state.dashboard_clients_count,
                state.inspector_clients_count,
                state.clients_count,
            )
        else:
            state.active_connections[client.id]["last_seen"] = datetime.now()

def unregister_client(client_id):
    with state_lock:
        if client_id in state.active_connections:
            conn = state.active_connections.pop(client_id)
            page = conn.get("page", "Unknown")

            if page == "Dashboard":
                state.dashboard_clients_count = max(0, state.dashboard_clients_count - 1)
            elif page == "Inspector":
                state.inspector_clients_count = max(0, state.inspector_clients_count - 1)
            else:
                state.clients_count = max(0, state.clients_count - 1)

            logging.info(
                "Client %s disconnected (%s). Active: %d, Inspector: %d, Other: %d",
                client_id,
                page,
                state.dashboard_clients_count,
                state.inspector_clients_count,
                state.clients_count,
            )

def setup_heartbeat(client, page_name, request):
    timer_active = {"value": True}  # Mutable dict — closures can't rebind outer locals

    async def heartbeat():
        if not timer_active["value"] or not client.has_socket_connection:
            return
        try:
            await client.run_javascript("true", timeout=1.5)
            register_client_activity(client, page_name, request)
        except Exception:
            pass

    heartbeat_timer = ui.timer(2.0, heartbeat)

    def on_disconnect():
        timer_active["value"] = False
        heartbeat_timer.deactivate()

        # Delayed removal: if this is a Wi-Fi change, JS will reload the page
        # and create a new client before this old one is removed.
        async def delayed_unregister():
            await asyncio.sleep(15.0)
            unregister_client(client.id)

        asyncio.create_task(delayed_unregister())

    client.on_disconnect(on_disconnect)
    return heartbeat_timer


# --- CLEANUP ---
def cleanup_resources():
    with state_lock:
        if state.cleanup_done:
            return
        state.cleanup_done = True
        state.is_running = False
        if state.serial_obj and state.serial_obj.is_open:
            try:
                state.serial_obj.close()
                state.serial_obj = None
                logging.info("Serial port closed")
            except Exception as e:
                logging.error("Error closing serial port: %s", e)

    logging.info("Cleanup complete")


# --- BACKGROUND WORKER ---
async def background_worker():
    logging.info("Background worker started")
    while state.is_running:
        # No clients -> close port and sleep
        total_clients = state.dashboard_clients_count + state.inspector_clients_count
        if total_clients == 0:
            if state.serial_obj and state.serial_obj.is_open:
                logging.info("No active clients, closing serial port")
                state.serial_obj.close()
                state.serial_obj = None
            await asyncio.sleep(1)
            continue

        # Open port if needed
        if state.serial_obj is None:
            try:
                target_port = conf.com_port
                if target_port.lower() == "auto":
                    detected = await run.io_bound(auto_detect_com_port, conf.baudrate, conf.slave_id)
                    if detected:
                        target_port = detected
                        conf.com_port = detected  # Save to avoid re-scanning
                    else:
                        logging.warning("Auto COM port detection failed, retrying in 5s")
                        await asyncio.sleep(5)
                        continue

                logging.debug("Opening port %s", target_port)
                ser = serial.Serial(target_port, conf.baudrate, timeout=1.0)
                ser.reset_input_buffer()

                if state.is_running:
                    state.serial_obj = ser
                    logging.info("Port %s opened (baud=%d)", target_port, conf.baudrate)
                else:
                    ser.close()
            except Exception:
                logging.exception("Failed to open port %s", conf.com_port)
                await asyncio.sleep(5)
                continue

        # Read registers using appropriate chunks for current client mix
        has_dash = state.dashboard_clients_count > 0
        has_insp = state.inspector_clients_count > 0

        if has_insp and has_dash:
            insp_ids = set()
            for s, c in state.inspector_chunks:
                insp_ids.update(range(s, s + c))
            missing = state.dashboard_required_ids - insp_ids
            if missing:
                extra = build_optimized_chunks(missing, conf.max_chunk_size, conf.opt_max_gap)
                chunks_to_read = state.inspector_chunks + extra
            else:
                chunks_to_read = state.inspector_chunks
            mode_label = "MERGED"
        elif has_insp:
            chunks_to_read = state.inspector_chunks
            mode_label = "INSPECTOR"
        else:
            chunks_to_read = state.chunks
            mode_label = "DASHBOARD"
        logging.debug("Reading cycle: Mode=%s, Chunks=%d", mode_label, len(chunks_to_read))

        for start, count in chunks_to_read:
            vals = await run.io_bound(
                read_chunk_sync, state.serial_obj, conf.slave_id, start, count
            )
            if vals is None:
                logging.debug("No data for chunk start_addr=%s count=%s", start, count)
            else:
                with state_lock:
                    for i, val in enumerate(vals):
                        state.latest_data[start + i] = val

        await asyncio.sleep(conf.update_interval)

async def zombie_cleanup_task():
    logging.info("Zombie cleanup task started")
    # Must be larger than heartbeat interval (2s) + disconnect grace period (15s)
    max_idle_seconds = 60

    while state.is_running:
        await asyncio.sleep(10)  # Check every 10 seconds
        
        zombies = []
        now = datetime.now()

        # Find stale clients
        with state_lock:
            for client_id, data in state.active_connections.items():
                last_seen = data.get("last_seen")
                if last_seen:
                    delta = (now - last_seen).total_seconds()
                    if delta > max_idle_seconds:
                        zombies.append(client_id)
        
        # Remove stale clients (outside lock to avoid re-entrancy)
        for z_id in zombies:
            logging.warning("Removing zombie client %s (idle >%ds)", z_id, max_idle_seconds)
            unregister_client(z_id)


# --- PAGES ---
@ui.page("/")
async def main_page(client: Client, request: Request):
    # Auto dark mode
    ui.add_css(':root { color-scheme: light dark; }')

    ui.colors(primary="#5898d4")

    ui.add_head_html(layout.get_fill_css())

    # Auto-reload page on WebSocket disconnect (handles Wi-Fi changes)
    ui.add_head_html('''
        <script>
            window.addEventListener('load', () => {
                setInterval(() => {
                    if (window.socket && window.socket.connected === false) {
                        console.log("Connection lost. Reloading to restore session...");
                        window.location.reload();
                    }
                }, 2000);
            });
        </script>
    ''')

    # Persist query params to cookie-based storage
    for key in ("dev", "fs"):
        if (val := request.query_params.get(key)) is not None:
            app.storage.browser[key] = val == "1"

    dev = app.storage.browser.get("dev", conf.show_dev_btns)
    fullscreen = app.storage.browser.get("fs", conf.show_fs_btn)

    show_all = {"value": request.cookies.get("gw_show_all") == "1"}  # Cookie toggle for starred-only filter
    active_widgets: list[layout.BaseWidget] = []

    heartbeat_ref: dict = {"timer": None}

    # --- HEADER ---
    with ui.header().classes("bg-blue-900 items-center shadow-lg"):
        ui.icon("bolt", size="md", color="yellow-400")

        ui.label("Growatt Monitor").classes("text-xl font-bold text-white")

        ui.element("div").classes("flex-grow")

        toggle_tooltip = None

        def update_toggle_btn():
            icon = "star_border" if show_all["value"] else "star"
            toggle_btn.props(f"icon={icon}")
            if toggle_tooltip:
                toggle_tooltip.text = "Show starred only" if show_all["value"] else "Show all"
            toggle_btn.update()

        def toggle_view():
            show_all["value"] = not show_all["value"]

            val = "1" if show_all["value"] else "0"
            ui.run_javascript(f'document.cookie="gw_show_all={val};path=/;max-age=315360000;SameSite=Lax"')
            update_toggle_btn()
            rebuild_grid()


        with ui.row().classes("gap-0 sm:gap-2 flex-nowrap items-center"):
            toggle_btn = ui.button(icon="star", on_click=toggle_view).props("flat color=white dense round")
            with toggle_btn:
                toggle_tooltip = ui.tooltip("Show all").classes('text-center')

            update_toggle_btn()


            if fullscreen is True:
                ui.button(
                    icon="fullscreen",
                    on_click=lambda: ui.run_javascript("""
                    if (!document.fullscreenElement) {
                        document.documentElement.requestFullscreen();
                    } else {
                        document.exitFullscreen();
                    }
                """),
                ).props("flat color=white dense round").tooltip("Toggle fullscreen")


            if dev is True:
                ui.button(
                    icon="manage_search",
                    on_click=lambda: ui.navigate.to("/inspector"),
                ).props("flat color=white dense round").tooltip("Inspector")
                ui.button(
                    icon="article",
                    on_click=lambda: ui.navigate.to("/logs"),
                ).props("flat color=white dense round").tooltip("Logs")


            # --- PAUSE BUTTON ---
            is_paused = {"value": False}
            pause_tooltip = None

            def toggle_pause():
                if is_paused["value"]:
                    is_paused["value"] = False
                    register_client_activity(client, "Dashboard", request)

                    if heartbeat_ref["timer"]:
                        heartbeat_ref["timer"].activate()

                    dashboard_timer.activate()

                    pause_btn.props("icon=pause color=white")
                    if pause_tooltip:
                        pause_tooltip.text = "Pause"
                    logging.debug("Client %s resumed updates", client.id)
                    ui.notify("Updates resumed", type="positive", position="top")
                else:
                    is_paused["value"] = True
                    dashboard_timer.deactivate()

                    if heartbeat_ref["timer"]:
                        heartbeat_ref["timer"].deactivate()

                    unregister_client(client.id)

                    pause_btn.props("icon=play_arrow color=green")
                    if pause_tooltip:
                        pause_tooltip.text = "Resume"
                    logging.debug("Client %s paused updates", client.id)
                    ui.notify("Updates paused (Client Inactive)", type="info", position="top")

            pause_btn = ui.button(icon="pause", on_click=toggle_pause).props(
                "flat color=white dense round"
            )
            with pause_btn:
                pause_tooltip = ui.tooltip("Pause").classes('text-center')


    # --- WIDGET GRID ---
    grid_container = ui.element("div").classes("w-full p-4")

    def rebuild_grid():
        nonlocal active_widgets
        grid_container.clear()
        active_widgets.clear()
        with grid_container:
            with ui.grid().classes(
                "w-full grid-cols-2 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4"
            ):
                page_widgets = create_widgets_from_config()
                grid_widgets = layout.build_interface(
                    page_widgets, show_all=show_all["value"]
                )
                active_widgets.extend(grid_widgets)

    rebuild_grid()

    def update_view():
        if not client.has_socket_connection:
            return
        try:
            with state_lock:
                snapshot = dict(state.latest_data)
            for widget in active_widgets:
                widget.update(snapshot)
        except Exception:
            logging.debug("Error updating dashboard widgets", exc_info=True)

    dashboard_timer = ui.timer(1.0, update_view)
    client.on_disconnect(lambda: dashboard_timer.deactivate())

    # Wait for WebSocket and register client
    await client.connected()

    with state_lock:
        state.dashboard_clients_count += 1
        state.active_connections[client.id] = {
            "ip": request.client.host if request.client else "Unknown",
            "page": "Dashboard",
            "connected_at": datetime.now(),
            "last_seen": datetime.now(),
        }
        logging.info(
            "Client %s connected (Dashboard) from %s. Active: %d, Inspector: %d",
            client.id,
            state.active_connections[client.id]["ip"],
            state.dashboard_clients_count,
            state.inspector_clients_count,
        )

    heartbeat_ref["timer"] = setup_heartbeat(client, "Dashboard", request)


# --- INSPECTOR PAGE ---
@ui.page("/inspector")
async def inspector_page(client: Client, request: Request):
    layout_inspector.create_inspector_page(client, state)

    setup_heartbeat(client, "Inspector", request)


# --- LOGS PAGE ---
@ui.page("/logs")
async def logs_page(client: Client, request: Request):
    layout_logs.create_logs_page(client, state)

    setup_heartbeat(client, "Logs", request)


# --- ENDPOINTS ---
@app.get("/shutdown")
async def shutdown_endpoint():
    logging.info("Shutdown requested via HTTP endpoint")

    async def delayed_shutdown():
        await asyncio.sleep(0.5)
        app.shutdown()

    asyncio.create_task(delayed_shutdown())
    return PlainTextResponse("Shutting down...")


@app.get("/restart")
async def restart_endpoint():
    if os.name != 'nt':
        return PlainTextResponse("Restart endpoint is only available on Windows", status_code=501)

    logging.info("Restart requested via HTTP endpoint (running_as_task=%s)", running_as_task)

    script_path = os.path.join(BASE_DIR, "TaskManager.ps1")
    
    ps_executable = "pwsh.exe" if shutil.which("pwsh.exe") else "powershell.exe"

    if running_as_task:
        cmd = [ps_executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path, "-RestartTask"]
    else:
        python_exe = sys.executable
        if not os.path.isfile(python_exe):
            logging.error("Python executable not found: %s", python_exe)
            return PlainTextResponse(
                f"Python executable not found: {python_exe}", status_code=500
            )
        cmd = [ps_executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path, "-RestartDirect", "-PythonExe", python_exe]
    try:
        subprocess.Popen(
            cmd,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logging.info("Restart script launched: %s", cmd)
    except Exception as e:
        logging.error("Failed to launch restart script: %s", e)
        return PlainTextResponse(
            f"Failed to launch restarter: {str(e)}", status_code=500
        )


    async def delayed_shutdown():
        await asyncio.sleep(0.5)
        app.shutdown()

    asyncio.create_task(delayed_shutdown())
    return PlainTextResponse("Restarting...")

# --- STARTUP ---
_initialized = False

def _initialize():
    global _initialized
    if _initialized:
        return
    _initialized = True

    conf.load_from_file(CONFIG_FILE)

    state.log_handler = configure_logging(
        conf.console_log_level, conf.file_log_level, LOG_FILE
    )

    logging.info("Config loaded: %s", CONFIG_FILE)

    app.on_startup(initialize_chunks)
    app.on_startup(background_worker)
    app.on_startup(zombie_cleanup_task)

    atexit.register(cleanup_resources)

    @app.on_shutdown
    async def shutdown():
        logging.info("Shutdown triggered")
        cleanup_resources()


# __mp_main__: NiceGUI re-imports this module in a subprocess for hot-reload
if __name__ in {"__main__", "__mp_main__"}:
    _initialize()

    logging.info("Application starting")

    running_as_task = is_running_as_task()

    ui.run(
        title="Growatt Monitor",
        favicon="⚡",
        dark=None,  # auto
        host=conf.server_host,
        port=conf.server_port,
        storage_secret=conf.storage_secret,
        reload=False,
        show=False,
        show_welcome_message=False,
    )
