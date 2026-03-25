import re
import asyncio
import logging
from collections import deque
from datetime import datetime
import html as html_module
from dataclasses import dataclass, field
from typing import Any

from nicegui import ui

# Highlight rules: applied top-down, already-matched text is frozen.
LOG_HIGHLIGHT_RULES = [
    # IP addresses
    (r'\b(?:\d{1,3}\.){3}\d{1,3}\b', "text-cyan-400 font-bold"),

    # UUIDs (dimmed to reduce noise)
    (r'\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b', "text-gray-500 text-xs opacity-70"),

    # COM ports
    (r'\bCOM\d+\b', "text-amber-400 font-bold"),

    # Brackets
    (r'[\[\]\(\)]', "text-gray-600 dark:text-gray-500"),

    # Positive keywords
    (r'\b(connected|opened|Opening|starting|started|detected|loaded|launched|resumed)\b', "text-green-400"),

    # Negative keywords
    (r'\b(disconnected|failed|error|closed|closing|Shutdown|paused|zombie|Removing|retrying|truncating|idle)\b', "text-rose-400 font-bold"),

    # Label prefixes
    (r'\b(Active|Dashboard)\:', "text-blue-400 font-bold"),
    (r'\b(Inspector|Other)\:', "text-orange-400 font-bold"),

    # Numbers
    (r'\b\d+\b', "text-purple-400"),
]


LOG_LEVEL_BADGE_STYLES = {
    "CRITICAL": "bg-red-600 text-white",
    "ERROR": "bg-red-500/20 text-red-600 dark:bg-red-500/30 dark:text-red-400",
    "WARNING": "bg-orange-500/20 text-orange-600 dark:bg-orange-500/30 dark:text-orange-400",
    "INFO": "bg-cyan-500/20 text-cyan-600 dark:bg-cyan-500/30 dark:text-cyan-400",
    "DEBUG": "bg-gray-500/20 text-gray-600 dark:bg-gray-500/30 dark:text-gray-400",
}

SHORT_LEVELS = {
    "CRITICAL": "CRIT",
    "WARNING": "WARN",
}

LEVEL_PRIORITY = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}

# DOM limits and scroll thresholds
MAX_VISIBLE_LOGS = 500
LOAD_BATCH_SIZE = 200
SCROLL_LOAD_THRESHOLD = 500
SCROLL_BOTTOM_THRESHOLD = 30


@dataclass
class _ViewState:
    start_raw_index: int = 0
    end_raw_index: int = 0
    elements: deque[tuple[int, Any]] = field(default_factory=deque)
    total_in_buffer: int = 0
    listener_active: bool = True
    loading_older: bool = False
    scroll_id: str | None = None
    auto_scroll: bool = True
    loaded_at_top: bool = False
    loaded_at_bottom: bool = False
    initial_scroll_done: bool = False


def format_timestamp(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(dt.microsecond / 1000):03d}"

def render_log_entry(entry: dict) -> str:
    # Segments model: (text, frozen). Frozen segments already contain HTML
    # spans and are skipped by subsequent rules, preventing double-styling.
    timestamp = format_timestamp(entry["ts"])
    level = entry["level"]

    raw_message = html_module.escape(entry["msg"])

    segments = [(raw_message, False)]

    for pattern_str, css_class in LOG_HIGHLIGHT_RULES:
        new_segments = []
        pattern = re.compile(pattern_str, re.IGNORECASE)

        for text_part, is_styled in segments:
            if is_styled:
                new_segments.append((text_part, True))
                continue

            matches = list(pattern.finditer(text_part))

            if not matches:
                new_segments.append((text_part, False))
                continue

            last_idx = 0
            for match in matches:
                if match.start() > last_idx:
                    new_segments.append((text_part[last_idx:match.start()], False))

                styled_html = f'<span class="{css_class}">{match.group(0)}</span>'
                new_segments.append((styled_html, True))

                last_idx = match.end()

            if last_idx < len(text_part):
                new_segments.append((text_part[last_idx:], False))

        segments = new_segments

    # Reassemble
    final_message = "".join(text for text, _ in segments)

    display_level = SHORT_LEVELS.get(level, level)
    badge_style = LOG_LEVEL_BADGE_STYLES.get(level, "bg-gray-500/20 text-gray-500")

    return f'''<div class="log-entry flex flex-nowrap items-start gap-2 py-0.5 px-1 hover:bg-gray-100 dark:hover:bg-gray-800/50 rounded text-sm font-mono leading-relaxed whitespace-nowrap min-w-max">
        <span class="log-timestamp text-gray-400 dark:text-gray-500 whitespace-nowrap flex-shrink-0">{timestamp}</span>
        <span class="log-level px-1.5 py-0 rounded text-xs font-semibold whitespace-nowrap flex-shrink-0 w-12 text-center {badge_style}">{display_level}</span>
        <span class="log-message text-gray-200 dark:text-gray-300 whitespace-nowrap">{final_message}</span>
    </div>'''



