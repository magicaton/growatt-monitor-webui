import logging
from abc import ABC, abstractmethod
from typing import Callable
from nicegui import ui

class BaseWidget(ABC):
    def __init__(self, meta_map: dict[int, dict] | None = None):
        self.meta_map = meta_map or {}

    def get_scaled_value(self, snapshot: dict[int, int], reg_id: int | None) -> float:
        if reg_id is None or reg_id not in snapshot:
            return 0.0

        raw = snapshot[reg_id]
        meta = self.meta_map.get(reg_id, {})
        scale = meta.get('scale', 1)
        return raw * scale

    @staticmethod
    def format_value(val: float, unit: str = '', scale: float = 1.0) -> str:
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
        if self.reg_id not in snapshot or self.label is None:
            return
        
        val = self.get_scaled_value(snapshot, self.reg_id)
        meta = self.meta_map.get(self.reg_id, {})
        scale = meta.get('scale', 1)
        unit = meta.get("unit", "")

        self.label.set_text(self.format_value(val, unit, scale))
        

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
        if self.reg_id not in snapshot or self.bar is None or self.val_label is None:
            return
        
        val = self.get_scaled_value(snapshot, self.reg_id)
        meta = self.meta_map.get(self.reg_id, {})
        scale = meta.get('scale', 1)
        unit = meta.get('unit', '')

        self.val_label.set_text(self.format_value(val, unit, scale))
        
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
    ):
        super().__init__(meta_map)
        self.title = title
        self.variables = variables
        self.formula = formula  # Lambda: (var_dict) -> result
        self.unit = unit
        self.scale = scale
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
                var_dict[reg_id] = self.get_scaled_value(snapshot, reg_id)
            
            result = self.formula(var_dict) * self.scale

            self.label.set_text(self.format_value(result, self.unit, self.scale))
        except Exception:
            logging.debug("MathCard '%s' formula error", self.title, exc_info=True)
            self.label.set_text("Err")


class EnergyStackWidget(BaseWidget):
    
    def __init__(
        self,
        meta_map: dict[int, dict],
        title: str = 'Load Source',
        solar_w_id: int | None = None,
        grid_w_id: int | None = None,
        batt_v_id: int | None = None,
        batt_dis_i_id: int | None = None,
        star: bool = True
    ):
        super().__init__(meta_map)
        self.title = title
        self.solar_w_id = solar_w_id
        self.grid_w_id = grid_w_id
        self.batt_v_id = batt_v_id
        self.batt_dis_i_id = batt_dis_i_id
        self.star = star
        
        self.card = None
        self.bars = {}
        self.labels = {}
    
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
        try:
            p_sol = self.get_scaled_value(snapshot, self.solar_w_id)
            
            p_grid = self.get_scaled_value(snapshot, self.grid_w_id)
            
            v_batt = self.get_scaled_value(snapshot, self.batt_v_id)
            i_batt = self.get_scaled_value(snapshot, self.batt_dis_i_id)
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
