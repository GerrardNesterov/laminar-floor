# LaminarFloor

**Experimental DXF-to-STL lattice panel generator for FDM/FFF printing.**

[Русский](../README.md) · [Report an issue](https://github.com/GerrardNesterov/laminar-floor/issues)

LaminarFloor explores a specific design problem: a perforated floor panel that rests on existing supports and allows vertical airflow. Provide panel dimensions, a closed support profile in DXF, material properties, load assumptions and printing parameters. The application generates the working surface, reinforcing ribs and support seats, displays a 3D preview, and exports an individual panel as STL.

## Features

- Closed DXF support profiles interpreted as surfaces of revolution, with selectable top edge and axis position.
- Panel dimensions, nominal thickness and an additional outer flange of 0–10 mm.
- PETG, PP and PA6 presets; nozzle, extrusion width and layer-height settings.
- Preliminary mechanical, airflow, contact and printability assessments.
- Layer visibility controls, problem highlighting and a calculation report.
- Local desktop workflow without a browser or server.

## Release status

The publication is based on **v6, built on 19 November 2026**, targeting **Windows 8.1 x64**. A previous iteration passed clean-Windows testing; that validation was not repeated for v6. A successful executable build is not a full acceptance test. Other operating systems are not claimed as supported by this release.

[Download the v6 preview](https://github.com/GerrardNesterov/laminar-floor/releases/tag/v6-preview). Save the EXE to a normal folder and launch it. Python and Qt are bundled. Application sources are available under **GPL-3.0**; see [LICENSE](../LICENSE), [build instructions](BUILD.md) and [third-party notices](../THIRD_PARTY_NOTICES.md).

## Limitations

This is an experimental design tool, not a certified structural engineering package. Numerical estimates do not establish load-bearing safety or replace physical testing. Sideways airflow is not modelled. The current interface has no JSON project save/load, wheel-load mode, STEP export or assembled-floor export. STL export can remain available even when the report advises against it.

## Feedback

Open an issue with your Windows version, reproduction steps, panel settings and error message. Share a minimal DXF example only if it contains no private or proprietary information.