def create_logs_page(client, state):
    # Auto dark mode
    ui.add_css(':root { color-scheme: light dark; }')

    # --- HEADER ---
    with ui.header().classes("bg-blue-900 items-center shadow-lg"):
        ui.icon("article", size="md", color="yellow-400")
        ui.label("Logs & Connections").classes("text-xl font-bold text-white")

        ui.element("div").classes("flex-grow")

        # Restart / Shutdown
        async def restart_service():
            with ui.dialog() as dialog, ui.card():
                ui.label("Are you sure you want to RESTART the service?")
                with ui.row().classes('w-full justify-end'):
                    ui.button("Cancel", on_click=dialog.close).props("flat")
                    async def do_restart():
                        dialog.close()
                        ui.notify("Restarting service...", type="warning", position="top")
                        await asyncio.sleep(0.5)
                        ui.run_javascript("fetch('/restart')")
                    ui.button("Restart", on_click=do_restart).props("color=orange")
            dialog.open()

        async def shutdown_service():
            with ui.dialog() as dialog, ui.card():
                ui.label("Are you sure you want to SHUTDOWN the server?")
                with ui.row().classes('w-full justify-end'):
                    ui.button("Cancel", on_click=dialog.close).props("flat")
                    async def do_shutdown():
                        dialog.close()
                        ui.notify("Shutting down...", type="negative", position="top")
                        await asyncio.sleep(0.5)
                        ui.run_javascript("fetch('/shutdown')")
                    ui.button("Shutdown", on_click=do_shutdown).props("color=red")
            dialog.open()

        with ui.row().classes("gap-0 sm:gap-2 flex-nowrap items-center"):
            ui.button(icon="restart_alt", on_click=restart_service).props(
                "flat color=orange dense round"
            ).tooltip("Restart")

            ui.button(icon="power_settings_new", on_click=shutdown_service).props(
                "flat color=red dense round"
            ).tooltip("Shutdown")

            # Back button (rightmost)
            target_url = "/"
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to(target_url)).props(
                "flat color=white dense round"
            )

    ui.add_css('body { margin: 0; padding: 0; overflow: hidden; }')
    ui.add_css('.nicegui-content { padding: 0 !important; }')

    # Header ~52px, leave small margin
    with ui.card().classes(
        "w-full max-w-6xl mx-auto my-1 p-2 sm:p-4 flex flex-col"
    ).style("height: calc(100vh - 60px); height: calc(100dvh - 60px)"):
        with ui.row().classes("w-full items-center mb-2"):
            with ui.tabs().classes("text-lg font-bold").props("dense no-caps") as tabs:
                logs_tab = ui.tab("logs", label="System Logs", icon="article")
                conn_tab = ui.tab("connections", label="Active Connections", icon="people")
        
        scroll_bottom_btn = None

        with ui.tab_panels(tabs, value=logs_tab).classes("flex-1 overflow-hidden w-full"):
            # --- LOGS TAB PANEL ---
            with ui.tab_panel(logs_tab).classes("h-full flex flex-col p-0"):
                # Level filter buttons
                filter_min_level = "INFO"
                filter_buttons = {}

                # Colors matching LOG_LEVEL_BADGE_STYLES (dark mode palette)
                filter_btn_styles = {
                    "ALL":     ("rgba(107,114,128,{a})", "#9ca3af", "#4b5563"),
                    "INFO":    ("rgba(6,182,212,{a})",   "#22d3ee", "#155e75"),
                    "WARNING": ("rgba(249,115,22,{a})",  "#fb923c", "#7c2d12"),
                    "ERROR":   ("rgba(239,68,68,{a})",   "#f87171", "#7f1d1d"),
                }

                filter_levels = [
                    ("ALL", "All"),
                    ("INFO", "Info+"),
                    ("WARNING", "Warn+"),
                    ("ERROR", "Error+"),
                ]

                with ui.row().classes("gap-1.5 px-1 py-1 flex-shrink-0 items-center"):
                    ui.icon("filter_list", size="xs").classes("text-gray-400")
                    for level_key, label in filter_levels:
                        def make_handler(lk=level_key):
                            return lambda: apply_filter(lk)
                        btn = ui.button(label, on_click=make_handler()).props(
                            "dense unelevated no-caps"
                        ).classes("px-3 py-0.5 min-h-0 h-7 text-sm font-semibold rounded")
                        filter_buttons[level_key] = btn

                def update_filter_buttons():
                    active = filter_min_level
                    for level_key, btn in filter_buttons.items():
                        bg_tpl, color_on, color_off = filter_btn_styles[level_key]
                        if level_key == active:
                            btn.style(f"background: {bg_tpl.format(a='0.3')}; color: {color_on}")
                        else:
                            btn.style(f"background: {bg_tpl.format(a='0.08')}; color: {color_off}")

                update_filter_buttons()

                # Log container (flex-1 instead of fixed height)
                log_container_wrapper = (
                    ui.element("div")
                    .classes("w-full flex-1 rounded border border-gray-300 dark:border-gray-700 overflow-hidden flex flex-col relative")
                    .style("background-color: #1a1a2e")
                )
        
                with log_container_wrapper:
                    # Scrollable log area
                    log_scroll = (
                        ui.scroll_area()
                        .classes("flex-1 w-full")
                        .style("background-color: #1a1a2e")
                    )
                    
                    with log_scroll:
                        log_column = ui.column().classes("w-full p-2 gap-0 overflow-x-auto")
                    
                    # Scroll-to-bottom button
                    scroll_bottom_btn = (
                        ui.button(icon="arrow_downward", on_click=lambda: None)
                        .props("fab-mini color=primary")
                        .classes("absolute bottom-3 right-3 z-10 opacity-80 hover:opacity-100")
                    )
                    scroll_bottom_btn.set_visibility(False)

            # --- CONNECTIONS TAB PANEL ---
            with ui.tab_panel(conn_tab).classes("p-0"):
                conn_container = ui.column().classes("w-full")

        log_handler = state.log_handler
        # Sliding window into log_handler.log_buffer:
        # [start_index .. start_index+len(elements)] = visible DOM range.
        # loading_older: concurrency lock for both load_older/load_newer.
        # loaded_at_top/bottom: one-shot flags to avoid repeated loads at edges.
        # initial_scroll_done: suppresses scroll-to-bottom button flash on page load.
        view_state = _ViewState()
        pending_logs = deque()

        def passes_filter(entry: dict) -> bool:
            min_lvl = filter_min_level
            if min_lvl == "ALL":
                return True
            return LEVEL_PRIORITY.get(entry.get("level", "INFO"), 1) >= LEVEL_PRIORITY.get(min_lvl, 1)

        def apply_filter(level_key: str):
            nonlocal filter_min_level
            filter_min_level = level_key
            update_filter_buttons()
            reload_logs()

        def render_entries_to_dom(entries_with_idx: list, prepend: bool = False):
            filtered = [(i, e) for i, e in entries_with_idx if passes_filter(e)]
            
            if prepend:
                for idx, entry in reversed(filtered):
                    html_content = render_log_entry(entry)
                    with log_column:
                        el = ui.html(html_content, sanitize=False)
                        el.move(target_index=0)
                        view_state.elements.appendleft((idx, el))
            else:
                for idx, entry in filtered:
                    html_content = render_log_entry(entry)
                    with log_column:
                        el = ui.html(html_content, sanitize=False)
                        view_state.elements.append((idx, el))
            
            trim_excess_logs(from_top=not prepend)

        def trim_excess_logs(from_top: bool = True):
            while len(view_state.elements) > MAX_VISIBLE_LOGS:
                if from_top:
                    _, el = view_state.elements.popleft()
                    el.delete()
                    if view_state.elements:
                        view_state.start_raw_index = view_state.elements[0][0]
                else:
                    _, el = view_state.elements.pop()
                    el.delete()
                    if view_state.elements:
                        view_state.end_raw_index = view_state.elements[-1][0] + 1

        async def load_older_logs():
            if not log_handler or view_state.start_raw_index <= 0:
                return
            if view_state.loading_older:
                return
            
            view_state.loading_older = True
            try:
                load_count = min(LOAD_BATCH_SIZE, view_state.start_raw_index)
                new_start = view_state.start_raw_index - load_count
                
                entries = log_handler.get_entries(new_start, load_count)
                
                if entries:
                    # Save scroll height before prepending
                    scroll_id = view_state.scroll_id
                    if scroll_id:
                        old_height = await ui.run_javascript(
                            f'document.getElementById("{scroll_id}")?.querySelector(".q-scrollarea__container")?.scrollHeight || 0'
                        )
                    else:
                        old_height = 0
                    
                    entries_with_idx = list(enumerate(entries, start=new_start))
                    render_entries_to_dom(entries_with_idx, prepend=True)
                    view_state.start_raw_index = new_start
                    
                    # Restore scroll position
                    if scroll_id and old_height:
                        await ui.run_javascript(f'''
                            (function() {{
                                var container = document.getElementById("{scroll_id}")?.querySelector(".q-scrollarea__container");
                                if (container) {{
                                    var newHeight = container.scrollHeight;
                                    var delta = newHeight - {old_height};
                                    container.scrollTop += delta;
                                }}
                            }})()
                        ''')
                    
                    if view_state.start_raw_index > 0:
                        view_state.loaded_at_top = False
            finally:
                view_state.loading_older = False

        async def load_newer_logs():
            if not log_handler:
                return
            
            end_index = view_state.end_raw_index
            total = len(log_handler)
            
            if end_index >= total:
                return
            if view_state.loading_older:
                return
            
            view_state.loading_older = True
            try:
                available = total - end_index
                load_count = min(LOAD_BATCH_SIZE, available)
                
                entries = log_handler.get_entries(end_index, load_count)
                
                if entries:
                    entries_with_idx = list(enumerate(entries, start=end_index))
                    render_entries_to_dom(entries_with_idx, prepend=False)
                    view_state.end_raw_index = end_index + load_count
                    
                    if view_state.end_raw_index < len(log_handler):
                        view_state.loaded_at_bottom = False
            finally:
                view_state.loading_older = False

        def reload_logs():
            if not log_handler:
                return

            log_column.clear()
            view_state.elements.clear()
            pending_logs.clear()

            total = len(log_handler)

            # Fetch only the last N entries instead of the entire buffer
            fetch_start = max(0, total - MAX_VISIBLE_LOGS * 2)
            recent_entries = log_handler.get_entries(fetch_start, total - fetch_start)

            filtered = [(fetch_start + i, e) for i, e in enumerate(recent_entries) if passes_filter(e)]

            display = filtered[-MAX_VISIBLE_LOGS:]

            if display:
                view_state.start_raw_index = display[0][0]
                view_state.end_raw_index = total
            else:
                view_state.start_raw_index = total
                view_state.end_raw_index = total

            with log_column:
                for i, entry in display:
                    html_content = render_log_entry(entry)
                    el = ui.html(html_content, sanitize=False)
                    view_state.elements.append((i, el))

            view_state.auto_scroll = True
            ui.timer(0.1, lambda: log_scroll.scroll_to(percent=1.0), once=True)

        def handle_new_log(entry: dict):
            # Called from any thread — only queue, never touch UI
            if not view_state.listener_active:
                return
            pending_logs.append(entry)

        def process_pending_logs():
            if not client.has_socket_connection:
                pending_logs.clear()
                return

            if not pending_logs:
                return

            try:
                rendered_any = False
                total_now = len(log_handler) if log_handler else 0
                view_state.total_in_buffer = total_now
                num_pending = len(pending_logs)
                
                expected_start_idx = total_now - num_pending
                is_at_bottom = (view_state.end_raw_index >= expected_start_idx)

                while pending_logs:
                    entry = pending_logs.popleft()

                    raw_idx = expected_start_idx
                    expected_start_idx += 1

                    if not is_at_bottom:
                        continue
                        
                    if view_state.end_raw_index > raw_idx:
                        continue

                    view_state.end_raw_index = raw_idx + 1

                    if not passes_filter(entry):
                        continue

                    html_content = render_log_entry(entry)
                    with log_column:
                        el = ui.html(html_content, sanitize=False)
                        view_state.elements.append((raw_idx, el))

                    trim_excess_logs(from_top=True)
                    rendered_any = True

                if rendered_any and view_state.auto_scroll:
                    log_scroll.scroll_to(percent=1.0)

            except Exception:
                logging.debug("Error processing pending logs", exc_info=True)

        async def handle_scroll_check():
            if not client.has_socket_connection:
                return
            
            scroll_id = view_state.scroll_id
            if not scroll_id:
                return
            
            try:
                # Get scroll position
                scroll_info = await ui.run_javascript(f'''
                    (function() {{
                        var container = document.getElementById("{scroll_id}")?.querySelector(".q-scrollarea__container");
                        if (!container) return null;
                        return {{
                            scrollTop: container.scrollTop,
                            scrollHeight: container.scrollHeight,
                            clientHeight: container.clientHeight
                        }};
                    }})()
                ''')
                
                if not scroll_info:
                    return
                
                scroll_top = scroll_info.get("scrollTop", 0)
                scroll_height = scroll_info.get("scrollHeight", 0)
                client_height = scroll_info.get("clientHeight", 0)
                
                distance_from_bottom = scroll_height - scroll_top - client_height
                
                if distance_from_bottom <= SCROLL_BOTTOM_THRESHOLD:
                    view_state.auto_scroll = True
                    view_state.initial_scroll_done = True
                    if scroll_bottom_btn:
                        scroll_bottom_btn.set_visibility(False)
                elif distance_from_bottom > SCROLL_BOTTOM_THRESHOLD + 50:
                    view_state.auto_scroll = False
                    if scroll_bottom_btn and view_state.initial_scroll_done:
                        scroll_bottom_btn.set_visibility(True)
                
                if scroll_top > SCROLL_LOAD_THRESHOLD + 200:
                    view_state.loaded_at_top = False
                if distance_from_bottom > SCROLL_LOAD_THRESHOLD + 200:
                    view_state.loaded_at_bottom = False
                
                # Load older logs near top
                if scroll_top < SCROLL_LOAD_THRESHOLD and view_state.start_raw_index > 0:
                    if not view_state.loaded_at_top:
                        view_state.loaded_at_top = True
                        await load_older_logs()
                
                # Load newer logs near bottom
                end_index = view_state.end_raw_index
                total = len(log_handler) if log_handler else 0
                if distance_from_bottom < SCROLL_LOAD_THRESHOLD and end_index < total:
                    if not view_state.loaded_at_bottom:
                        view_state.loaded_at_bottom = True
                        await load_newer_logs()
                    
            except Exception:
                pass

        view_state.scroll_id = f"c{log_scroll.id}"
        
        if scroll_bottom_btn:
            scroll_bottom_btn.on_click(reload_logs)
        
        if log_handler:
            view_state.total_in_buffer = len(log_handler)
            reload_logs()

            listener_id = log_handler.add_listener(handle_new_log)

            scroll_timer = ui.timer(0.3, handle_scroll_check)
            log_process_timer = ui.timer(0.15, process_pending_logs)

        _conn_rows = {}
        _conn_client_set_ids = frozenset()

        def _build_connections_full(active_conns, now):
            row_classes = "w-full p-2 gap-2 no-wrap items-center"

            ip_cell = "w-28 md:w-36 flex-shrink-0 text-xs md:text-sm leading-tight"
            page_cell = "w-20 md:w-24 flex-shrink-0 text-xs md:text-sm leading-tight"
            client_id_cell = "flex-1 min-w-0 text-xs md:text-sm leading-tight hidden md:!block whitespace-nowrap overflow-hidden text-ellipsis"

            right_cell = "w-20 text-xs md:text-sm text-center flex-shrink-0"
            right_cell_first = f"{right_cell} ml-auto"

            _conn_rows.clear()
            conn_container.clear()
            with conn_container:
                header_row = ui.row().classes(f"{row_classes} rounded font-bold")
                header_row.style("background-color: #f5f5f5")
                header_row.classes(add="dark:!bg-neutral-800")
                with header_row:
                    ui.label("IP Address").classes(ip_cell)
                    ui.label("Page").classes(page_cell)
                    ui.label("Client ID").classes(client_id_cell)
                    ui.label("Duration").classes(right_cell_first)
                    ui.label("Last Seen").classes(right_cell)

                sorted_conns = sorted(
                    active_conns.items(), key=lambda x: x[1]["connected_at"]
                )

                for cid, conn in sorted_conns:
                    duration = now - conn["connected_at"]
                    seconds = int(duration.total_seconds())
                    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
                    dur_str = f"{h:02d}:{m:02d}:{s:02d}"

                    last_seen = conn.get("last_seen", conn["connected_at"])
                    last_seen_str = last_seen.strftime("%H:%M:%S")

                    with ui.row().classes(f"{row_classes} border-b border-gray-200 dark:border-gray-700"):
                        ui.label(conn["ip"]).classes(f"{ip_cell} font-mono")
                        ui.label(conn["page"]).classes(page_cell)
                        ui.label(cid).classes(f"{client_id_cell} font-mono text-gray-500")
                        dur_lbl = ui.label(dur_str).classes(f"{right_cell_first} font-mono")
                        seen_lbl = ui.label(last_seen_str).classes(f"{right_cell} font-mono")

                    _conn_rows[cid] = {"duration": dur_lbl, "last_seen": seen_lbl}

        def update_connections():
            nonlocal _conn_client_set_ids
            if not client.has_socket_connection:
                return

            try:
                active_conns = state.active_connections
                current_ids = frozenset(active_conns.keys())
                now = datetime.now()

                if current_ids != _conn_client_set_ids:
                    _conn_client_set_ids = current_ids
                    _build_connections_full(active_conns, now)
                else:
                    for cid, conn in active_conns.items():
                        if cid in _conn_rows:
                            duration = now - conn["connected_at"]
                            seconds = int(duration.total_seconds())
                            h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
                            _conn_rows[cid]["duration"].set_text(f"{h:02d}:{m:02d}:{s:02d}")

                            last_seen = conn.get("last_seen", conn["connected_at"])
                            _conn_rows[cid]["last_seen"].set_text(last_seen.strftime("%H:%M:%S"))
            except Exception:
                logging.debug("Error updating connections view", exc_info=True)

        update_connections()
        con_timer = ui.timer(1.0, update_connections)

        if log_handler:
            def cleanup_listener():
                view_state.listener_active = False
                pending_logs.clear()
                con_timer.deactivate()
                scroll_timer.deactivate()
                log_process_timer.deactivate()
                log_handler.remove_listener(listener_id)

            client.on_disconnect(cleanup_listener)