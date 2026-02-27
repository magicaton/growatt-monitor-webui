from nicegui import ui, Client

from core.dashboard_config import get_config


def create_inspector_page(client: Client, state) -> None:
    # Auto dark mode
    ui.add_css(':root { color-scheme: light dark; }')
    
    ui.colors(primary='#d45858')
    
    with ui.header().classes("bg-red-900 items-center shadow-lg"):
        ui.icon("manage_search", size="md", color="yellow-400")
        ui.label("Growatt Register Inspector").classes("text-xl font-bold text-white")
        
        ui.element("div").classes("flex-grow")
        target_url = "/"
        ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to(target_url)).props("flat color=white")
    
    with ui.element('div').classes('w-full p-4'):
        table_container = ui.element('div').classes('w-full')
        
        ui_cells = {}  # {addr: {'raw': el, 'x01': el, 'x001': el, 'named': el}}
        
        def build_table():
            nonlocal ui_cells
            ui_cells = {}
            table_container.clear()

            addr_set: set[int] = set()
            for start_addr, count in state.inspector_chunks:
                if count <= 0:
                    continue
                addr_set.update(range(start_addr, start_addr + count))

            row_addrs = sorted(addr_set)
            config = get_config()
            
            with table_container:
                # Columns: Index, Raw, x0.1, x0.01, Named Value
                with ui.element('div').classes('overflow-x-auto'):
                    with ui.element('table').classes('w-full border-collapse text-sm'):
                        with ui.element('thead'):
                            with ui.element('tr').classes('bg-gray-200 dark:bg-gray-700'):
                                for header in ["Reg #", "Raw", "×0.1", "×0.01", "Named Value"]:
                                    with ui.element("th").classes(
                                        "border border-gray-300 dark:border-gray-600 "
                                        "px-2 py-1 text-left font-bold"
                                    ):
                                        ui.label(header)
                        
                        with ui.element('tbody'):
                            for addr in row_addrs:
                                meta = config.get_register_meta(addr)
                                
                                with ui.element('tr').classes(
                                    'hover:bg-gray-100 dark:hover:bg-gray-800'
                                ):
                                    with ui.element('td').classes(
                                        'border border-gray-300 dark:border-gray-600 '
                                        'px-2 py-1 font-mono'
                                    ):
                                        idx_class = (
                                            'font-bold text-blue-600 dark:text-blue-400'
                                            if meta else 'text-gray-500'
                                        )
                                        ui.label(str(addr)).classes(idx_class)
                                    
                                    with ui.element('td').classes(
                                        'border border-gray-300 dark:border-gray-600 '
                                        'px-2 py-1 font-mono'
                                    ):
                                        raw_lbl = ui.label("---").classes(
                                            "text-gray-600 dark:text-gray-400"
                                        )
                                    
                                    with ui.element('td').classes(
                                        'border border-gray-300 dark:border-gray-600 '
                                        'px-2 py-1 font-mono'
                                    ):
                                        x01_lbl = ui.label("---").classes(
                                            "text-gray-600 dark:text-gray-400"
                                        )
                                    
                                    with ui.element('td').classes(
                                        'border border-gray-300 dark:border-gray-600 '
                                        'px-2 py-1 font-mono'
                                    ):
                                        x001_lbl = ui.label("---").classes(
                                            "text-gray-600 dark:text-gray-400"
                                        )
                                    
                                    with ui.element('td').classes(
                                        'border border-gray-300 dark:border-gray-600 px-2 py-1'
                                    ):
                                        if meta:
                                            named_lbl = ui.label("---").classes(
                                                "font-bold text-blue-700 dark:text-blue-300"
                                            )
                                            ui.label(f" ({meta.get('name', 'Unknown')})").classes(
                                                "text-xs text-gray-500 dark:text-gray-400"
                                            )
                                        else:
                                            named_lbl = ui.label("-").classes("text-gray-400")
                                    
                                    ui_cells[addr] = {
                                        "raw": raw_lbl,
                                        "x01": x01_lbl,
                                        "x001": x001_lbl,
                                        "named": named_lbl if meta else None,
                                        "meta": meta
                                    }
        
        build_table()
        
        def update_inspector_view():
            current_data = state.latest_data
            
            for addr, cells in ui_cells.items():
                if addr in current_data:
                    raw_val = current_data[addr]
                    
                    cells['raw'].set_text(str(raw_val))
                    
                    cells['x01'].set_text(f"{raw_val * 0.1:.1f}")
                    
                    cells['x001'].set_text(f"{raw_val * 0.01:.2f}")
                    
                    meta = cells['meta']
                    if meta and cells['named']:
                        scale = meta.get('scale', 1)
                        val = raw_val * scale
                        unit = meta.get('unit', '')
                        
                        if scale == 1:
                            val_str = f"{int(val)} {unit}"
                        elif scale == 0.01:
                            val_str = f"{val:.2f} {unit}"
                        else:
                            val_str = f"{val:.1f} {unit}"
                            
                        cells['named'].set_text(val_str.strip())
        
        ui.timer(1.0, update_inspector_view)
