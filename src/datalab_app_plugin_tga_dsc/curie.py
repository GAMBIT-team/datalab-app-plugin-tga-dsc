"""Curie temperatures from magnetic TGA measurements.

With a magnet placed near the pan, a ferromagnetic sample is pulled towards it
and the balance reads an apparent mass that includes the magnetic force. The
force vanishes at the Curie temperature, so the apparent mass steps sharply
there on heating. Whether it steps up or down depends on where the magnet is.
"""

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter

__all__ = ("find_curie_temperature", "row_at_temperature", "mentions_curie")

TEMPERATURE = "Ts"
MASS = "Weight"

SMOOTHING_WINDOW = 11
"""Savitzky-Golay window, in points, for the derivatives used to find the step.
Much shorter than the one used for DTG, which would smear out a sharp step."""

SMOOTHING_POLYORDER = 2

HEATING_FRACTION = 0.2
"""Only points heating at least this fraction of the fastest heating rate are
searched, which skips isotherms and cooling segments."""

MIN_HEATING_RATE = 0.1 / 60
"""Heating slower than this (0.1 °C/min, in °C/s) counts as isothermal."""


def find_curie_temperature(df: pd.DataFrame) -> float | None:
    """Find the Curie temperature, in °C, as the inflection point of the step.

    That is the temperature on heating at which the apparent mass changes
    fastest with temperature, refined between points with a parabola. Returns
    ``None`` if the data never heat up.
    """
    if TEMPERATURE not in df or len(df) < 3:
        return None

    t = df["t"].to_numpy(dtype=float)
    temperature = df[TEMPERATURE].to_numpy(dtype=float)
    mass = df[MASS].to_numpy(dtype=float)

    heating_rate = np.gradient(_smooth(temperature), t)
    heating = heating_rate > max(HEATING_FRACTION * np.nanmax(heating_rate), MIN_HEATING_RATE)
    if not np.any(heating):
        return None

    slope = np.full_like(mass, np.nan)
    slope[heating] = np.abs(np.gradient(_smooth(mass), t)[heating] / heating_rate[heating])
    if np.all(np.isnan(slope)):
        return None

    peak = int(np.nanargmax(slope))
    return _refine_peak(temperature, slope, peak)


def row_at_temperature(df: pd.DataFrame, temperature: float) -> dict[str, float] | None:
    """Interpolate every column where the temperature first rises past ``temperature``.

    Returns ``None`` if it never does.
    """
    if TEMPERATURE not in df:
        return None
    values = df[TEMPERATURE].to_numpy(dtype=float)
    crossings = np.flatnonzero((values[:-1] <= temperature) & (values[1:] > temperature))
    if not len(crossings):
        return None

    i = int(crossings[0])
    fraction = (temperature - values[i]) / (values[i + 1] - values[i])
    numeric = df.select_dtypes("number")
    row = numeric.iloc[i] + fraction * (numeric.iloc[i + 1] - numeric.iloc[i])
    return {str(column): float(value) for column, value in row.items()}


def mentions_curie(*texts: str | None) -> bool:
    """Whether any of ``texts`` (e.g. the sample name) mentions a Curie point."""
    return any("curie" in text.lower() for text in texts if text)


def _smooth(values: np.ndarray) -> np.ndarray:
    window = min(SMOOTHING_WINDOW, len(values))
    if window % 2 == 0:
        window -= 1
    if window <= SMOOTHING_POLYORDER:
        return values
    return savgol_filter(values, window, SMOOTHING_POLYORDER)


def _refine_peak(x: np.ndarray, y: np.ndarray, peak: int) -> float:
    """Place the peak at the vertex of a parabola through it and its neighbours."""
    if peak == 0 or peak == len(x) - 1:
        return float(x[peak])
    x3, y3 = x[peak - 1 : peak + 2], y[peak - 1 : peak + 2]
    if np.any(np.isnan(y3)) or len(np.unique(x3)) < 3:
        return float(x[peak])
    a, b, _ = np.polyfit(x3, y3, 2)
    if a >= 0:
        return float(x[peak])
    return float(np.clip(-b / (2 * a), x3[0], x3[-1]))
