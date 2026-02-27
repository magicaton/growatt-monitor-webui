import os
import yaml
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable


def compile_formula(expr: str) -> Callable[[dict[int, float]], float]:
    # Sandboxed eval: only allow safe math builtins, expose register
    # values as dict `v` (e.g. expr="v[1] - v[2]").
    safe_builtins = {
        "max": max,
        "min": min,
        "abs": abs,
        "round": round,
        "int": int,
        "float": float,
    }
    
    def formula_func(v: dict[int, float]) -> float:
        return eval(expr, {"__builtins__": safe_builtins}, {"v": v})
    
    return formula_func


@dataclass
class DashboardConfig:
    card_definitions: list[dict] = field(default_factory=list)
    _metadata: dict[int, dict] = field(default_factory=dict, repr=False)
    _required_ids: set[int] = field(default_factory=set, repr=False)

    def get_register_meta(self, reg_id: int) -> dict | None:
        return self._metadata.get(reg_id)

    @property
    def metadata(self) -> dict[int, dict]:
        return self._metadata

    @property
    def required_ids(self) -> set[int]:
        return set(self._required_ids)


# Module-level singleton
_config: DashboardConfig | None = None


def load_dashboard_config(path: str) -> DashboardConfig:
    global _config
    
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dashboard config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    card_definitions: list[dict] = data.get("widgets", [])
    metadata: dict[int, dict] = {}
    required_ids: set[int] = set()
    
    for widget_def in card_definitions:
        widget_reg_ids = _extract_widget_reg_ids(widget_def)
        required_ids.update(widget_reg_ids)
        
        reg_id = widget_def.get("reg_id")
        if reg_id is not None:
            meta = {}
            if "name" in widget_def:
                meta["name"] = widget_def["name"]
            elif "title" in widget_def:
                meta["name"] = widget_def["title"]
            if "unit" in widget_def:
                meta["unit"] = widget_def["unit"]
            if "scale" in widget_def:
                meta["scale"] = widget_def["scale"]

            if meta:
                metadata[reg_id] = meta
    
    for reg_def in data.get("registers", []):
        reg_id = reg_def.get("reg_id")
        if reg_id is None:
            logging.warning("Register without 'reg_id', skipping: %s", reg_def)
            continue
        
        meta = {}
        if "name" in reg_def:
            meta["name"] = reg_def["name"]
        if "unit" in reg_def:
            meta["unit"] = reg_def["unit"]
        if "scale" in reg_def:
            meta["scale"] = reg_def["scale"]
        
        metadata[reg_id] = meta
    
    # Create the dashboard config singleton
    _config = DashboardConfig(
        card_definitions=card_definitions,
        _metadata=metadata,
        _required_ids=required_ids,
    )
    
    logging.info(
        "Dashboard config: %d widgets, %d metadata, %d registers",
        len(card_definitions), len(metadata), len(required_ids),
    )
    
    return _config


def _extract_widget_reg_ids(widget_def: dict) -> set[int]:
    ids: set[int] = set()
    
    # reg_id field (RegisterCard, StripCard)
    if "reg_id" in widget_def:
        ids.add(widget_def["reg_id"])
    
    # variables list (MathCard)
    if "variables" in widget_def:
        ids.update(widget_def["variables"])
    
    # Wildcard *_id fields (e.g. solar_w_id, batt_v_id in EnergyStackWidget)
    for key, value in widget_def.items():
        if key.endswith("_id") and key != "reg_id" and isinstance(value, int):
            ids.add(value)
    
    return ids


def get_config() -> DashboardConfig:
    if _config is None:
        raise RuntimeError("Dashboard config not loaded. Call load_dashboard_config() first.")
    return _config


def create_widgets_from_config(
    widget_classes: dict[str, type] | None = None,
) -> list:
    config = get_config()
    
    if widget_classes is None:
        widget_classes = _get_widget_classes()
    
    widgets = []
    
    for widget_def in config.card_definitions:
        widget_type = widget_def.get("type")
        if not widget_type:
            continue
        
        widget_class = widget_classes.get(widget_type)
        if not widget_class:
            logging.warning("Unknown widget type '%s', skipping", widget_type)
            continue
        
        try:
            widget = _create_widget_instance(widget_class, widget_def)
            widgets.append(widget)
        except Exception as e:
            logging.error("Failed to create widget '%s': %s", widget_type, e)
            continue
    
    return widgets


def _create_widget_instance(widget_class: type, card_def: dict) -> object:
    # Introspect __init__ signature and auto-map YAML keys to constructor params.
    # Special cases: "formula" is compiled, "meta_map" is injected from config.
    sig = inspect.signature(widget_class.__init__)
    params = sig.parameters
    
    kwargs: dict[str, Any] = {}
    
    for param_name, param in params.items():
        if param_name == "self":
            continue
        
        # Skip *args/**kwargs
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        
        if param_name == "formula" and "formula" in card_def:
            kwargs["formula"] = compile_formula(card_def["formula"])
            continue

        if param_name == "meta_map":
            kwargs["meta_map"] = get_config().metadata
            continue
        
        if param_name in card_def:
            kwargs[param_name] = card_def[param_name]
        elif param.default is inspect.Parameter.empty:
            logging.warning(
                "Missing required param '%s' in config for %s",
                param_name, widget_class.__name__,
            )
    
    return widget_class(**kwargs)


def _get_widget_classes() -> dict[str, type]:
    from ui import layout
    
    classes: dict[str, type] = {}
    for name in dir(layout):
        obj = getattr(layout, name)
        if (
            isinstance(obj, type) 
            and issubclass(obj, layout.BaseWidget) 
            and obj is not layout.BaseWidget
        ):
            classes[name] = obj
    
    return classes
