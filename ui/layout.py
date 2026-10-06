import logging
from abc import ABC, abstractmethod
from typing import Callable
from nicegui import ui

MISSING_TEXT = "—"  # shown when a value that was displayed is no longer fresh


def decode_register(snapshot: dict[int, int], reg_id: int | None, meta: dict | None) -> float | None:
    # Scaled value of reg_id, or None if any needed word is missing.
    # Optional meta: hi_reg_id (32-bit value, high word at that address), signed (two's complement).
    if reg_id is None or reg_id not in snapshot:
        return None
    meta = meta or {}
    raw = snapshot[reg_id]
    bits = 16
    hi = meta.get('hi_reg_id')
    if hi is not None:
        if hi not in snapshot:
            return None
        raw = (snapshot[hi] << 16) | raw
        bits = 32
    if meta.get('signed') and raw >= 1 << (bits - 1):
        raw -= 1 << bits
    return raw * meta.get('scale', 1)


class BaseWidget(ABC):
    def __init__(self, meta_map: dict[int, dict] | None = None):
        self.meta_map = meta_map or {}

    def get_value(self, snapshot: dict[int, int], reg_id: int | None) -> float | None:
        if reg_id is None:
            return None
        return decode_register(snapshot, reg_id, self.meta_map.get(reg_id))

    @staticmethod
    def format_value(val: float, unit: str = '', scale: float = 1.0, decimals: int | None = None) -> str:
        if decimals is not None:
            text = f"{val:.{decimals}f}"
            if float(text) == 0:
                text = f"{0.0:.{decimals}f}"  # avoid "-0.0"
            return f"{text} {unit}" if unit else text
        if unit == '':
            return f"{int(val)}"
        elif scale == 0.01:
            return f"{val:.2f} {unit}"
        elif scale == 1:
            return f"{int(val)} {unit}"
        else:
            return f"{val:.1f} {unit}"

    @abstractmethod
    def build(self) -> None:
        pass
    
    @abstractmethod
    def update(self, snapshot: dict[int, int]) -> None:
        pass


class RegisterCard(BaseWidget):
    
    def __init__(
        self,
        meta_map: dict[int, dict],
        reg_id: int,
        title: str | None = None,
        fill: str | None = None,  # 'high_good' or 'low_good'
        star: bool = False,
    ):
        super().__init__(meta_map)
        self.reg_id = reg_id
        
        meta = self.meta_map.get(reg_id, {})
        self.title = title or meta.get('name', f'Register {reg_id}')
        self.fill = fill or meta.get('fill')
        self.star = star
        
        self.card = None
        self.label = None
    
    def build(self) -> None:
        self.card = ui.card().classes(
            "no-shadow border-[1px] border-gray-200 dark:border-gray-700 h-full flex flex-col"
        )
        with self.card:
            ui.label(self.title).classes(
                "text-gray-500 dark:text-gray-400 text-xs font-bold uppercase tracking-wider"
            )
            self.label = ui.label("...").classes(
                "text-2xl font-bold text-blue-900 dark:text-blue-300 mt-auto"
            )
    
    def update(self, snapshot: dict[int, int]) -> None:
        if self.label is None:
            return
        
        val = self.get_value(snapshot, self.reg_id)
        if val is None:
            # Keep "..." until the first value; afterwards mark it as missing/stale
            if self.label.text not in ("...", MISSING_TEXT):
                self.label.set_text(MISSING_TEXT)
                if self.fill and self.card:
                    self.card.style('--fill-pct: 0%')
            return
        meta = self.meta_map.get(self.reg_id, {})
        scale = meta.get('scale', 1)
        unit = meta.get("unit", "")

        self.label.set_text(self.format_value(val, unit, scale, meta.get('decimals')))
        

        if self.fill and self.card:
            style = self._get_fill_style(val, self.fill)
            self.card.style(style)
            self.card.update()
    
    @staticmethod
    def _get_fill_style(val: float, fill_mode: str) -> str:
        # Linear interpolation: 0% → red, 50% → yellow, 100% → green.
        # Returns CSS custom properties consumed by get_fill_css().
        percentage = max(0.0, min(100.0, val))
        
        if fill_mode == 'low_good':
            color_val = 100 - percentage
        else:  # high_good
            color_val = percentage
        
        if color_val <= 50:
            ratio = color_val / 50
            r_dark, g_dark, b_dark = 140, int(70 + ratio * 70), 70
            r_light, g_light, b_light = 220, int(50 + ratio * 170), 50
        else:
            ratio = (color_val - 50) / 50
            r_dark, g_dark, b_dark = int(140 - ratio * 60), 140, int(70 + ratio * 30)
            r_light, g_light, b_light = int(220 - ratio * 120), 220, int(50 + ratio * 50)
        
        bg_color_light = f"rgba({r_light}, {g_light}, {b_light}, 0.3)"
        bg_color_dark = f"rgba({r_dark}, {g_dark}, {b_dark}, 0.4)"
        
        return (
            f"--fill-color-light: {bg_color_light}; "
            f"--fill-color-dark: {bg_color_dark}; "
            f"--fill-pct: {percentage}%;"
        )


