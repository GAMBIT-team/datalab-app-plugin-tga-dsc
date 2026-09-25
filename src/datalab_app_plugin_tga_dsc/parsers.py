"""Parsers for thermal analysis exports.

Every parser returns the data as a DataFrame of float columns alongside a
:class:`ThermalAnalysisMetadata`. Columns are renamed to a shared set of names so
that the rest of the plugin need not know which instrument a file came from:

- ``t``: elapsed time (s)
- ``Ts``: sample temperature (°C)
- ``Tr``: reference or programme temperature (°C)
- ``HF``: heat flow (mW)
- ``Weight``: balance signal (mg)
- ``dW/dT``: the instrument's own derivative of weight (%/°C), negative for a loss

A file need not have all of them, but it must at least have ``t`` and ``Weight``.
"""

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from datalab_app_plugin_tga_dsc.metadata import ThermalAnalysisMetadata

__all__ = (
    "parse_thermal_file",
    "parse_sta_ascii",
    "parse_ta_universal_ascii",
    "detect_format",
    "STA_COLUMNS",
)

ENCODING = "latin-1"
"""Exports are read as latin-1 to support the degree sign the instruments write."""

TA_ENCODING = "cp437"
"""Universal Analysis writes the DOS code page, in which the degree sign is 0xF8."""

REQUIRED_COLUMNS = ("t", "Weight")

STA_COLUMNS = ("Index", "Ts", "t", "HF", "Weight", "Tr")
"""Columns of the simultaneous TGA/DSC export: index, sample temperature, elapsed
time, heat flow, balance signal, and reference temperature."""

TA_SIGNALS = {
    "Time (min)": ("t", 60.0),
    "Time (s)": ("t", 1.0),
    "Temperature (°C)": ("Ts", 1.0),
    "Weight (mg)": ("Weight", 1.0),
    # Universal Analysis counts weight loss as positive; flip it to match our DTG.
    "Deriv. Weight (%/°C)": ("dW/dT", -1.0),
    "Heat Flow (mW)": ("HF", 1.0),
}
"""Universal Analysis signal names, mapped to a shared column name and the factor
that converts them to its units. Other signals are kept under their own name."""

_TA_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_TA_XCOMMENT_FIELD = re.compile(r"(\w+):\s*(.*?)\s*(?=\s\w+:|$)")


def detect_format(path: Path | str) -> str:
    """Name the format of an export by looking at its first few lines."""
    with open(path, encoding=ENCODING) as f:
        head = [line.strip() for line, _ in zip(f, range(40))]

    if any(line.startswith("Nsig\t") for line in head):
        return "ta_universal_ascii"
    if head and head[0].split()[:2] == ["Index", "Ts"]:
        return "sta_ascii"
    raise RuntimeError(f"Could not recognise the format of {Path(path).name!r}")


def parse_thermal_file(path: Path | str) -> tuple[pd.DataFrame, ThermalAnalysisMetadata]:
    """Parse an export in any supported format."""
    path = _check_path(path)
    parser = {
        "sta_ascii": parse_sta_ascii,
        "ta_universal_ascii": parse_ta_universal_ascii,
    }[detect_format(path)]
    return parser(path)


def parse_sta_ascii(path: Path | str) -> tuple[pd.DataFrame, ThermalAnalysisMetadata]:
    """Parse an ASCII export from a simultaneous thermal analyser.

    Files contain two header rows (column names, then units), whitespace-aligned
    numeric data, and an optional footer holding the sample name and export
    timestamp.
    """
    path = _check_path(path)

    df = pd.read_csv(
        path,
        sep=r"\s+",
        skiprows=2,
        header=None,
        names=STA_COLUMNS,
        encoding=ENCODING,
    )

    numeric = pd.to_numeric(df["Index"], errors="coerce")
    footer = df[numeric.isna()]
    df = df[numeric.notna()].astype(float).reset_index(drop=True)
    _check_data(df, path)

    with open(path, encoding=ENCODING) as f:
        names, units = f.readline().split(), f.readline().split()

    metadata = ThermalAnalysisMetadata(
        file_format="sta_ascii",
        original_filename=path.name,
        signals=[f"{name} {unit}" for name, unit in zip(names, units)],
    )
    if not footer.empty:
        fields = [str(value) for value in footer.iloc[0].tolist() if not pd.isna(value)]
        name, _, timestamp = " ".join(fields).partition(",")
        if name.strip():
            metadata.sample_name = name.strip()
        if timestamp.strip():
            # Left as written: the day and month order is not stated in the file.
            metadata.export_timestamp = timestamp.strip()

    return df, metadata


