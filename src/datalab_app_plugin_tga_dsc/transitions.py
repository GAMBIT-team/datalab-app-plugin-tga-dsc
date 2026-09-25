"""Thermal events: finding them in the data, and the record kept of each.

A transition is anything that happens at a temperature and shows up in the
mass or heat-flow signal: melting, decomposition, dehydration, a Curie point,
and so on. Transitions are found automatically on heating, and can then be
moved, deleted, added, given a kind and commented on by hand.

Two kinds of feature are detected:

- Mass steps, placed at their inflection point: the temperature at which the
  mass changes fastest. These include apparent mass steps, such as the one at a
  Curie point when a magnet is held near the pan.
- Heat-flow peaks, placed at the top of the peak.

Only the heating segments of a run are searched, and the first and last parts
of each are skipped, where the balance and heat flow are still settling.
"""

import uuid
from typing import Any, Literal

import numpy as np
import pandas as pd
import pydantic
from pydantic import BaseModel, Field
from scipy.signal import find_peaks, savgol_filter

from datalab_app_plugin_tga_dsc._pydantic import PYDANTIC_V1, dump, validate

__all__ = (
    "Transition",
    "TRANSITION_KINDS",
    "detect_transitions",
    "find_mass_steps",
    "find_heat_flow_peaks",
    "heating_mask",
    "merge_detected",
    "apply_edits",
    "row_at_temperature",
    "mentions_curie",
)

TRANSITION_KINDS = (
    "",
    "Curie",
    "melting",
    "crystallisation",
    "glass transition",
    "solid-solid phase transition",
    "decomposition",
    "dehydration",
    "desolvation",
    "oxidation",
    "reduction",
    "other",
)
"""Kinds offered in the editor; the empty string means unassigned. Any other text
is accepted too."""

CURIE = "Curie"

MIN_HEATING_RATE = 0.1 / 60
"""Heating slower than this (0.1 °C/min, in °C/s) counts as isothermal."""

HEATING_FRACTION = 0.7
"""Points heating slower than this fraction of the typical heating rate are left
out, which trims the ramps into and out of each heating segment."""

SETTLING_TIME = 60.0
"""Time (s) skipped at each end of a heating segment."""

SETTLING_FRACTION = 0.02
"""Fraction of each heating segment's temperature span skipped at each end, if
that is longer than :data:`SETTLING_TIME`."""

SMOOTHING_SPAN = 3.0
"""Temperature span (°C) over which the signals are smoothed before looking for
features. It is converted to a number of points from the typical temperature
step between points."""

MASS_STEP_THRESHOLD = 0.1
"""A mass step is a region where the rate of mass change exceeds this fraction
of its largest value on heating."""

MIN_MASS_STEP = 1.0
"""Smallest net mass change across a step (% of the initial mass) that is kept."""

PEAK_WINDOW = 30.0
"""Temperature span (°C) within which a heat-flow peak's prominence is measured,
so that a drifting baseline is not mistaken for a peak."""

MIN_PEAK_PROMINENCE = 0.1
"""Smallest heat-flow peak kept, as a fraction of the most prominent one."""

PEAK_SEPARATION = 10.0
"""Of heat-flow peaks closer than this (°C), only the most prominent is kept.
A sharp peak otherwise tends to bring a spurious one of the opposite sign
alongside, where the baseline turns into it."""

DUPLICATE_TOLERANCE = 2.0
"""Detected transitions within this many °C of one that is kept are dropped."""