class StripCard(RegisterCard):
    
    def __init__(
        self,
        meta_map: dict[int, dict],
        reg_id: int,
        title: str | None = None,
        mode: str = 'high_good',  # 'high_good' or 'low_good'
        star: bool = False,
    ):
        super().__init__(meta_map, reg_id, title=title, star=star)
        self.mode = mode
        self.bar = None
        self.val_label = None
    
    def build(self) -> None:
        self.card = ui.card().classes(
            'no-shadow border-[1px] border-gray-200 dark:border-gray-700 col-span-2 sm:col-span-1'
        )
        with self.card:
            with ui.row().classes("w-full items-center justify-between mb-1"):
                ui.label(self.title).classes(
                    "text-gray-500 dark:text-gray-400 text-xs font-bold uppercase tracking-wider"
                )
                self.val_label = ui.label("...").classes(
                    "text-sm font-bold text-gray-700 dark:text-gray-300"
                )
            
            with ui.element('div').classes(
                'w-full h-4 rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden relative border border-gray-100 dark:border-gray-700'
            ):
                self.bar = ui.element('div').classes(
                    'h-full transition-all duration-500'
                ).style('width: 0%')
    
    def update(self, snapshot: dict[int, int]) -> None:
        if self.bar is None or self.val_label is None:
            return
        
        val = self.get_value(snapshot, self.reg_id)
        if val is None:
            if self.val_label.text not in ("...", MISSING_TEXT):
                self.val_label.set_text(MISSING_TEXT)
                self.bar.style('width: 0%')
            return
        meta = self.meta_map.get(self.reg_id, {})
        scale = meta.get('scale', 1)
        unit = meta.get('unit', '')

        self.val_label.set_text(self.format_value(val, unit, scale, meta.get('decimals')))
        
        # Update bar
        pct = max(0.0, min(100.0, val))
        self.bar.style(f'width: {pct}%')
        
        color = self._get_color(pct)
        self.bar.classes(replace='h-full transition-all duration-500 ' + color)
        self.bar.update()
        
    def _get_color(self, pct: float) -> str:
        if self.mode == 'low_good':
            pct = 100 - pct
        if pct < 30:
            return "bg-red-500"
        elif pct < 70:
            return "bg-orange-400"
        else:
            return "bg-green-500"


