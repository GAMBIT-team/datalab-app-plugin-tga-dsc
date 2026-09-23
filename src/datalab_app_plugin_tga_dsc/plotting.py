"""Linked two-panel Bokeh layout for thermal analysis data."""

from collections.abc import Sequence
from typing import Any

import pandas as pd
from bokeh.events import DoubleTap, MouseLeave, MouseMove, Tap
from bokeh.layouts import column, gridplot
from bokeh.models import (
    ColumnDataSource,
    CrosshairTool,
    CustomJS,
    DataRange1d,
    HoverTool,
    LinearAxis,
    TextInput,
)
from bokeh.plotting import figure

__all__ = ("create_linked_tga_plots",)

TOOLS = "pan, box_zoom, wheel_zoom, reset, save"

AXIS_MENU_MARKER = " ▾"
"""Suffix appended to clickable axis labels (a small down-pointing triangle)."""

MASS_COLOR = "#1b9e77"
HEAT_COLOR = "#d95f02"
SECONDARY_COLOR = "#7570b3"
"""Trace colours; the upper panel's axis labels are tinted to match their trace
whenever the secondary axis is showing."""

DEFAULT_LABEL_COLOR = "#444444"
"""Bokeh's default axis label colour, restored when the secondary axis is off."""

MUTED_LABEL_COLOR = "#999999"
TRANSPARENT = "rgba(0, 0, 0, 0)"

SECONDARY_OFF_OPTION = "(none)"
"""Menu entry that hides the secondary trace."""

SECONDARY_OFF_LABEL = "+ trace"
"""Placeholder shown on the secondary axis while it is off, so that it can still
be clicked to add a trace."""

