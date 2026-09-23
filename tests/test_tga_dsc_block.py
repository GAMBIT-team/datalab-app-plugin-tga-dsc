from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from bokeh.models import CustomJS, GlyphRenderer, LinearAxis

from datalab_app_plugin_tga_dsc import TGAInsituBlock, __version__
from datalab_app_plugin_tga_dsc.plotting import (
    AXIS_MENU_MARKER,
    SECONDARY_OFF_LABEL,
    create_linked_tga_plots,
)
from datalab_app_plugin_tga_dsc.utils import (
    HEAT_Y_OPTIONS,
    MASS_Y_OPTIONS,
    RAW_COLUMNS,
    X_OPTIONS,
    add_derived_columns,
    parse_sta_ascii,
)

EXAMPLE_FILE = Path(__file__).parent.parent / "example_data" / "tga" / "JCC-01-29-7eqNaNH2.txt"


@pytest.fixture
def raw_df():
    return parse_sta_ascii(EXAMPLE_FILE)


def test_version():
    assert __version__
    assert TGAInsituBlock.version == __version__


def test_parse_sta_ascii(raw_df):
    assert list(raw_df.columns) == list(RAW_COLUMNS)
    assert len(raw_df) == 23701
    assert not raw_df.isnull().values.any()
    assert all(pd.api.types.is_float_dtype(dtype) for dtype in raw_df.dtypes)

    assert raw_df["Weight"].iloc[0] == pytest.approx(1.60001)
    assert raw_df["Weight"].iloc[-1] == pytest.approx(-0.509667)
    assert raw_df["t"].iloc[-1] == pytest.approx(23700.0)
    assert raw_df["Tr"].max() == pytest.approx(1000.0)


def test_parse_sta_ascii_metadata(raw_df):
    assert raw_df.attrs["sample_name"] == "JCC-01-29-7eqNaNH2"
    assert raw_df.attrs["export_timestamp"] == "09.06.2026 20:19:20"
    assert raw_df.attrs["original_filename"] == "JCC-01-29-7eqNaNH2.txt"


def test_parse_sta_ascii_missing_file():
    with pytest.raises(RuntimeError, match="does not exist"):
        parse_sta_ascii(EXAMPLE_FILE.parent / "no-such-file.txt")


def test_add_derived_columns(raw_df):
    m0 = float(raw_df["Weight"].iloc[0])
    df = add_derived_columns(raw_df, m0=m0)

    for column in (*X_OPTIONS, *MASS_Y_OPTIONS, *HEAT_Y_OPTIONS):
        assert column in df.columns

    assert df["mass (%)"].iloc[0] == pytest.approx(100.0)
    assert df["Δmass (%)"].iloc[0] == pytest.approx(0.0)
    assert df["mass (%)"].iloc[-1] == pytest.approx(-31.854, abs=1e-3)
    assert df["Δmass (%)"].iloc[-1] == pytest.approx(-131.854, abs=1e-3)
    assert df["t (min)"].iloc[-1] == pytest.approx(395.0)
    assert df["t (h)"].iloc[-1] == pytest.approx(6.5833, abs=1e-4)
    assert df["heat flow (mW/mg)"].to_numpy() == pytest.approx((df["HF"] / m0).to_numpy())

    peak = df["DTG (%/min)"].idxmin()
    assert df["Ts"][peak] == pytest.approx(919.1, abs=5.0)
    assert df["DTG (%/min)"].min() == pytest.approx(-9.488, abs=1e-2)
    assert np.isfinite(df["DTG (%/min)"]).all()


def test_add_derived_columns_rejects_zero_mass(raw_df):
    with pytest.raises(ValueError, match="non-zero"):
        add_derived_columns(raw_df, m0=0.0)


def test_block_generates_plot_and_metadata():
    block = TGAInsituBlock(item_id="test-tga-insitu")
    block.generate_insitu_tga_plot(file_path=EXAMPLE_FILE, link_plots=True)

    assert block.data["bokeh_plot_data"] is not None
    assert block.data["m0"] == pytest.approx(1.60001)
    assert block.data["sample_name"] == "JCC-01-29-7eqNaNH2"


def test_block_subsamples_long_traces():
    block = TGAInsituBlock(item_id="test-tga-insitu")
    df = block.process_and_store_data(EXAMPLE_FILE)

    assert block.data["data_granularity"] == 4
    assert len(df) == 5926


def test_block_respects_m0_override():
    block = TGAInsituBlock(item_id="test-tga-insitu")
    block.set_m0(3.2)
    df = block.process_and_store_data(EXAMPLE_FILE)

    assert block.data["m0"] == pytest.approx(3.2)
    assert df["mass (%)"].iloc[0] == pytest.approx(100.0 * 1.60001 / 3.2)


def test_block_rejects_unknown_extension(tmp_path):
    bad = tmp_path / "data.zip"
    bad.write_text("")
    block = TGAInsituBlock(item_id="test-tga-insitu")
    with pytest.raises(ValueError, match="Unsupported file extension"):
        block.generate_insitu_tga_plot(file_path=bad)


@pytest.fixture
def derived_df(raw_df):
    return add_derived_columns(raw_df, m0=float(raw_df["Weight"].iloc[0]))


def _axis_labels(layout):
    return [model.axis_label for model in layout.references() if isinstance(model, LinearAxis)]


def _renderers(layout):
    return [model for model in layout.references() if isinstance(model, GlyphRenderer)]


def test_secondary_axis_is_off_by_default(derived_df):
    layout = create_linked_tga_plots(derived_df, X_OPTIONS, MASS_Y_OPTIONS, HEAT_Y_OPTIONS)

    assert SECONDARY_OFF_LABEL + AXIS_MENU_MARKER in _axis_labels(layout)
    assert [renderer.visible for renderer in _renderers(layout)].count(False) == 1


def test_secondary_axis_can_start_on(derived_df):
    layout = create_linked_tga_plots(
        derived_df,
        X_OPTIONS,
        MASS_Y_OPTIONS,
        HEAT_Y_OPTIONS,
        secondary_y_default="DTG (%/min)",
    )

    renderers = _renderers(layout)
    assert "DTG (%/min)" + AXIS_MENU_MARKER in _axis_labels(layout)
    assert len(renderers) == 3
    assert all(renderer.visible for renderer in renderers)


def test_secondary_axis_accepts_heat_flow_options(derived_df):
    """The secondary axis offers both panels' options, not just the mass ones."""
    layout = create_linked_tga_plots(
        derived_df,
        X_OPTIONS,
        MASS_Y_OPTIONS,
        HEAT_Y_OPTIONS,
        secondary_y_default="heat flow (mW/mg)",
    )

    assert "heat flow (mW/mg)" + AXIS_MENU_MARKER in _axis_labels(layout)


def test_x_menu_drives_every_trace(derived_df):
    """A trace left off the x menu would keep plotting against a stale column."""
    layout = create_linked_tga_plots(derived_df, X_OPTIONS, MASS_Y_OPTIONS, HEAT_Y_OPTIONS)

    driven = {
        id(renderer)
        for model in layout.references()
        if isinstance(model, CustomJS) and "x_renderers" in model.args
        for renderer in model.args["x_renderers"]
    }
    assert driven == {id(renderer) for renderer in _renderers(layout)}


def test_secondary_axis_rejects_unknown_column(derived_df):
    with pytest.raises(ValueError, match="not one of the y-axis options"):
        create_linked_tga_plots(
            derived_df,
            X_OPTIONS,
            MASS_Y_OPTIONS,
            HEAT_Y_OPTIONS,
            secondary_y_default="not a column",
        )