class MathCard(BaseWidget):
    
    def __init__(
        self,
        meta_map: dict[int, dict],
        title: str,
        variables: list[int],
        formula: Callable[[dict[int, float]], float],
        unit: str = '',
        scale: float = 1.0,
        star: bool = False,
        decimals: int | None = None,
    ):
        super().__init__(meta_map)
        self.title = title
        self.variables = variables
        self.formula = formula  # Lambda: (var_dict) -> result
        self.unit = unit
        self.scale = scale
        self.decimals = decimals
        self.star = star
        
        self.card = None
        self.label = None
    
    def build(self) -> None:
        self.card = ui.card().classes(
            'no-shadow border-[1px] border-gray-200 dark:border-gray-700 h-full flex flex-col'
        )
        with self.card:
            ui.label(self.title).classes(
                'text-gray-500 dark:text-gray-400 text-xs font-bold uppercase tracking-wider'
            )
            self.label = ui.label('...').classes(
                'text-2xl font-bold text-purple-700 dark:text-purple-300 mt-auto'
            )
    
    def update(self, snapshot: dict[int, int]) -> None:
        if self.label is None:
            return
        
        try:
            var_dict = {}
            for reg_id in self.variables:
                val = self.get_value(snapshot, reg_id)
                if val is None:  # Missing or stale input: don't compute from zeros
                    if self.label.text not in ("...", MISSING_TEXT):
                        self.label.set_text(MISSING_TEXT)
                    return
                var_dict[reg_id] = val
            
            result = self.formula(var_dict) * self.scale

            self.label.set_text(self.format_value(result, self.unit, self.scale, self.decimals))
        except Exception:
            logging.debug("MathCard '%s' formula error", self.title, exc_info=True)
            self.label.set_text("Err")


class EnergyStackWidget(BaseWidget):
    
    def __init__(
        self,
        meta_map: dict[int, dict],
        title: str = 'Load Source',
        solar_w_id: int | list[int] | None = None,  # list: summed (e.g. PV1 + PV2)
        grid_w_id: int | None = None,
        batt_v_id: int | None = None,
        batt_dis_i_id: int | None = None,
        star: bool = True
    ):
        super().__init__(meta_map)
        self.title = title
        if solar_w_id is None:
            self.solar_w_ids = []
        elif isinstance(solar_w_id, list):
            self.solar_w_ids = solar_w_id
        else:
            self.solar_w_ids = [solar_w_id]
        self.grid_w_id = grid_w_id
        self.batt_v_id = batt_v_id
        self.batt_dis_i_id = batt_dis_i_id
        self.star = star
        
        self.card = None
        self.bars = {}
        self.labels = {}
        self.has_data = False
    
    def build(self) -> None:
        self.card = ui.card().classes(
            "no-shadow border-[1px] border-gray-200 dark:border-gray-700 col-span-2"
        )
        with self.card:
            ui.label(self.title).classes(
                "text-gray-500 dark:text-gray-400 text-xs font-bold uppercase tracking-wider mb-1"
            )
            
            with ui.element('div').classes(
                'w-full h-8 flex rounded-md overflow-hidden bg-gray-100 dark:bg-gray-800 relative'
            ):
                # Solar (Amber)
                self.bars['sol'] = ui.element('div').classes(
                    'h-full bg-amber-200 dark:bg-amber-400 flex items-center justify-center transition-all duration-500'
                ).style('width: 0%')
                with self.bars['sol']:
                    self.labels['sol'] = ui.label('').classes(
                        'text-[10px] font-bold text-amber-800 dark:!text-black px-1 truncate'
                    )
                    ui.tooltip('Solar Power').classes(
                        'bg-amber-300 dark:bg-amber-400 text-black dark:!text-black text-xs text-center'
                    )
                
                # Grid (Crimson)
                self.bars['grid'] = ui.element('div').classes(
                    'h-full bg-red-200 dark:bg-[#6b0f0f] flex items-center justify-center transition-all duration-500'
                ).style('width: 0%')
                with self.bars['grid']:
                    self.labels['grid'] = ui.label('').classes(
                        'text-[10px] font-bold text-red-900 dark:text-red-100 px-1 truncate'
                    )
                    ui.tooltip('Grid Power').classes(
                        'bg-red-300 dark:bg-[#580c0c] text-red-900 dark:text-red-100 text-xs text-center'
                    )
                
                # Battery (Lime)
                self.bars['batt'] = ui.element('div').classes(
                    'h-full bg-lime-200 dark:bg-lime-500 flex items-center justify-center transition-all duration-500'
                ).style('width: 0%')
                with self.bars['batt']:
                    self.labels['batt'] = ui.label('').classes(
                        'text-[10px] font-bold text-lime-900 dark:!text-black px-1 truncate'
                    )
                    ui.tooltip('Battery Discharge').classes(
                        'bg-lime-300 dark:bg-lime-600 text-lime-900 dark:!text-black text-xs text-center'
                    )
            
            self.labels["total"] = ui.label("Total: 0 W").classes(
                "text-xs text-center w-full mt-1 text-gray-500"
            )
    
    def update(self, snapshot: dict[int, int]) -> None:
        ids = self.solar_w_ids + [self.grid_w_id, self.batt_v_id, self.batt_dis_i_id]
        values = {i: self.get_value(snapshot, i) for i in ids if i is not None}
        if any(v is None for v in values.values()):
            # Missing or stale input: a partial split would be misleading
            if self.has_data:
                self.has_data = False
                for key in ('sol', 'grid', 'batt'):
                    self.bars[key].style('width: 0%')
                    self.labels[key].set_text('')
                self.labels['total'].set_text(f"Total Load: {MISSING_TEXT}")
            return
        try:
            p_sol = sum(values[i] for i in self.solar_w_ids)
            
            p_grid = values.get(self.grid_w_id, 0.0)
            
            v_batt = values.get(self.batt_v_id, 0.0)
            i_batt = values.get(self.batt_dis_i_id, 0.0)
            p_batt = v_batt * i_batt
            
            p_sol = max(0.0, p_sol)
            p_grid = max(0.0, p_grid)
            p_batt = max(0.0, p_batt)
            
            total_watts = p_sol + p_grid + p_batt
            
            if total_watts > 10:
                pct_sol = (p_sol / total_watts) * 100
                pct_grid = (p_grid / total_watts) * 100
                pct_batt = (p_batt / total_watts) * 100
            else:
                pct_sol, pct_grid, pct_batt = 0, 0, 0
            
            self.bars['sol'].style(f'width: {pct_sol}%')
            self.bars['grid'].style(f'width: {pct_grid}%')
            self.bars['batt'].style(f'width: {pct_batt}%')
            
            self.labels['sol'].set_text(f"{int(p_sol)}W" if pct_sol > 10 else "")
            self.labels['grid'].set_text(f"{int(p_grid)}W" if pct_grid > 10 else "")
            self.labels['batt'].set_text(f"{int(p_batt)}W" if pct_batt > 10 else "")
            
            self.labels['total'].set_text(f"Total Load: {int(total_watts)} W")
            self.has_data = True
        
        except Exception:
            logging.debug("EnergyStackWidget '%s' calc error", self.title, exc_info=True)
            self.labels['total'].set_text("Error calc")