AXIS_MENU_SETUP = """
  // Shared, page-wide state for the axis menus, installed on first use.
  //
  // Bokeh's own tap event is not used to open the menu: its tap gesture waits
  // out the double-tap interval before firing, which makes clicking an axis
  // feel sluggish. Instead the hovered axis is tracked from Bokeh's mouse move
  // events, and a plain DOM `pointerdown` listener opens the menu immediately.
  const state = (function () {
    if (window._datalab_axis_menu != null) {
      return window._datalab_axis_menu;
    }

    const MARKER = " \\u25be";
    const MENU_ID = "datalab-axis-menu";

    function close_menu() {
      const existing = document.getElementById(MENU_ID);
      if (existing == null) {
        return;
      }
      for (const [target, type, handler] of existing._dismissers || []) {
        target.removeEventListener(type, handler, true);
      }
      existing.remove();
    }

    function apply_option(target, option) {
      if (target.secondary) {
        apply_secondary_option(target, option);
        return;
      }
      for (const renderer of target.renderers) {
        renderer.glyph[target.dimension].field = option;
      }
      for (const axis of target.axes) {
        axis.axis_label = option + MARKER;
      }
      target.source.change.emit();
    }

    // The secondary axis carries an extra trace that can be switched off. When
    // off it keeps a muted placeholder label, so there is still something to
    // click to bring a trace back; its ticks and axis line are hidden, and the
    // primary axis label drops back to the default colour.
    function apply_secondary_option(target, option) {
      const off = option === target.off_option;
      const axis = target.axes[0];
      const primary = target.primary_axis;

      for (const renderer of target.renderers) {
        if (!off) {
          renderer.glyph.y.field = option;
        }
        renderer.visible = !off;
      }

      axis.axis_label = (off ? target.off_label : option) + MARKER;
      axis.axis_label_text_color = off ? target.muted_color : target.color;
      axis.major_label_text_color = off ? target.transparent : target.color;
      // Take the themed colours from the primary axis, which is never restyled.
      axis.major_tick_line_color = off ? null : primary.major_tick_line_color;
      axis.axis_line_color = off ? null : primary.axis_line_color;
      primary.axis_label_text_color = off ? target.default_color : target.primary_color;

      if (target.hover != null) {
        target.hover.renderers = off ? target.hover_primary_only : target.hover_both;
      }
      target.source.change.emit();
    }

    function open_menu(target, pointer) {
      close_menu();

      let current = (target.axes[0].axis_label || "").replace(MARKER, "");
      if (target.secondary && current === target.off_label) {
        current = target.off_option;
      }
      const menu = document.createElement("div");
      menu.id = MENU_ID;
      menu.style.cssText = [
        "position: fixed",
        "z-index: 9999",
        "min-width: 9em",
        "padding: 0.25em 0",
        "background: #fff",
        "color: #212529",
        "border: 1px solid rgba(0, 0, 0, 0.2)",
        "border-radius: 0.25rem",
        "box-shadow: 0 0.25rem 0.5rem rgba(0, 0, 0, 0.15)",
        "font-family: inherit",
        "font-size: 0.9rem",
        "line-height: 1.5",
      ].join("; ");

      for (const option of target.options) {
        const item = document.createElement("div");
        item.textContent = option;
        item.style.cssText = "padding: 0.2em 1.1em; cursor: pointer; white-space: nowrap;";
        const selected = option === current;
        if (selected) {
          item.style.fontWeight = "600";
          item.style.background = "#e9ecef";
        }
        item.addEventListener("mouseenter", () => {
          item.style.background = "#dbe7f5";
        });
        item.addEventListener("mouseleave", () => {
          item.style.background = selected ? "#e9ecef" : "";
        });
        item.addEventListener("click", () => {
          close_menu();
          apply_option(target, option);
        });
        menu.appendChild(item);
      }

      document.body.appendChild(menu);

      const bbox = menu.getBoundingClientRect();
      const left = Math.min(pointer.x + 2, window.innerWidth - bbox.width - 8);
      const top = Math.min(pointer.y + 2, window.innerHeight - bbox.height - 8);
      menu.style.left = Math.max(8, left) + "px";
      menu.style.top = Math.max(8, top) + "px";

      const dismiss = (ev) => {
        if (ev.type === "pointerdown" && menu.contains(ev.target)) {
          return;
        }
        if (ev.type === "keydown" && ev.key !== "Escape") {
          return;
        }
        close_menu();
      };
      menu._dismissers = [
        [document, "pointerdown", dismiss],
        [document, "keydown", dismiss],
        [window, "scroll", dismiss],
        [window, "resize", dismiss],
      ];
      // Defer, so the click that opened the menu does not immediately close it.
      setTimeout(() => {
        for (const [el, type, handler] of menu._dismissers) {
          el.addEventListener(type, handler, true);
        }
      }, 0);
    }

    // Taps are delivered for the whole plot, including the axis gutters, with
    // data coordinates extrapolated beyond the frame. A point left of the
    // frame is therefore on the y axis, one right of it on the secondary y
    // axis, and one below it on the x axis.
    function hit(plot, x, y) {
      if (!isFinite(x) || !isFinite(y)) {
        return null;
      }
      const on_y_axis = x < plot.x_range.start;
      const on_y2_axis = x > plot.x_range.end;
      const on_x_axis = y < plot.y_range.start;
      if (on_x_axis) {
        return on_y_axis || on_y2_axis ? null : "x";
      }
      if (on_y_axis) {
        return "y";
      }
      if (on_y2_axis) {
        return "y2";
      }
      return null;
    }

    const shared = {
      target: null,
      pointer: {x: 0, y: 0},
      canvas: null,
      opened_at: 0,
      hit: hit,
      open_menu: open_menu,
      close_menu: close_menu,
      // Bokeh resets the cursor on every move it handles, just before running
      // these callbacks, so the hint is (re)applied from them rather than here.
      set_cursor: function (hovering_axis) {
        const el = shared.canvas;
        if (el == null) {
          return;
        }
        if (hovering_axis) {
          el.style.cursor = "pointer";
        } else if (el.style.cursor === "pointer") {
          el.style.cursor = "";
        }
      },
    };

    document.addEventListener("pointermove", (ev) => {
      shared.pointer = {x: ev.clientX, y: ev.clientY};
      const el = ev.target;
      if (el instanceof Element && el.classList.contains("bk-canvas-events")) {
        shared.canvas = el;
      }
    }, true);

    document.addEventListener("pointerdown", (ev) => {
      shared.pointer = {x: ev.clientX, y: ev.clientY};
      const menu = document.getElementById(MENU_ID);
      if (menu != null && menu.contains(ev.target)) {
        return;
      }
      if (shared.target == null) {
        return;
      }
      shared.opened_at = performance.now();
      open_menu(shared.target, shared.pointer);
    }, true);

    window._datalab_axis_menu = shared;
    return shared;
  })();
"""

AXIS_MENU_TARGET = """
  // Returns null for a region this panel has no menu for, e.g. the right-hand
  // gutter of a panel without a secondary axis.
  function region_target(region) {
    return region == null ? null : make_target(region);
  }

  function make_target(region) {
    if (region === "y") {
      return {
        plot_id: p.id,
        dimension: "y",
        options: y_options,
        renderers: y_renderers,
        axes: [y_axis],
        source: source,
      };
    }
    if (region === "y2") {
      if (y2_axis == null) {
        return null;
      }
      return {
        plot_id: p.id,
        dimension: "y",
        secondary: true,
        options: y2_options,
        renderers: y2_renderers,
        axes: [y2_axis],
        primary_axis: y_axis,
        source: source,
        hover: hover,
        hover_primary_only: y_renderers,
        hover_both: y_renderers.concat(y2_renderers),
        off_option: y2_off_option,
        off_label: y2_off_label,
        color: y2_color,
        primary_color: y_color,
        default_color: default_label_color,
        muted_color: muted_label_color,
        transparent: transparent_color,
      };
    }
    return {
      plot_id: p.id,
      dimension: "x",
      options: x_options,
      renderers: x_renderers,
      axes: x_axes,
      source: source,
    };
  }
"""

