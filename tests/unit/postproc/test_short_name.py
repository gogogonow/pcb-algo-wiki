"""Tests for src.postproc.short_name.short_id."""
from src.postproc.short_name import short_id


def test_universal_node_compressed():
    assert short_id("IC1_pin1_seg1_universal_node") == "p1s1_u"


def test_split_pad_compressed():
    assert short_id("IC1_pin2_seg2_end_split_pad") == "p2s2_sp"


def test_start_combiner_compressed():
    assert short_id("IC1_pin1_seg5_start_combiner") == "p1s5_sc"


def test_pin_compressed():
    assert short_id("C1.PIN_1") == "C1.1"


def test_to_component_compressed():
    assert short_id("IC1_pin1_seg2_to_C7") == "p1s2->C7"


def test_plain_pass_through():
    assert short_id("GND") == "GND"
    assert short_id("RF_INPUT") == "RF_INPUT"


def test_generic_component_pin_prefix():
    # Generic: any "<COMP>_pin<N>_" works (not hard-coded to IC1)
    assert short_id("U2_pin3_seg1") == "p3s1"
    assert short_id("Q1_pin1_seg4_end_split_pad") == "p1s4_sp"


def test_idempotent_on_short_ids():
    assert short_id("p1s1_u") == "p1s1_u"
    assert short_id("C1.1") == "C1.1"