# --- PUBLIC ---
def build_interface(
    widgets: list[BaseWidget],
    show_all: bool = False,
) -> list[BaseWidget]:
    active_widgets = []
    
    for widget in widgets:
        # Filter: show only starred unless show_all
        should_show = (
            show_all or 
            getattr(widget, 'star', False)
        )
        
        if should_show:
            widget.build()
            active_widgets.append(widget)
    
    return active_widgets


def get_secondary_page_css() -> str:
    return '''
        .inspector-scroll { max-width: 100%; overflow-x: auto; }
        .inspector-table { min-width: 560px; }
        .inspector-table th, .inspector-table td:not(:last-child) { white-space: nowrap; }
        .inspector-table td:last-child { min-width: 160px; }
        .logs-header { height: 66px; flex-wrap: nowrap; }
        .logs-shell { height: calc(100dvh - 74px); min-height: 0; }
        .logs-shell .q-tab-panels, .logs-shell .q-tab-panel { min-height: 0; }
        .connections-scroll { overflow: auto; }
        .connections-table { min-width: 440px; }
        @media (min-width: 640px) {
            .inspector-container { max-width: 1280px; margin-inline: auto; }
        }
        @media (max-width: 639px) {
            .secondary-header { padding: 10px 12px; gap: 8px; flex-wrap: nowrap; }
            .secondary-title { font-size: 18px; line-height: 24px; min-width: 0; }
            .secondary-header > .q-icon { font-size: 24px !important; }
            .secondary-header .q-btn { min-width: 44px; min-height: 44px; }
            .secondary-actions { flex-shrink: 0; }
            .nicegui-content:has(.inspector-container) { padding: 12px; }
            .inspector-container { padding: 0; }
            .inspector-table { font-size: 13px; }
            .logs-header { height: 64px; }
            .logs-shell { height: calc(100dvh - 72px); gap: 8px; }
            .logs-tabs .q-tab { padding: 0 10px; min-height: 44px; }
            .logs-tabs .q-tab__label { font-size: 13px; }
            .logs-filters { gap: 4px; flex-wrap: nowrap; }
            .logs-filters .q-btn { padding: 0 8px; min-height: 36px; }
            .log-entry { min-width: 0; width: 100%; flex-wrap: wrap; font-size: 13px; }
            .log-message { width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; }
            .connections-table { font-size: 13px; }
        }
    '''


