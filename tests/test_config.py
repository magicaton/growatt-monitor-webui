import os

import pytest

from core.config import Config
from core.dashboard_config import load_dashboard_config
from core.modbus_core import build_optimized_chunks

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_config_template_loads():
    conf = Config()
    conf.load_from_file(os.path.join(ROOT, "config_templates", "config.yaml"))
    assert conf.server_port == 8080
    assert conf.inspector_chunks == [(0, 40), (40, 40), (80, 40)]


@pytest.mark.parametrize("text", ["", "# only comments\n", "logging:\nmodbus:\n"])
def test_empty_config_uses_defaults(tmp_path, text):
    conf = Config()
    conf.load_from_file(write(tmp_path, "config.yaml", text))
    assert conf.com_port == "auto"
    assert conf.server_port == 8080


def test_config_must_be_a_mapping(tmp_path):
    with pytest.raises(ValueError):
        Config().load_from_file(write(tmp_path, "config.yaml", "- a\n- b\n"))


@pytest.mark.parametrize("text", ["", "widgets:\nregisters:\n"])
def test_empty_dashboard_config(tmp_path, text):
    config = load_dashboard_config(write(tmp_path, "dashboard.yaml", text))
    assert config.card_definitions == []
    assert config.required_ids == set()


def test_dashboard_template_polls_high_words():
    config = load_dashboard_config(os.path.join(ROOT, "config_templates", "dashboard.yaml"))
    # PV1/PV2 power, load active/apparent power and grid power are 32-bit pairs
    assert {3, 4, 5, 6, 9, 10, 11, 12, 36, 37} <= config.required_ids
    assert config.get_register_meta(37)["hi_reg_id"] == 36
    # Still read in the same two Modbus requests
    assert build_optimized_chunks(config.required_ids, 40, 20) == [(0, 38), (83, 2)]


def test_list_of_ids_and_registers_section(tmp_path):
    path = write(tmp_path, "dashboard.yaml", """
widgets:
  - {type: EnergyStackWidget, solar_w_id: [4, 6], grid_w_id: 37}
registers:
  - {reg_id: 4, hi_reg_id: 3, scale: 0.1}
  - {reg_id: 50, signed: true, decimals: 2}
""")
    config = load_dashboard_config(path)
    assert config.required_ids == {3, 4, 6, 37}
    assert config.get_register_meta(50) == {"signed": True, "decimals": 2}
