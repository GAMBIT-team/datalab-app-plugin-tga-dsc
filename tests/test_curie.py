from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from datalab_app_plugin_tga_dsc.curie import (
    find_curie_temperature,
    mentions_curie,
    row_at_temperature,
)
from datalab_app_plugin_tga_dsc.parsers import parse_ta_universal_ascii

CURIE_FILE = Path(__file__).parent.parent / "example_data" / "tga" / "Fe-Curie T-August 2026.txt"


def _synthetic_run(tc: float, step: float, cool: bool = False) -> pd.DataFrame:
    """Heat at 10 °C/min from 400 to 700 °C, with a sigmoidal mass step at ``tc``."""
    t = np.arange(0.0, 1800.0, 2.0)
    temperature = 400.0 + t / 6.0
    if cool:
        # Cool back down through a second, bigger step, which should be ignored.
        t = np.concatenate([t, t[-1] + 2.0 + t])
        temperature = np.concatenate([temperature, temperature[::-1]])
    weight = 10.0 + step / (1.0 + np.exp((temperature - tc) / 2.0))
    if cool:
        weight[len(weight) // 2 :] += 5.0 / (
            1.0 + np.exp(temperature[len(weight) // 2 :] - 450.0)
        )
    return pd.DataFrame({"t": t, "Ts": temperature, "Weight": weight})


@pytest.mark.parametrize("step", [0.3, -0.3])
def test_find_curie_temperature_either_direction(step):
    assert find_curie_temperature(_synthetic_run(612.3, step)) == pytest.approx(612.3, abs=0.2)


def test_find_curie_temperature_ignores_cooling():
    assert find_curie_temperature(_synthetic_run(612.3, 0.3, cool=True)) == pytest.approx(
        612.3, abs=0.2
    )


def test_find_curie_temperature_needs_heating():
    df = pd.DataFrame({"t": [0.0, 1.0, 2.0, 3.0], "Ts": [500.0] * 4, "Weight": [1, 2, 3, 4]})
    assert find_curie_temperature(df) is None
    assert find_curie_temperature(df.drop(columns="Ts")) is None


def test_find_curie_temperature_of_iron():
    df, _ = parse_ta_universal_ascii(CURIE_FILE)
    # The literature value is 770 °C; this run reads a little high.
    assert find_curie_temperature(df) == pytest.approx(772.9, abs=0.1)


def test_row_at_temperature():
    df = pd.DataFrame({"t": [0.0, 10.0, 20.0], "Ts": [100.0, 200.0, 300.0], "Weight": [1, 2, 4]})
    row = row_at_temperature(df, 250.0)
    assert row == pytest.approx({"t": 15.0, "Ts": 250.0, "Weight": 3.0})
    assert row_at_temperature(df, 350.0) is None


def test_mentions_curie():
    assert mentions_curie(None, "Fe-Curie point-080326")
    assert not mentions_curie("JCC-01-29-7eqNaNH2", None)
