"""Parsing and derived quantities for simultaneous thermal analysis data."""

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter

__all__ = (
    "parse_sta_ascii",
    "add_derived_columns",
    "RAW_COLUMNS",
    "X_OPTIONS",
    "MASS_Y_OPTIONS",
    "HEAT_Y_OPTIONS",
)

RAW_COLUMNS = ("Index", "Ts", "t", "HF", "Weight", "Tr")
"""Instrument columns: index, sample temperature, elapsed time, heat flow,
balance signal, and reference temperature."""

X_OPTIONS = ("t (s)", "t (min)", "t (h)", "Ts (°C)", "Tr (°C)")
MASS_Y_OPTIONS = ("mass (%)", "Δmass (%)", "mass (mg)", "DTG (%/min)")
HEAT_Y_OPTIONS = ("heat flow (mW)", "heat flow (mW/mg)")

DTG_WINDOW = 101
"""Savitzky-Golay window used to smooth the mass signal before differentiating."""

DTG_POLYORDER = 3


def parse_sta_ascii(path: Path | str) -> pd.DataFrame:
    """Parse an ASCII export from a simultaneous thermal analyser.

    Files contain two header rows, whitespace-aligned numeric data, and an
    optional footer holding the sample name and export timestamp. They are read
    as latin-1 to support the degree sign used by the instrument.
    """
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"Provided path does not exist: {path!r}")

    df = pd.read_csv(
        path,
        sep=r"\s+",
        skiprows=2,
        header=None,
        names=RAW_COLUMNS,
        encoding="latin-1",
    )

    numeric = pd.to_numeric(df["Index"], errors="coerce")
    footer = df[numeric.isna()]
    df = df[numeric.notna()].astype(float).reset_index(drop=True)

    if df.empty:
        raise RuntimeError(f"Found no numeric data rows in {path!r}")

    df.attrs["original_filename"] = path.name
    if not footer.empty:
        fields = [str(value) for value in footer.iloc[0].tolist() if not pd.isna(value)]
        name, _, timestamp = " ".join(fields).partition(",")
        if name.strip():
            df.attrs["sample_name"] = name.strip()
        if timestamp.strip():
            df.attrs["export_timestamp"] = timestamp.strip()

    return df


def add_derived_columns(df: pd.DataFrame, m0: float) -> pd.DataFrame:
    """Add time, mass, DTG, and heat-flow columns derived from instrument data."""
    if not np.isfinite(m0) or m0 == 0:
        raise ValueError(f"Initial mass `m0` must be finite and non-zero, not {m0!r}.")

    df = df.copy()

    t_min = df["t"] / 60.0
    df["t (s)"] = df["t"]
    df["t (min)"] = t_min
    df["t (h)"] = df["t"] / 3600.0
    df["Ts (°C)"] = df["Ts"]
    df["Tr (°C)"] = df["Tr"]

    df["mass (mg)"] = df["Weight"]
    df["mass (%)"] = 100.0 * df["Weight"] / m0
    df["Δmass (%)"] = 100.0 * (df["Weight"] - m0) / m0
    df["DTG (%/min)"] = np.gradient(_smooth(df["mass (%)"].to_numpy()), t_min.to_numpy())

    df["heat flow (mW)"] = df["HF"]
    df["heat flow (mW/mg)"] = df["HF"] / m0

    return df


def _smooth(values: np.ndarray) -> np.ndarray:
    """Apply the DTG filter, shrinking its window for short traces."""
    window = min(DTG_WINDOW, len(values))
    if window % 2 == 0:
        window -= 1
    if window <= DTG_POLYORDER:
        return values
    return savgol_filter(values, window, DTG_POLYORDER)