AXIS_HOVER_CALLBACK = (
    AXIS_MENU_SETUP
    + AXIS_MENU_TARGET
    + """
  const target = region_target(state.hit(p, cb_obj.x, cb_obj.y));
  if (target == null) {
    if (state.target != null && state.target.plot_id === p.id) {
      state.target = null;
    }
  } else {
    state.target = target;
  }
  state.set_cursor(target != null);
"""
)

AXIS_LEAVE_CALLBACK = (
    AXIS_MENU_SETUP
    + """
  if (state.target != null && state.target.plot_id === p.id) {
    state.target = null;
  }
  state.set_cursor(false);
"""
)

AXIS_TAP_CALLBACK = (
    AXIS_MENU_SETUP
    + AXIS_MENU_TARGET
    + """
  // Fallback for touch devices, which have no hover to track: Bokeh's tap
  // gesture is slower, but only fires here if `pointerdown` did not already
  // open the menu.
  if (performance.now() - state.opened_at < 750) {
    return;
  }
  const target = region_target(state.hit(p, cb_obj.x, cb_obj.y));
  if (target == null) {
    return;
  }
  state.opened_at = performance.now();
  state.open_menu(target, state.pointer);
"""
)


def _style_secondary_axis(axis, primary_axis, active: bool) -> None:
    """Style the secondary axis for its on or off state.

    While off it keeps a muted placeholder label to click, but no ticks or axis
    line. While on it is tinted to match its trace, and the primary axis label
    is tinted too so that the two are told apart.
    """
    if active:
        axis.axis_label_text_color = SECONDARY_COLOR
        axis.major_label_text_color = SECONDARY_COLOR
        primary_axis.axis_label_text_color = MASS_COLOR
    else:
        axis.axis_label_text_color = MUTED_LABEL_COLOR
        axis.major_label_text_color = TRANSPARENT
        axis.major_tick_line_color = None
        axis.axis_line_color = None