class Transition(BaseModel):
    """One thermal event."""

    if PYDANTIC_V1:

        class Config:
            extra = "ignore"

    else:
        model_config = pydantic.ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    temperature: float = Field(
        description="Temperature (°C) of the inflection of a mass step, or the top of a heat-flow peak."
    )
    kind: str = Field("", description="What happens, e.g. melting; empty if unassigned.")
    comment: str = ""
    source: Literal["auto", "manual"] = Field(
        "manual", description="Whether the temperature was found automatically or set by hand."
    )
    signal: Literal["mass", "heat flow"] | None = Field(
        None, description="The signal an automatically found transition was found in."
    )
    mass_change: float | None = Field(
        None, description="Net mass change across a mass step, in % of the initial mass."
    )

    @property
    def edited(self) -> bool:
        """Whether this transition holds anything entered by hand."""
        return self.source == "manual" or bool(self.kind) or bool(self.comment)

    @property
    def label(self) -> str:
        return f"{self.kind} {self.temperature:.1f} °C".strip()

    def to_dict(self) -> dict[str, Any]:
        return dump(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Transition":
        return validate(cls, data)


def detect_transitions(df: pd.DataFrame, mentions_curie_point: bool = False) -> list[Transition]:
    """Find the mass steps and heat-flow peaks in ``df``, in temperature order.

    If ``mentions_curie_point``, the largest mass step is taken to be the Curie
    point.
    """
    steps = find_mass_steps(df)
    if mentions_curie_point and steps:
        largest = max(steps, key=lambda step: abs(step.mass_change or 0))
        largest.kind = CURIE
    transitions = steps + find_heat_flow_peaks(df)
    return sorted(transitions, key=lambda transition: transition.temperature)


def heating_mask(df: pd.DataFrame) -> np.ndarray:
    """Return which rows are searched for transitions.

    These are rows heating at close to the typical rate, less the settling time
    at each end of each heating segment.
    """
    if "Ts" not in df or len(df) < 3:
        return np.zeros(len(df), dtype=bool)

    t = df["t"].to_numpy(dtype=float)
    temperature = df["Ts"].to_numpy(dtype=float)
    rate = np.gradient(_smooth(temperature, _odd(len(temperature) // 20)), t)
    heating = rate > MIN_HEATING_RATE
    if not heating.any():
        return heating
    heating &= rate > HEATING_FRACTION * np.percentile(rate[heating], 90)

    # Trim the ends of each contiguous heating segment.
    for segment in _segments(heating):
        start, stop = segment.start, segment.stop
        span = temperature[stop - 1] - temperature[start]
        settled = (t[segment] - t[start] >= SETTLING_TIME) & (
            t[stop - 1] - t[segment] >= SETTLING_TIME
        )
        settled &= (temperature[segment] - temperature[start] >= SETTLING_FRACTION * span) & (
            temperature[stop - 1] - temperature[segment] >= SETTLING_FRACTION * span
        )
        heating[segment] = settled
    return heating


def find_mass_steps(df: pd.DataFrame) -> list[Transition]:
    """Find steps in the (apparent) mass on heating.

    A step is a contiguous region in which the mass changes faster than
    :data:`MASS_STEP_THRESHOLD` of the fastest change, and whose net mass change
    is at least :data:`MIN_MASS_STEP`. It is placed at its fastest point.
    """
    heating = heating_mask(df)
    if not heating.any():
        return []

    t = df["t"].to_numpy(dtype=float)
    temperature = df["Ts"].to_numpy(dtype=float)
    mass = 100.0 * df["Weight"].to_numpy(dtype=float) / float(df["Weight"].iloc[0])
    window = _window(temperature, heating)
    rate = np.gradient(_smooth(temperature, window), t)

    slope = np.zeros_like(mass)
    slope[heating] = np.abs(np.gradient(_smooth(mass, window), t)[heating] / rate[heating])
    fast = slope > MASS_STEP_THRESHOLD * slope.max()

    steps = []
    for region in _segments(fast):
        start, stop = region.start, region.stop
        mass_change = mass[stop - 1] - mass[start]
        if abs(mass_change) < MIN_MASS_STEP:
            continue
        peak = start + int(np.argmax(slope[start:stop]))
        steps.append(
            Transition(
                temperature=round(_refine_peak(temperature, slope, peak), 2),
                source="auto",
                signal="mass",
                mass_change=round(float(mass_change), 3),
            )
        )
    return steps


def find_heat_flow_peaks(df: pd.DataFrame) -> list[Transition]:
    """Find peaks in the heat flow on heating, pointing either way.

    Prominence is measured within :data:`PEAK_WINDOW`. Peaks less than
    :data:`MIN_PEAK_PROMINENCE` of the most prominent are dropped, as are peaks
    within :data:`PEAK_SEPARATION` of a more prominent one.
    """
    if "HF" not in df:
        return []
    heating = heating_mask(df)
    if not heating.any():
        return []

    temperature = df["Ts"].to_numpy(dtype=float)
    step = np.median(np.abs(np.diff(temperature[heating])))
    heat_flow = _smooth(df["HF"].to_numpy(dtype=float), _window(temperature, heating))
    window = _odd(PEAK_WINDOW / step) if step > 0 else None

    candidates = []
    for segment in _segments(heating):
        for sign in (1.0, -1.0):
            peaks, properties = find_peaks(sign * heat_flow[segment], prominence=0, wlen=window)
            for peak, prominence in zip(peaks, properties["prominences"]):
                candidates.append((segment.start + peak, prominence, sign))

    if not candidates:
        return []
    largest = max(prominence for _, prominence, _ in candidates)
    kept: list[tuple[int, float, float]] = []
    for peak, prominence, sign in sorted(candidates, key=lambda candidate: -candidate[1]):
        if prominence < MIN_PEAK_PROMINENCE * largest:
            break
        if all(
            abs(temperature[peak] - temperature[other]) >= PEAK_SEPARATION for other, _, _ in kept
        ):
            kept.append((peak, prominence, sign))

    return [
        Transition(
            temperature=round(_refine_peak(temperature, sign * heat_flow, peak), 2),
            source="auto",
            signal="heat flow",
        )
        for peak, _, sign in kept
    ]


def merge_detected(existing: list[Transition], detected: list[Transition]) -> list[Transition]:
    """Replace the automatically found transitions in ``existing`` with ``detected``.

    Transitions holding anything entered by hand are kept, and detected ones that
    duplicate them are dropped.
    """
    kept = [transition for transition in existing if transition.edited]
    new = [
        transition
        for transition in detected
        if all(
            abs(transition.temperature - other.temperature) > DUPLICATE_TOLERANCE
            or transition.signal != other.signal
            for other in kept
        )
    ]
    return sorted(kept + new, key=lambda transition: transition.temperature)


def apply_edits(existing: list[Transition], edits: list[dict[str, Any]]) -> list[Transition]:
    """Apply a list of transitions edited by hand to ``existing``.

    ``edits`` is the whole new list: each entry gives a ``temperature``,
    ``kind`` and ``comment``, and the ``id`` of the transition it edits, if any.
    Entries without a known ``id`` are new; existing transitions left out are
    deleted. A transition moved by hand counts as set by hand from then on.
    """
    by_id = {transition.id: transition for transition in existing}
    edited = []
    for edit in edits:
        fields = {key: edit[key] for key in ("temperature", "kind", "comment") if key in edit}
        if isinstance(fields.get("kind"), str):
            fields["kind"] = fields["kind"].strip()
        original = by_id.get(edit.get("id"))  # type: ignore[arg-type]
        if original is None:
            transition = Transition.from_dict({**fields, "source": "manual"})
        else:
            data = {**original.to_dict(), **fields}
            if abs(float(data["temperature"]) - original.temperature) > 1e-6:
                data["source"] = "manual"
                data.pop("mass_change", None)
            transition = Transition.from_dict(data)
        if not np.isfinite(transition.temperature):
            raise ValueError(f"Transition temperature must be finite, not {transition.temperature}")
        edited.append(transition)
    return sorted(edited, key=lambda transition: transition.temperature)


def row_at_temperature(df: pd.DataFrame, temperature: float) -> dict[str, float] | None:
    """Interpolate every column where the temperature first rises past ``temperature``.

    Returns ``None`` if it never does.
    """
    if "Ts" not in df:
        return None
    values = df["Ts"].to_numpy(dtype=float)
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


def _segments(mask: np.ndarray) -> list[slice]:
    """The contiguous runs of ``True`` in ``mask``."""
    edges = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(int), [0]])))
    return [slice(int(start), int(stop)) for start, stop in zip(edges[::2], edges[1::2])]