def get_dashboard_css() -> str:
    return r'''
        .dashboard-grid > .q-card { min-width: 0; }
        .dashboard-container { max-width: 1600px; margin-inline: auto; }
        @media (min-width: 1536px) {
            .dashboard-grid .text-2xl { font-size: 28px; line-height: 36px; }
        }
        @media (min-width: 640px) {
            .dashboard-grid .uppercase {
                text-transform: none;
                letter-spacing: normal;
                font-size: 14px;
                line-height: 20px;
                font-weight: 500;
            }
        }
        @media (min-width: 1920px) {
            .dashboard-grid { gap: 20px; }
            .dashboard-grid > .q-card { padding: 20px; gap: 20px; }
            .dashboard-grid .text-xs { font-size: 14px; line-height: 20px; }
            .dashboard-grid .uppercase,
            .dashboard-grid .text-sm { font-size: 16px; line-height: 24px; }
            .dashboard-grid .text-2xl { font-size: 32px; line-height: 40px; }
            .dashboard-grid [class~="text-[10px]"] { font-size: 12px; line-height: 16px; }
            .dashboard-grid .h-4 { height: 20px; }
            .dashboard-grid .h-8 { height: 40px; }
        }
        @media (max-width: 639px) {
            .nicegui-content:has(.dashboard-container) { padding: 12px; }
            .dashboard-container { padding: 0; }
            .dashboard-grid { gap: 10px; }
            .dashboard-grid > .q-card { padding: 12px; gap: 8px; }
            .dashboard-grid .uppercase {
                text-transform: none;
                letter-spacing: normal;
                font-size: 13px;
                line-height: 18px;
                font-weight: 500;
                overflow-wrap: anywhere;
            }
            .dashboard-grid .text-2xl {
                font-size: 24px;
                line-height: 30px;
                overflow-wrap: anywhere;
            }
            .dashboard-grid [class~="text-[10px]"] { font-size: 12px; }
            .dashboard-header { padding: 10px 12px; gap: 8px; flex-wrap: nowrap; }
            .dashboard-title { font-size: 18px; line-height: 24px; min-width: 0; }
            .dashboard-actions { flex-shrink: 0; }
            .dashboard-actions > .q-btn { min-width: 44px; min-height: 44px; }
            .dashboard-header > .q-icon { font-size: 24px !important; }
        }
        @media (max-width: 339px) {
            .dashboard-grid { grid-template-columns: minmax(0, 1fr); }
            .dashboard-grid > .q-card { grid-column: span 1 / span 1; }
        }
    '''


def get_fill_css() -> str:
    # Cards with --fill-color CSS vars get a horizontal gradient fill.
    return """
    <style>
        .q-card[style*="--fill-color"] {
            background: linear-gradient(to right, var(--fill-color-light) var(--fill-pct, 0%), transparent var(--fill-pct, 0%)) !important;
        }
        @media (prefers-color-scheme: dark) {
            .q-card[style*="--fill-color"] {
                background: linear-gradient(to right, var(--fill-color-dark) var(--fill-pct, 0%), transparent var(--fill-pct, 0%)) !important;
            }
        }
        .dark .q-card[style*="--fill-color"] {
            background: linear-gradient(to right, var(--fill-color-dark) var(--fill-pct, 0%), transparent var(--fill-pct, 0%)) !important;
        }
    </style>
    """