def create_linked_tga_plots(
    df: pd.DataFrame,
    x_options: Sequence[str],
    mass_y_options: Sequence[str],
    heat_y_options: Sequence[str],
    x_default: str | None = None,
    mass_y_default: str | None = None,
    heat_y_default: str | None = None,
    secondary_y_default: str | None = None,
    parameters: dict[str, dict[str, Any]] | None = None,
    link_plots: bool = True,
):
    """Build two vertically stacked line panels that share an x-axis.

    The upper panel shows a mass-derived signal and the lower panel a heat-flow
    signal. Axes are chosen by clicking their labels: clicking either x-axis
    label drives both x-axes, and clicking a y-axis label changes that panel
    only.

    The upper panel also carries a secondary y-axis on the right, for showing a
    second trace alongside the first (mass and DTG, say, or mass and heat flow).
    It offers every mass and heat-flow option, is off unless
    ``secondary_y_default`` names a column, and is switched on and off from its
    own axis label.
    """
    x_default = x_default or x_options[0]
    mass_y_default = mass_y_default or mass_y_options[0]
    heat_y_default = heat_y_default or heat_y_options[0]
    # The secondary trace may come from either panel's options.
    secondary_y_options = list(dict.fromkeys([*mass_y_options, *heat_y_options]))
    if secondary_y_default is not None and secondary_y_default not in secondary_y_options:
        raise ValueError(f"{secondary_y_default!r} is not one of the y-axis options")

    plotted = list(dict.fromkeys([*x_options, *mass_y_options, *heat_y_options]))
    missing = [name for name in plotted if name not in df.columns]
    if missing:
        raise ValueError(f"Columns missing from the data: {missing}")

    source = ColumnDataSource(df[plotted])

    # The upper panel shares the lower panel's x-axis, so it is left unlabelled
    # and only the lower x-axis carries the label (and its menu).
    mass_figure = figure(
        sizing_mode="scale_width",
        aspect_ratio=2.5,
        tools=TOOLS,
        y_axis_label=mass_y_default + AXIS_MENU_MARKER,
    )
    heat_figure = figure(
        sizing_mode="scale_width",
        aspect_ratio=2.5,
        tools=TOOLS,
        x_axis_label=x_default + AXIS_MENU_MARKER,
        y_axis_label=heat_y_default + AXIS_MENU_MARKER,
        x_range=mass_figure.x_range,
    )

    mass_line = mass_figure.line(
        x=x_default, y=mass_y_default, source=source, line_width=2, color=MASS_COLOR
    )
    heat_line = heat_figure.line(
        x=x_default, y=heat_y_default, source=source, line_width=2, color=HEAT_COLOR
    )

    # The secondary trace on the upper panel, with its own range and right-hand
    # axis. The range follows this trace alone, rather than everything drawn.
    secondary_active = secondary_y_default is not None
    secondary_y_field = secondary_y_default or mass_y_options[-1]
    mass_figure.extra_y_ranges = {"secondary": DataRange1d()}
    mass_line2 = mass_figure.line(
        x=x_default,
        y=secondary_y_field,
        source=source,
        line_width=2,
        color=SECONDARY_COLOR,
        y_range_name="secondary",
        visible=secondary_active,
    )
    mass_figure.extra_y_ranges["secondary"].renderers = [mass_line2]
    # A plot's default range auto-scales over every renderer, including those
    # drawn against an extra range, so pin it to the primary trace.
    mass_figure.y_range.renderers = [mass_line]
    secondary_axis = LinearAxis(
        y_range_name="secondary",
        axis_label=(secondary_y_field if secondary_active else SECONDARY_OFF_LABEL)
        + AXIS_MENU_MARKER,
    )
    mass_figure.add_layout(secondary_axis, "right")
    _style_secondary_axis(secondary_axis, mass_figure.yaxis[0], active=secondary_active)

    hover_tools = {}
    for fig, renderers in ((mass_figure, [mass_line]), (heat_figure, [heat_line])):
        hover_tools[fig] = HoverTool(
            renderers=[*renderers, mass_line2]
            if secondary_active and fig is mass_figure
            else renderers,
            tooltips=[("x", "$x{0.00}"), ("y", "$y{0.0000}")],
            mode="vline",
        )
        fig.add_tools(hover_tools[fig])
        fig.js_on_event(DoubleTap, CustomJS(args=dict(p=fig), code="p.reset.emit()"))

    if link_plots:
        crosshair = CrosshairTool(dimensions="height", line_color="grey")
        mass_figure.add_tools(crosshair)
        heat_figure.add_tools(crosshair)

    x_axes = [heat_figure.xaxis[0]]
    for fig, renderer, y_options in (
        (mass_figure, mass_line, mass_y_options),
        (heat_figure, heat_line, heat_y_options),
    ):
        secondary = fig is mass_figure
        menu_args = dict(
            p=fig,
            source=source,
            x_options=list(x_options),
            y_options=list(y_options),
            # Every trace follows the x-axis, the secondary one included, or
            # it would be left plotted against whichever column the x-axis
            # held when it was last shown.
            x_renderers=[mass_line, mass_line2, heat_line],
            x_axes=x_axes,
            y_renderers=[renderer],
            y_axis=fig.yaxis[0],
            y_color=MASS_COLOR if secondary else HEAT_COLOR,
            y2_options=[*secondary_y_options, SECONDARY_OFF_OPTION] if secondary else None,
            y2_renderers=[mass_line2] if secondary else None,
            y2_axis=secondary_axis if secondary else None,
            y2_color=SECONDARY_COLOR,
            y2_off_option=SECONDARY_OFF_OPTION,
            y2_off_label=SECONDARY_OFF_LABEL,
            hover=hover_tools[fig],
            default_label_color=DEFAULT_LABEL_COLOR,
            muted_label_color=MUTED_LABEL_COLOR,
            transparent_color=TRANSPARENT,
        )
        fig.js_on_event(MouseMove, CustomJS(args=menu_args, code=AXIS_HOVER_CALLBACK))
        fig.js_on_event(Tap, CustomJS(args=menu_args, code=AXIS_TAP_CALLBACK))
        fig.js_on_event(MouseLeave, CustomJS(args=dict(p=fig), code=AXIS_LEAVE_CALLBACK))

    widgets = []
    if parameters:
        for parameter in parameters.values():
            widget = TextInput(title=parameter["label"], value=str(parameter["value"]))
            if parameter.get("event"):
                widget.js_on_change("value", CustomJS(code=parameter["event"]))
            widgets.append(widget)

    grid = gridplot([[mass_figure], [heat_figure]], merge_tools=True, sizing_mode="scale_width")

    return column(
        *widgets,
        grid,
        sizing_mode="scale_width",
    )
