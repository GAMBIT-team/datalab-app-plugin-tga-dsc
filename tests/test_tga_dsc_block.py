from pathlib import Path

import numpy as np
import pytest
from bokeh.models import CustomJS, GlyphRenderer, LinearAxis

from datalab_app_plugin_tga_dsc import TGAInsituBlock, __version__
from datalab_app_plugin_tga_dsc.parsers import parse_sta_ascii, parse_ta_universal_ascii
from datalab_app_plugin_tga_dsc.plotting import (
    AXIS_MENU_MARKER,
    SECONDARY_OFF_LABEL,
    create_linked_tga_plots,
)
from datalab_app_plugin_tga_dsc.utils import (
    HEAT_Y_OPTIONS,
    MASS_Y_OPTIONS,
    X_OPTIONS,
    add_derived_columns,
    available_options,
)

EXAMPLE_DIR = Path(__file__).parent.parent / "example_data" / "tga"
EXAMPLE_FILE = EXAMPLE_DIR / "JCC-01-29-7eqNaNH2.txt"
CURIE_FILE = EXAMPLE_DIR / "Fe-Curie T-August 2026.txt"
"""A TGA run with a magnet under the pan, so the apparent mass steps at the
Curie point of iron (770 °C). It has no heat flow."""


@pytest.fixture
def raw_df():
    return parse_sta_ascii(EXAMPLE_FILE)[0]


@pytest.fixture
def curie_df():
    raw, _ = parse_ta_universal_ascii(CURIE_FILE)
    return add_derived_columns(raw, m0=float(raw["Weight"].iloc[0]))


def test_version():
    assert __version__
    assert TGAInsituBlock.version == __version__


def test_add_derived_columns(raw_df):
    m0 = float(raw_df["Weight"].iloc[0])
    df = add_derived_columns(raw_df, m0=m0)

    # Every column but the instrument's own DTG, which this file lacks.
    assert available_options(df, (*X_OPTIONS, *MASS_Y_OPTIONS, *HEAT_Y_OPTIONS)) == tuple(
        column
        for column in (*X_OPTIONS, *MASS_Y_OPTIONS, *HEAT_Y_OPTIONS)
        if column != "DTG (%/°C)"
    )

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


def test_add_derived_columns_without_heat_flow(curie_df):
    assert available_options(curie_df, X_OPTIONS) == ("t (s)", "t (min)", "t (h)", "Ts (°C)")
    assert available_options(curie_df, MASS_Y_OPTIONS) == MASS_Y_OPTIONS
    assert available_options(curie_df, HEAT_Y_OPTIONS) == ()
    # The step down at the Curie point, as a negative DTG.
    peak = curie_df["DTG (%/°C)"].idxmin()
    assert curie_df["Ts (°C)"][peak] == pytest.approx(773.0, abs=2.0)


def test_add_derived_columns_rejects_zero_mass(raw_df):
    with pytest.raises(ValueError, match="non-zero"):
        add_derived_columns(raw_df, m0=0.0)


def test_block_generates_plot_and_metadata():
    block = TGAInsituBlock(item_id="test-tga-insitu")
    block.generate_insitu_tga_plot(file_path=EXAMPLE_FILE, link_plots=True)

    assert block.data["bokeh_plot_data"] is not None
    assert block.data["m0"] == pytest.approx(1.60001)
    assert block.data["metadata"]["sample_name"] == "JCC-01-29-7eqNaNH2"
    assert block.data["metadata"]["export_timestamp"] == "09.06.2026 20:19:20"


def test_block_plots_curie_file():
    block = TGAInsituBlock(item_id="test-tga-insitu")
    block.generate_insitu_tga_plot(file_path=CURIE_FILE)

    assert block.data["bokeh_plot_data"] is not None
    assert block.data["m0"] == pytest.approx(15.8394)

    metadata = block.data["metadata"]
    assert metadata["file_format"] == "ta_universal_ascii"
    assert metadata["sample_mass_mg"] == pytest.approx(15.485)
    assert metadata["measured_at"] == "2026-08-03T17:12:00"
    assert metadata["gas1"] == "Argon 100mL/min"


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


def _options(df):
    return tuple(
        available_options(df, options) for options in (X_OPTIONS, MASS_Y_OPTIONS, HEAT_Y_OPTIONS)
    )


def _axis_labels(layout):
    return [model.axis_label for model in layout.references() if isinstance(model, LinearAxis)]


def _renderers(layout):
    return [model for model in layout.references() if isinstance(model, GlyphRenderer)]


def test_secondary_axis_is_off_by_default(derived_df):
    layout = create_linked_tga_plots(derived_df, *_options(derived_df))

    assert SECONDARY_OFF_LABEL + AXIS_MENU_MARKER in _axis_labels(layout)
    assert [renderer.visible for renderer in _renderers(layout)].count(False) == 1


def test_secondary_axis_can_start_on(derived_df):
    layout = create_linked_tga_plots(
        derived_df,
        *_options(derived_df),
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
        *_options(derived_df),
        secondary_y_default="heat flow (mW/mg)",
    )

    assert "heat flow (mW/mg)" + AXIS_MENU_MARKER in _axis_labels(layout)


def test_x_menu_drives_every_trace(derived_df):
    """A trace left off the x menu would keep plotting against a stale column."""
    layout = create_linked_tga_plots(derived_df, *_options(derived_df))

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
            *_options(derived_df),
            secondary_y_default="not a column",
        )


def test_no_heat_flow_gives_one_panel(curie_df):
    layout = create_linked_tga_plots(
        curie_df,
        available_options(curie_df, X_OPTIONS),
        available_options(curie_df, MASS_Y_OPTIONS),
        (),
        x_default="Ts (°C)",
    )

    renderers = _renderers(layout)
    labels = _axis_labels(layout)
    # The mass trace and the (hidden) secondary trace, but no heat flow.
    assert len(renderers) == 2
    assert not any("heat flow" in label for label in labels if label)
    # With no lower panel, the upper panel carries the x-axis label.
    assert "Ts (°C)" + AXIS_MENU_MARKER in labels


def test_no_heat_flow_x_menu_drives_every_trace(curie_df):
    layout = create_linked_tga_plots(
        curie_df,
        available_options(curie_df, X_OPTIONS),
        available_options(curie_df, MASS_Y_OPTIONS),
        (),
    )

    driven = {
        id(renderer)
        for model in layout.references()
        if isinstance(model, CustomJS) and "x_renderers" in model.args
        for renderer in model.args["x_renderers"]
    }
    assert driven == {id(renderer) for renderer in _renderers(layout)}
