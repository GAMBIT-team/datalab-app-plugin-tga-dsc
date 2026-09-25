# <div align="center"><i>datalab-app-plugin-tga-dsc</i></div>

<div align="center">
<a href="https://github.com/GAMBIT-team/datalab-app-plugin-tga-dsc/releases"><img src="https://badgen.net/github/release/GAMBIT-team/datalab-app-plugin-tga-dsc?icon=github&color=blue"></a>
<a href="https://github.com/GAMBIT-team/datalab-app-plugin-tga-dsc"><img src="https://badgen.net/github/license/GAMBIT-team/datalab-app-plugin-tga-dsc?icon=license&color=purple"></a>
<a href="https://GAMBIT-team.github.io/datalab-app-plugin-tga-dsc"><img src="https://github.com/GAMBIT-team/datalab-app-plugin-tga-dsc/actions/workflows/docs.yml/badge.svg"></a>
</div>

datalab-app-plugin-tga-dsc is a [*datalab*](https://datalab-org.io) plugin for
visualising simultaneous thermogravimetric analysis and differential scanning
calorimetry data. The block was originally developed by Jamie Neilson in the
[*datalab* in-situ plugin](https://github.com/jrneilson/datalab-app-plugin-insitu/tree/feature/tga-insitu).

## Installation

This is intended to be installed into a working production or development *datalab* environment.

To install the current version of this plugin from the default branch, add the following
to `plugins.toml` in the root of the *datalab* repository (alongside `pydatalab/` and
`webapp/`). If `plugins.toml` does not exist, create it first:

```toml
dependencies = [
    "datalab-app-plugin-tga-dsc",
]

[tool.uv.sources]
datalab-app-plugin-tga-dsc = { git = "https://github.com/GAMBIT-team/datalab-app-plugin-tga-dsc.git" }
```

Omitting a Git ref installs the default branch. For a reproducible deployment, add a
`tag` or `rev` to pin a release or commit.

Then run the installer from the `pydatalab/` directory:

```shell
cd pydatalab
uv run invoke dev.install
```

## Development installation

To install for development, clone this repository into the path expected by the
configuration below. From the root of the *datalab* repository, run:

```shell
git clone https://github.com/GAMBIT-team/datalab-app-plugin-tga-dsc.git pydatalab/plugins/datalab-app-plugin-tga-dsc
```

Then add the following to `plugins.toml` in the root of the *datalab* repository:

```toml
dependencies = [
    "datalab-app-plugin-tga-dsc",
]

[tool.uv.sources]
datalab-app-plugin-tga-dsc = { path = "pydatalab/plugins/datalab-app-plugin-tga-dsc", editable = true }
```

Run the installer from the `pydatalab/` directory:

```shell
cd pydatalab
uv run invoke dev.install
```

More info on plugin development can be found at: https://docs.datalab-org.io/en/stable/plugins/#installing-plugins

## Input format

Upload the instrument's `.txt` ASCII export directly. Two formats are
recognised:

- Simultaneous TGA/DSC exports, with two header rows (column names and units)
  followed by the six whitespace-aligned columns `Index`, `Ts`, `t`, `HF`,
  `Weight`, and `Tr`. An optional final line can contain the sample name and
  export timestamp.
- TGA exports from TA Instruments' Universal Analysis, which start with a
  header of `Key<tab>value` lines (`Sample`, `Size`, `Nsig`, `Sig1`, ...)
  before the data.

Whatever metadata the file has (sample name, mass, operator, date, gases, and
so on) is stored with the block. Files without heat flow, like the TA TGA
exports, are shown as a single mass panel.

The block displays linked mass and heat-flow plots. Axes are switched by
clicking their labels: elapsed time or sample/reference temperature for the
x-axis (shared by both panels), mass/relative mass/DTG for the upper y-axis, and
absolute or mass-normalised heat flow for the lower y-axis. The upper panel has
a secondary y-axis on the right for showing a second trace alongside the first,
such as mass and DTG, or mass and heat flow; it offers every mass and heat-flow
option, is off until picked from that axis label, and the two labels are tinted
to match their traces while it is on. The initial mass used
for normalisation can be edited on the plot.

### Transitions

When a file is first plotted, the block looks for transitions on heating: steps
in the mass (placed at their inflection point) and peaks in the heat flow
(placed at their top). Each is marked on the plot and listed in a table below
it, where it can be given a kind (Curie, melting, decomposition, dehydration,
...) and a free-text comment, or have its temperature changed. Transitions can
also be deleted, added by clicking the plot, or detected again; detecting again
keeps anything that has been edited by hand.

Transitions are stored in the block's `computed` data, along with the
temperature of any marked as a Curie point as `curie_temperature`. For magnetic
TGA runs, where a magnet near the pan makes the apparent mass step at the Curie
point, the largest mass step is marked as a Curie point automatically if the
sample name, method or comment mentions one.

Mass values are not baseline- or buoyancy-corrected. DTG is calculated from a
Savitzky–Golay-smoothed mass trace before the data are reduced for display.

Releases are created via semantic version tags on GitHub, and will require manually updating the CHANGELOG.
