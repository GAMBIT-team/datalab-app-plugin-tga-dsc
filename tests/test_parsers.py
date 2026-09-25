from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from datalab_app_plugin_tga_dsc.parsers import (
    STA_COLUMNS,
    detect_format,
    parse_sta_ascii,
    parse_ta_universal_ascii,
    parse_thermal_file,
)

EXAMPLE_DIR = Path(__file__).parent.parent / "example_data" / "tga"
STA_FILE = EXAMPLE_DIR / "JCC-01-29-7eqNaNH2.txt"
TA_FILE = EXAMPLE_DIR / "Fe-Curie T-August 2026.txt"


def test_detect_format():
    assert detect_format(STA_FILE) == "sta_ascii"
    assert detect_format(TA_FILE) == "ta_universal_ascii"


def test_detect_format_rejects_unknown(tmp_path):
    other = tmp_path / "other.txt"
    other.write_text("some\nother\nfile\n")
    with pytest.raises(RuntimeError, match="Could not recognise"):
        detect_format(other)


def test_parse_thermal_file_dispatches():
    _, sta = parse_thermal_file(STA_FILE)
    _, ta = parse_thermal_file(TA_FILE)
    assert sta.file_format == "sta_ascii"
    assert ta.file_format == "ta_universal_ascii"


def test_parse_missing_file():
    with pytest.raises(RuntimeError, match="does not exist"):
        parse_thermal_file(EXAMPLE_DIR / "no-such-file.txt")


def test_parse_sta_ascii():
    df, _ = parse_sta_ascii(STA_FILE)

    assert list(df.columns) == list(STA_COLUMNS)
    assert len(df) == 23701
    assert not df.isnull().values.any()
    assert all(pd.api.types.is_float_dtype(dtype) for dtype in df.dtypes)

    assert df["Weight"].iloc[0] == pytest.approx(1.60001)
    assert df["Weight"].iloc[-1] == pytest.approx(-0.509667)
    assert df["t"].iloc[-1] == pytest.approx(23700.0)
    assert df["Tr"].max() == pytest.approx(1000.0)


def test_parse_sta_ascii_metadata():
    _, metadata = parse_sta_ascii(STA_FILE)

    assert metadata.sample_name == "JCC-01-29-7eqNaNH2"
    assert metadata.export_timestamp == "09.06.2026 20:19:20"
    assert metadata.original_filename == "JCC-01-29-7eqNaNH2.txt"
    assert metadata.signals[1] == "Ts [°C]"


def test_parse_ta_universal_ascii():
    df, _ = parse_ta_universal_ascii(TA_FILE)

    assert list(df.columns) == ["t", "Ts", "Weight", "dW/dT"]
    # The first row follows a form feed, and must not be lost.
    assert len(df) == 373
    assert df["t"].iloc[0] == pytest.approx(0.0)
    assert df["Ts"].iloc[0] == pytest.approx(599.780)
    assert df["t"].iloc[-1] == pytest.approx(12.4588 * 60)
    assert df["Weight"].iloc[-1] == pytest.approx(15.5839)
    assert df["dW/dT"].iloc[0] == pytest.approx(-6.402e-3)


def test_parse_ta_universal_ascii_metadata():
    _, metadata = parse_ta_universal_ascii(TA_FILE)

    assert metadata.sample_name == "Fe-Curie point-080326"
    assert metadata.instrument == "TGA 1000 °C"
    assert metadata.operator == "TM"
    assert metadata.comment is None
    assert metadata.sample_mass_mg == pytest.approx(15.485)
    assert metadata.measured_at == datetime(2026, 8, 3, 17, 12)
    assert metadata.signals == [
        "Time (min)",
        "Temperature (°C)",
        "Weight (mg)",
        "Deriv. Weight (%/°C)",
    ]

    # Everything else in the header is kept as extra fields.
    extras = metadata.model_dump()
    assert extras["status"] == "CLOSED"
    assert extras["pan"] == "Alumina"
    assert extras["gas1"] == "Argon 100mL/min"
    assert extras["controls"] == "Gas 1 Event Off Sampling 2.0 sec/pt"
    assert extras["org_file"].endswith("Fe-Curie point-080326.001")
    assert "sig1" not in extras and "nsig" not in extras


def test_parse_ta_universal_ascii_requires_weight(tmp_path):
    no_weight = tmp_path / "no-weight.txt"
    no_weight.write_text(
        "CLOSED\nNsig\t2\nSig1\tTime (min)\nSig2\tTemperature (°C)\n0.0 25.0\n1.0 26.0\n",
        encoding="cp437",
    )
    with pytest.raises(RuntimeError, match="Weight"):
        parse_ta_universal_ascii(no_weight)
