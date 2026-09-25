"""Derived quantities and plotting options for thermal analysis data."""

from collections.abc import Sequence

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter

__all__ = (
    "add_derived_columns",
    "available_options",
    "X_OPTIONS",
    "MASS_Y_OPTIONS",
    "HEAT_Y_OPTIONS",
)

X_OPTIONS = ("t (s)", "t (min)", "t (h)", "Ts (°C)", "Tr (°C)")
MASS_Y_OPTIONS = ("mass (%)", "Δmass (%)", "mass (mg)", "DTG (%/min)", "DTG (%/°C)")
HEAT_Y_OPTIONS = ("heat flow (mW)", "heat flow (mW/mg)")
"""Every column that may be plotted. Which of them a file provides depends on
the signals it recorded; see :func:`available_options`."""

DTG_WINDOW = 101
"""Savitzky-Golay window used to smooth the mass signal before differentiating."""

DTG_POLYORDER = 3


def add_derived_columns(df: pd.DataFrame, m0: float) -> pd.DataFrame:
    """Add time, mass, DTG, and heat-flow columns derived from instrument data.

    Columns are only added for the signals that ``df`` has, beyond the time and
    weight that every file provides.
    """
    if not np.isfinite(m0) or m0 == 0:
        raise ValueError(f"Initial mass `m0` must be finite and non-zero, not {m0!r}.")

    df = df.copy()

    t_min = df["t"] / 60.0
    df["t (s)"] = df["t"]
    df["t (min)"] = t_min
    df["t (h)"] = df["t"] / 3600.0

    df["mass (mg)"] = df["Weight"]
    df["mass (%)"] = 100.0 * df["Weight"] / m0
    df["Δmass (%)"] = 100.0 * (df["Weight"] - m0) / m0
    df["DTG (%/min)"] = np.gradient(_smooth(df["mass (%)"].to_numpy()), t_min.to_numpy())

    # Signals that only some instruments record.
    if "Ts" in df:
        df["Ts (°C)"] = df["Ts"]
    if "Tr" in df:
        df["Tr (°C)"] = df["Tr"]
    if "dW/dT" in df:
        df["DTG (%/°C)"] = df["dW/dT"]
    if "HF" in df:
        df["heat flow (mW)"] = df["HF"]
        df["heat flow (mW/mg)"] = df["HF"] / m0

    return df


def available_options(df: pd.DataFrame, options: Sequence[str]) -> tuple[str, ...]:
    """Return those of ``options`` that ``df`` has a column for."""
    return tuple(option for option in options if option in df.columns)


def _smooth(values: np.ndarray) -> np.ndarray:
    """Apply the DTG filter, shrinking its window for short traces."""
    window = min(DTG_WINDOW, len(values))
    if window % 2 == 0:
        window -= 1
    if window <= DTG_POLYORDER:
        return values
    return savgol_filter(values, window, DTG_POLYORDER)
