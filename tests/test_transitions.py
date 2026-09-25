from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from datalab_app_plugin_tga_dsc.parsers import parse_thermal_file
from datalab_app_plugin_tga_dsc.transitions import (
    CURIE,
    Transition,
    apply_edits,
    detect_transitions,
    find_heat_flow_peaks,
    find_mass_steps,
    heating_mask,
    mentions_curie,
    merge_detected,
    row_at_temperature,
)

EXAMPLE_DIR = Path(__file__).parent.parent / "example_data" / "tga"
CURIE_FILE = EXAMPLE_DIR / "Fe-Curie T-August 2026.txt"
INDIUM_FILE = EXAMPLE_DIR / "Calib TGA_DSC Standard In.txt"
STA_FILE = EXAMPLE_DIR / "JCC-01-29-7eqNaNH2.txt"


def _ramp(tc: float, step: float, cool: bool = False, peak: float | None = None) -> pd.DataFrame:
    """Heat at 10 °C/min from 400 to 700 °C with a sigmoidal mass step at ``tc``,
    and optionally a heat-flow peak at ``peak`` on a sloping baseline."""
    t = np.arange(0.0, 1800.0, 2.0)
    temperature = 400.0 + t / 6.0
    if cool:
        t = np.concatenate([t, t[-1] + 2.0 + t])
        temperature = np.concatenate([temperature, temperature[::-1]])
    weight = 10.0 + step / (1.0 + np.exp((temperature - tc) / 2.0))
    heat_flow = 0.01 * temperature
    if peak is not None:
        heat_flow -= 3.0 * np.exp(-(((temperature - peak) / 2.0) ** 2))
    if cool:
        # A bigger step and peak on cooling, which should both be ignored.
        cooling = slice(len(t) // 2, None)
        weight[cooling] += 5.0 / (1.0 + np.exp(temperature[cooling] - 450.0))
        heat_flow[cooling] += 10.0 * np.exp(-(((temperature[cooling] - 500.0) / 2.0) ** 2))
    return pd.DataFrame({"t": t, "Ts": temperature, "Weight": weight, "HF": heat_flow})


@pytest.mark.parametrize("step", [0.3, -0.3])
def test_find_mass_steps_either_direction(step):
    (found,) = find_mass_steps(_ramp(612.3, step))
    assert found.temperature == pytest.approx(612.3, abs=0.2)
    assert found.signal == "mass"
    assert found.source == "auto"
    # The step is measured across its fast part only, so it reads a little short.
    m0 = 10.0 + step
    assert found.mass_change == pytest.approx(-100 * step / m0, rel=0.1)


def test_detection_ignores_cooling():
    transitions = detect_transitions(_ramp(612.3, 0.3, cool=True, peak=550.0))
    assert [(t.signal, round(t.temperature)) for t in transitions] == [
        ("heat flow", 550),
        ("mass", 612),
    ]


def test_detection_ignores_small_steps():
    assert find_mass_steps(_ramp(612.3, 0.05)) == []


def test_detection_needs_heating():
    df = pd.DataFrame({"t": np.arange(10.0), "Ts": [500.0] * 10, "Weight": np.arange(10.0)})
    assert not heating_mask(df).any()
    assert detect_transitions(df) == []
    assert detect_transitions(df.drop(columns="Ts")) == []


def test_heating_mask_skips_settling():
    mask = heating_mask(_ramp(612.3, 0.3))
    t = _ramp(612.3, 0.3)["t"]
    assert not mask[t < 60].any()
    assert not mask[t > t.iloc[-1] - 60].any()
    assert mask[(t > 120) & (t < 1600)].all()


def test_detects_curie_point_of_iron():
    df, metadata = parse_thermal_file(CURIE_FILE)
    (curie,) = detect_transitions(df, mentions_curie(metadata.sample_name))
    # The literature value is 770 °C; this run reads a little high.
    assert curie.temperature == pytest.approx(772.7, abs=0.1)
    assert curie.kind == CURIE
    assert curie.mass_change == pytest.approx(-2.8, abs=0.1)


def test_detects_melting_of_indium():
    df, _ = parse_thermal_file(INDIUM_FILE)
    # The start-up transient must not count, nor the baseline turning into the peak.
    assert find_mass_steps(df) == []
    (melting,) = find_heat_flow_peaks(df)
    # The peak sits a little above the 156.6 °C onset.
    assert melting.temperature == pytest.approx(158.2, abs=0.2)


def test_detects_decomposition_steps():
    df, _ = parse_thermal_file(STA_FILE)
    steps = find_mass_steps(df)
    assert [round(step.temperature) for step in steps] == [155, 188, 262, 275, 920]
    assert steps[-1].mass_change == pytest.approx(-78.2, abs=0.1)
    assert all(t.kind == "" for t in steps)


def test_merge_detected_keeps_edits():
    untouched = Transition(temperature=100.0, source="auto", signal="mass")
    commented = Transition(temperature=200.0, source="auto", signal="mass", comment="odd")
    manual = Transition(temperature=300.0)
    detected = [
        Transition(temperature=110.0, source="auto", signal="mass"),
        Transition(temperature=201.0, source="auto", signal="mass"),
        Transition(temperature=201.0, source="auto", signal="heat flow"),
    ]

    merged = merge_detected([untouched, commented, manual], detected)
    assert [(t.temperature, t.signal) for t in merged] == [
        (110.0, "mass"),
        (200.0, "mass"),
        (201.0, "heat flow"),
        (300.0, None),
    ]


def test_apply_edits():
    step = Transition(temperature=100.0, source="auto", signal="mass", mass_change=-5.0)
    peak = Transition(temperature=200.0, source="auto", signal="heat flow")
    doomed = Transition(temperature=300.0, source="auto", signal="mass")

    edited = apply_edits(
        [step, peak, doomed],
        [
            {"id": step.id, "temperature": 100.0, "kind": " melting ", "comment": "sharp"},
            {"id": peak.id, "temperature": "150.5", "kind": "", "comment": ""},
            {"temperature": 50.0, "kind": "dehydration", "comment": "added"},
        ],
    )

    assert [t.temperature for t in edited] == [50.0, 100.0, 150.5]
    added, kept, moved = edited
    assert added.source == "manual" and added.kind == "dehydration" and added.id
    assert kept.id == step.id and kept.kind == "melting" and kept.comment == "sharp"
    assert kept.source == "auto" and kept.mass_change == -5.0
    # Moving a transition by hand makes it manual.
    assert moved.id == peak.id and moved.source == "manual" and moved.signal == "heat flow"


def test_apply_edits_rejects_bad_temperatures():
    with pytest.raises(ValueError):
        apply_edits([], [{"temperature": "hot"}])
    with pytest.raises(ValueError):
        apply_edits([], [{"temperature": float("nan")}])


def test_transition_round_trip():
    transition = Transition(temperature=772.67, kind=CURIE, source="auto", signal="mass")
    assert Transition.from_dict(transition.to_dict()) == transition
    assert "mass_change" not in transition.to_dict()
    assert transition.label == "Curie 772.7 °C"
    assert Transition(temperature=10.0).label == "10.0 °C"


def test_row_at_temperature():
    df = pd.DataFrame({"t": [0.0, 10.0, 20.0], "Ts": [100.0, 200.0, 300.0], "Weight": [1, 2, 4]})
    assert row_at_temperature(df, 250.0) == pytest.approx({"t": 15.0, "Ts": 250.0, "Weight": 3.0})
    assert row_at_temperature(df, 350.0) is None


def test_mentions_curie():
    assert mentions_curie(None, "Fe-Curie point-080326")
    assert not mentions_curie("JCC-01-29-7eqNaNH2", None)