def _odd(n: float) -> int:
    n = max(int(n), 1)
    return n if n % 2 else n + 1


def _window(temperature: np.ndarray, heating: np.ndarray) -> int:
    """The smoothing window, in points, spanning :data:`SMOOTHING_SPAN`."""
    step = np.median(np.abs(np.diff(temperature[heating]))) if heating.sum() > 1 else 0
    return _odd(max(5, SMOOTHING_SPAN / step)) if step > 0 else 5


def _smooth(values: np.ndarray, window: int, polyorder: int = 2) -> np.ndarray:
    window = min(window, len(values) if len(values) % 2 else len(values) - 1)
    if window <= polyorder:
        return values
    return savgol_filter(values, window, polyorder)


def _refine_peak(x: np.ndarray, y: np.ndarray, peak: int) -> float:
    """Place the peak at the vertex of a parabola through it and its neighbours."""
    if peak == 0 or peak == len(x) - 1:
        return float(x[peak])
    x3, y3 = x[peak - 1 : peak + 2], y[peak - 1 : peak + 2]
    if not np.all(np.isfinite(y3)) or len(np.unique(x3)) < 3:
        return float(x[peak])
    a, b, _ = np.polyfit(x3, y3, 2)
    if a >= 0:
        return float(x[peak])
    return float(np.clip(-b / (2 * a), x3.min(), x3.max()))