def parse_ta_universal_ascii(path: Path | str) -> tuple[pd.DataFrame, ThermalAnalysisMetadata]:
    """Parse a TA Instruments export written by Universal Analysis.

    Files start with a line giving the run status, then a header of tab-separated
    ``Key value`` lines. ``Nsig`` gives the number of signals, which are named in
    order by ``Sig1``, ``Sig2``, and so on. Whitespace-separated numeric data
    follows the header.
    """
    path = _check_path(path)

    with open(path, encoding=TA_ENCODING) as f:
        lines = f.read().splitlines()

    header: dict[str, str] = {}
    status = None
    data_start = None
    for i, line in enumerate(lines):
        if _is_numeric_row(line):
            data_start = i
            break
        key, _, value = line.partition("\t")
        key, value = key.strip(), " ".join(value.split())
        if i == 0 and not value:
            status = key
        elif _TA_KEY.match(key):
            header[key] = value

    if data_start is None:
        raise RuntimeError(f"Found no numeric data rows in {path!r}")

    try:
        signals = [header[f"Sig{n}"] for n in range(1, int(header["Nsig"]) + 1)]
    except (KeyError, ValueError):
        raise RuntimeError(f"Could not read the signal names from the header of {path!r}")

    # Read by hand rather than with `pd.read_csv`, which trips over the form feed
    # that Universal Analysis writes before the first row.
    rows = [line.split() for line in lines[data_start:] if line.strip()]
    if any(len(row) != len(signals) for row in rows):
        raise RuntimeError(f"Expected {len(signals)} columns in every data row of {path!r}")
    df = pd.DataFrame(rows, columns=signals).astype(float)
    df = _rename_ta_signals(df)
    _check_data(df, path)

    metadata = ThermalAnalysisMetadata(
        file_format="ta_universal_ascii",
        original_filename=path.name,
        sample_name=header.pop("Sample", None) or None,
        instrument=header.pop("Module", None) or None,
        method=header.pop("Method", None) or None,
        operator=header.pop("Operator", None) or None,
        comment=header.pop("Comment", None) or None,
        sample_mass_mg=_ta_mass_mg(header.pop("Size", "")),
        measured_at=_ta_datetime(header.pop("Date", ""), header.pop("Time", "")),
        signals=signals,
    )
    for key in [key for key in header if re.fullmatch(r"Sig\d+|Nsig", key)]:
        del header[key]
    if status:
        metadata.status = status
    for key, value in _ta_xcomment_fields(header.get("Xcomment", "")).items():
        setattr(metadata, key, value)
    for key, value in header.items():
        if value:
            setattr(metadata, _snake_case(key), value)

    return df, metadata


def _check_path(path: Path | str) -> Path:
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"Provided path does not exist: {path!r}")
    return path


def _check_data(df: pd.DataFrame, path: Path) -> None:
    if df.empty:
        raise RuntimeError(f"Found no numeric data rows in {path!r}")
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise RuntimeError(f"Could not find the {missing} columns in {path!r}")


def _is_numeric_row(line: str) -> bool:
    fields = line.split()
    if not fields:
        return False
    try:
        [float(field) for field in fields]
    except ValueError:
        return False
    return True


def _rename_ta_signals(df: pd.DataFrame) -> pd.DataFrame:
    renamed = {}
    for signal in df.columns:
        column, factor = TA_SIGNALS.get(signal, (signal, 1.0))
        if column in renamed:
            # A second signal for the same quantity, e.g. time in both units.
            column, factor = signal, 1.0
        renamed[column] = df[signal] * factor
    return pd.DataFrame(renamed)


def _ta_mass_mg(size: str) -> float | None:
    """Read the ``Size`` field, e.g. ``15.4850\tmg``."""
    fields = size.split()
    if len(fields) == 2 and fields[1] == "mg":
        try:
            return float(fields[0])
        except ValueError:
            pass
    return None


def _ta_datetime(date: str, time: str) -> datetime | None:
    """Read the ``Date`` and ``Time`` fields, e.g. ``3-Aug-26`` and ``17:12``."""
    try:
        return datetime.strptime(f"{date} {time}", "%d-%b-%y %H:%M")
    except ValueError:
        return None


def _ta_xcomment_fields(xcomment: str) -> dict[str, str]:
    """Split the ``Xcomment`` field into its ``Name: value`` parts.

    It holds the pan and purge gases, e.g. ``Pan: Alumina  Gas1: Argon  100mL/min``.
    """
    return {
        _snake_case(name): " ".join(value.split())
        for name, value in _TA_XCOMMENT_FIELD.findall(xcomment)
        if value.strip()
    }


def _snake_case(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()
