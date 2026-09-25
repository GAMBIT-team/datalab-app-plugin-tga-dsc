"""Transition markers on the plot, and the table for editing them.

Edits are sent back to the block as ``set_transitions`` events carrying the
whole edited list, and re-detection as a ``detect_transitions`` event. The code
that actually dispatches an event is supplied by the caller, as a snippet that
sends the JS object ``detail``.
"""

import math
from collections.abc import Sequence

import pandas as pd
from bokeh.events import Tap
from bokeh.layouts import column, row
from bokeh.models import (
    Button,
    CellEditor,
    ColumnDataSource,
    CustomJS,
    DataTable,
    GlyphRenderer,
    HoverTool,
    NumberEditor,
    NumberFormatter,
    SelectEditor,
    StringEditor,
    TableColumn,
    Toggle,
)

from datalab_app_plugin_tga_dsc.transitions import TRANSITION_KINDS, Transition, row_at_temperature

__all__ = (
    "TEMPERATURE_COLUMN",
    "transition_marker_source",
    "add_transition_markers",
    "transition_editor",
)

TEMPERATURE_COLUMN = "Ts (°C)"
"""The plotted column that transitions are placed against."""

MARKER_COLOR = "#333333"

ADD_LABEL = "Add a transition"
ADDING_LABEL = "Click the plot where it happens…"

_EDIT_LIBRARY = """
  function rows_from_table() {
    const d = table.data;
    const rows = [];
    for (let i = 0; i < d.id.length; i++) {
      rows.push({id: d.id[i], temperature: Number(d.temperature[i]), kind: d.kind[i], comment: d.comment[i]});
    }
    return rows;
  }
  function send(detail) {
    __DISPATCH__
  }
"""

_ON_EDIT = "send({event_name: 'set_transitions', transitions: rows_from_table()});"

_ON_DELETE = """
  const selected = new Set(table.selected.indices);
  if (selected.size) {
    send({event_name: 'set_transitions', transitions: rows_from_table().filter((_, i) => !selected.has(i))});
  }
"""

_ON_DETECT = "send({event_name: 'detect_transitions'});"

_ON_TOGGLE = "toggle.label = toggle.active ? ADDING_LABEL : ADD_LABEL;"

# Clicks are placed by temperature: directly if temperature is on the x-axis,
# otherwise from the nearest point along whatever the x-axis shows.
_ON_TAP = """
  if (!toggle.active) {
    return;
  }
  const inside = (value, range) =>
    value >= Math.min(range.start, range.end) && value <= Math.max(range.start, range.end);
  if (!inside(cb_obj.x, p.x_range) || !inside(cb_obj.y, p.y_range)) {
    return;
  }
  const x_field = line.glyph.x.field;
  let temperature = cb_obj.x;
  if (x_field !== TEMPERATURE) {
    const xs = source.data[x_field];
    let best = 0;
    for (let i = 1; i < xs.length; i++) {
      if (Math.abs(xs[i] - cb_obj.x) < Math.abs(xs[best] - cb_obj.x)) {
        best = i;
      }
    }
    temperature = source.data[TEMPERATURE][best];
  }
  toggle.active = false;
  const rows = rows_from_table();
  rows.push({temperature: Number(temperature), kind: '', comment: ''});
  send({event_name: 'set_transitions', transitions: rows});
"""

_ON_SELECT = """
  const ids = new Set(table.selected.indices.map((i) => table.data.id[i]));
  for (const marker of markers) {
    marker.selected.indices = marker.data.id
      .map((id, i) => (ids.has(id) ? i : -1))
      .filter((i) => i >= 0);
  }
"""


def transition_marker_source(
    df: pd.DataFrame, transitions: Sequence[Transition], columns: Sequence[str]
) -> ColumnDataSource:
    """Place ``transitions`` on the data, with a value for each of ``columns``.

    Transitions that the data never heat through are left out.
    """
    data: dict[str, list] = {"id": [], "label": [], "comment": [], **{c: [] for c in columns}}
    for transition in transitions:
        values = row_at_temperature(df, transition.temperature)
        if values is None:
            continue
        data["id"].append(transition.id)
        data["label"].append(transition.label)
        data["comment"].append(transition.comment)
        for c in columns:
            data[c].append(values.get(c, math.nan))
    return ColumnDataSource(data)


