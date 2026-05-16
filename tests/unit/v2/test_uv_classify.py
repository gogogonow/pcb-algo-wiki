from solver.v2.uv_classify import UvKind, classify_uv


def _stub(pin_nets):
    class _U:
        pass

    u = _U()
    u.pin_nets = pin_nets
    return u


def test_two_signal_nets_is_series():
    assert (
        classify_uv(_stub({"PIN_1": "RF_NET_1", "PIN_2": "RF_NET_3"})) == UvKind.SERIES
    )


def test_one_gnd_is_shunt():
    assert classify_uv(_stub({"PIN_1": "RF_NET_1", "PIN_2": "GND"})) == UvKind.SHUNT


def test_one_pin_is_probe():
    assert classify_uv(_stub({"PIN_1": "RF_NET_1"})) == UvKind.PROBE


def test_power_rail_is_shunt():
    assert classify_uv(_stub({"PIN_1": "RF_NET_1", "PIN_2": "VCC"})) == UvKind.SHUNT


def test_vdd_prefix_is_shunt():
    assert classify_uv(_stub({"PIN_1": "RF_NET_1", "PIN_2": "VDD_3V3"})) == UvKind.SHUNT


def test_empty_pin_nets_is_probe():
    assert classify_uv(_stub({})) == UvKind.PROBE
