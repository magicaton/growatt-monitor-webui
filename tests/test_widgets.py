import pytest

from ui.layout import (
    BaseWidget, EnergyStackWidget, MathCard, MISSING_TEXT, RegisterCard, decode_register,
)


class FakeElement:
    # Stands in for NiceGUI labels/bars so update() can run without a page
    def __init__(self, text="..."):
        self.text = text
        self.styles = []

    def set_text(self, text):
        self.text = text

    def style(self, value):
        self.styles.append(value)

    def update(self):
        pass


def test_decode_register():
    assert decode_register({1: 2301}, 1, {"scale": 0.1}) == pytest.approx(230.1)
    assert decode_register({}, 1, {"scale": 0.1}) is None
    assert decode_register({1: 5}, None, None) is None
    # 32-bit value: 1 * 65536 + 4464 = 70000 -> 7000.0 W
    meta = {"hi_reg_id": 36, "scale": 0.1}
    assert decode_register({36: 1, 37: 4464}, 37, meta) == pytest.approx(7000.0)
    assert decode_register({37: 4464}, 37, meta) is None
    # Two's complement
    assert decode_register({5: 65535}, 5, {"signed": True}) == -1
    assert decode_register({5: 65535}, 5, {}) == 65535
    assert decode_register({4: 0xFFFF, 5: 0xFFFE}, 5, {"hi_reg_id": 4, "signed": True}) == -2


@pytest.mark.parametrize("val, unit, scale, expected", [
    # Existing behaviour without `decimals` is unchanged
    (5, "", 1, "5"),
    (87, "%", 1, "87 %"),
    (230.1, "V", 0.1, "230.1 V"),
    (49.98, "Hz", 0.01, "49.98 Hz"),
    (12.7, "A", 1.0, "12 A"),
])
def test_format_value_default(val, unit, scale, expected):
    assert BaseWidget.format_value(val, unit, scale) == expected


def test_format_value_decimals():
    assert BaseWidget.format_value(12.700000000000001, "A", 1.0, decimals=1) == "12.7 A"
    assert BaseWidget.format_value(-0.7, "A", 1.0, decimals=1) == "-0.7 A"
    assert BaseWidget.format_value(-0.04, "A", 1.0, decimals=1) == "0.0 A"
    assert BaseWidget.format_value(3.14159, "", 1.0, decimals=2) == "3.14"


def test_register_card_marks_stale_value():
    card = RegisterCard({20: {"unit": "V", "scale": 0.1}}, reg_id=20)
    card.label = FakeElement()
    card.update({})
    assert card.label.text == "..."  # no data yet
    card.update({20: 2301})
    assert card.label.text == "230.1 V"
    card.update({})
    assert card.label.text == MISSING_TEXT


def test_math_card_decimals_and_missing_inputs():
    meta = {83: {"scale": 0.1}, 84: {"scale": 0.1}}
    card = MathCard(meta, "Charge", [83, 84], lambda v: v[83] - v[84], unit="A", decimals=1)
    card.label = FakeElement()
    card.update({83: 127})
    assert card.label.text == "..."  # not computed from zeros
    card.update({83: 127, 84: 0})
    assert card.label.text == "12.7 A"
    card.update({83: 0, 84: 7})
    assert card.label.text == "-0.7 A"
    card.update({83: 0})
    assert card.label.text == MISSING_TEXT


def make_stack(**kwargs):
    meta = {
        4: {"hi_reg_id": 3, "scale": 0.1},
        6: {"hi_reg_id": 5, "scale": 0.1},
        37: {"hi_reg_id": 36, "scale": 0.1},
        17: {"scale": 0.01},
        84: {"scale": 0.1},
    }
    stack = EnergyStackWidget(meta, **kwargs)
    for key in ("sol", "grid", "batt"):
        stack.bars[key] = FakeElement()
        stack.labels[key] = FakeElement("")
    stack.labels["total"] = FakeElement("Total: 0 W")
    return stack


def test_energy_stack_sums_solar_inputs():
    stack = make_stack(solar_w_id=[4, 6], grid_w_id=37, batt_v_id=17, batt_dis_i_id=84)
    snapshot = {3: 0, 4: 10000, 5: 0, 6: 5000, 36: 0, 37: 0, 17: 5000, 84: 0}
    stack.update(snapshot)
    assert stack.labels["total"].text == "Total Load: 1500 W"
    assert stack.labels["sol"].text == "1500W"


def test_energy_stack_single_solar_id_still_works():
    stack = make_stack(solar_w_id=6, grid_w_id=37)
    stack.update({5: 0, 6: 20000, 36: 1, 37: 4464})  # 2000 W solar + 7000 W grid
    assert stack.labels["total"].text == "Total Load: 9000 W"


def test_energy_stack_blanks_on_stale_input():
    stack = make_stack(solar_w_id=[4, 6], grid_w_id=37)
    stack.update({3: 0, 4: 10000})  # no data for 6/37 yet
    assert stack.labels["total"].text == "Total: 0 W"
    stack.update({3: 0, 4: 10000, 5: 0, 6: 0, 36: 0, 37: 5000})
    assert stack.labels["total"].text == "Total Load: 1500 W"
    stack.update({3: 0, 4: 10000})
    assert stack.labels["total"].text == f"Total Load: {MISSING_TEXT}"
    assert stack.labels["sol"].text == ""