def add_transition_markers(fig, source: ColumnDataSource, x: str, y: str) -> list[GlyphRenderer]:
    """Draw a labelled marker for each transition in ``source``.

    Returns the renderers, which the axis menus must drive alongside the traces.
    """
    marker = fig.scatter(
        x=x,
        y=y,
        source=source,
        marker="inverted_triangle",
        size=11,
        fill_color="white",
        line_color=MARKER_COLOR,
        line_width=1.5,
        selection_fill_color=MARKER_COLOR,
        nonselection_fill_alpha=1.0,
        nonselection_line_alpha=1.0,
    )
    label = fig.text(
        x=x,
        y=y,
        text="label",
        source=source,
        y_offset=-10,
        text_align="center",
        text_baseline="bottom",
        text_font_size="8.5pt",
        text_color=MARKER_COLOR,
    )
    fig.add_tools(HoverTool(renderers=[marker], tooltips=[("", "@label"), ("", "@comment")]))
    return [marker, label]


def transition_editor(
    transitions: Sequence[Transition],
    source: ColumnDataSource,
    figures: Sequence,
    line: GlyphRenderer,
    marker_sources: Sequence[ColumnDataSource],
    dispatch: str,
):
    """Build the table and buttons for editing transitions.

    ``source`` holds the plotted data, and ``line`` is a trace whose x field
    follows the x-axis menu; clicks on ``figures`` are placed with them.
    ``dispatch`` is JS that sends the object ``detail`` as a block event.
    """
    table_source = ColumnDataSource(
        {
            "id": [t.id for t in transitions],
            "temperature": [t.temperature for t in transitions],
            "kind": [t.kind for t in transitions],
            "comment": [t.comment for t in transitions],
            "signal": [t.signal or "" for t in transitions],
            "mass_change": [
                math.nan if t.mass_change is None else t.mass_change for t in transitions
            ],
        }
    )
    read_only = CellEditor()
    table = DataTable(
        source=table_source,
        columns=[
            TableColumn(
                field="temperature",
                title="T (°C)",
                formatter=NumberFormatter(format="0.0"),
                editor=NumberEditor(step=0.1),
                width=70,
            ),
            TableColumn(
                field="kind",
                title="Kind",
                editor=SelectEditor(options=list(TRANSITION_KINDS)),
                width=140,
            ),
            TableColumn(field="comment", title="Comment", editor=StringEditor(), width=300),
            TableColumn(field="signal", title="Found in", editor=read_only, width=70),
            TableColumn(
                field="mass_change",
                title="Δmass (%)",
                formatter=NumberFormatter(format="0.00", nan_format=""),
                editor=read_only,
                width=70,
            ),
        ],
        editable=True,
        reorderable=False,
        index_position=None,
        sizing_mode="stretch_width",
        height=28 * (max(len(transitions), 1) + 1) + 4,
    )

    library = _EDIT_LIBRARY.replace("__DISPATCH__", dispatch)
    toggle = Toggle(label=ADD_LABEL, width=220)
    delete = Button(label="Delete selected", width=130)
    detect = Button(label="Detect again", width=130)

    table_source.js_on_change(
        "patching", CustomJS(args=dict(table=table_source), code=library + _ON_EDIT)
    )
    delete.js_on_click(CustomJS(args=dict(table=table_source), code=library + _ON_DELETE))
    detect.js_on_click(CustomJS(args=dict(table=table_source), code=library + _ON_DETECT))
    toggle.js_on_change(
        "active",
        CustomJS(
            args=dict(toggle=toggle, ADD_LABEL=ADD_LABEL, ADDING_LABEL=ADDING_LABEL),
            code=_ON_TOGGLE,
        ),
    )
    for fig in figures:
        fig.js_on_event(
            Tap,
            CustomJS(
                args=dict(
                    p=fig,
                    toggle=toggle,
                    line=line,
                    source=source,
                    table=table_source,
                    TEMPERATURE=TEMPERATURE_COLUMN,
                ),
                code=library + _ON_TAP,
            ),
        )
    table_source.selected.js_on_change(
        "indices",
        CustomJS(args=dict(table=table_source, markers=list(marker_sources)), code=_ON_SELECT),
    )

    return column(row(toggle, delete, detect), table, sizing_mode="stretch_width")
