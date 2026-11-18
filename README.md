[README_special_dimensions_3d.md](https://github.com/user-attachments/files/35002275/README_special_dimensions_3d.md)
# Special 3D Dimensions for IngeTrazo

This extension creates solid 3D dimension geometry and text that can be
exported with the model to GLB.

Explore your projects with confidence: measurements are calculated from the
guides' geometric definitions, while perimeter, arc, and angle analysis helps
reveal relationships and communicate design decisions clearly.

<img width="640" height="530" alt="Special Dim" src="https://github.com/user-attachments/assets/31e34074-54f1-43bb-af4f-49793e1c8920" />


## Requirements

- No other extension is required. Guide lines and arcs are managed and stored
  by Special 3D Dimensions in the current document.

## Features

- Measure a perimeter made from connected straight guide lines and continuous
  arcs. Full-circle guides are accepted as closed loops.
- Choose whether perimeter dimensions include hole boundaries. When holes are
  excluded, only outer loops and nested islands are measured.
- Convert selected model edges/faces with **Extensions > Convert Selected
  Edges/Faces to Guides**. Curved boundary
  runs made from segmented edges are recognized as guide arcs.
- Convert selected edges/faces to guides, then edit, move, duplicate, and
  delete guide lines and arcs in the **Special Dimension Guides** panel.
  Select one guide and use **Rename** to change its panel label without
  changing its geometry.
- Refine partial-arc boundaries with **Arc + Line** to absorb a connected
  guide line into the arc, or **Arc - Line** to trim the arc and retain the
  line as a straight edge. Both operations project the selected boundary point
  onto the original circle, preserving the arc's center and radius even when
  the segment endpoint is off-circle. These operations edit guide data only;
  they do not delete edges from the original model geometry.
- Show or hide guide overlays with **Show guides in viewport**. This only
  toggles the guides; the model and its normal 3D display are unchanged.
- Selecting guides in the panel preselects the matching guides in the
  dimension-creation dialog.
- Measure arc length and arc sweep.
- Measure arc and full-circle radii, with the center marked in the generated
  dimension geometry.
- Measure the diameter of a full-circle guide, with the center marked.
- Measure the angle between two intersecting guide lines.
- Display angles as decimal degrees or degrees-minutes-seconds.
- Customize the text prefix for each measurement type; defaults such as
  `Perimeter: ` are editable and retain the spaces typed after them.
- Move a generated measurement label in the viewport with
  **Extensions > Move Special Dimension Text**.
- Choose whether a newly created dimension is selected, showing or hiding its
  orange selection frame without changing the dimension itself.
- Choose a dimension view, text font, text height, extrusion, offset, decimal
  precision, and color.
- Refresh generated dimensions with **Extensions > Update Special 3D
  Dimensions** after changing their source guides. Creation and updates can be
  undone with `Ctrl+Z`.

## Usage

1. To create guides from the model, use IngeTrazo's edge/face selection mode
   and drag a selection area, then choose **Extensions > Convert Selected
   Edges/Faces to Guides**. Segmented circular runs are converted to guide
   arcs; the remaining boundary edges become guide lines.
2. Manage converted guide lines and arcs in the **Special Dimension Guides**
   panel using **Edit**, **Move**, **Duplicate**, and **Delete**. To refine an
   arc, select exactly one arc and one line. Use **Rename** in the fixed panel
   to change a selected guide's name without altering its geometry. Use
   **Arc + Line** when the line
   meets an arc endpoint and continues beyond it; this extends the arc along
   its existing circle to the projected boundary point and removes the guide
   line. Use **Arc - Line** when the line joins an arc endpoint to a point
   inside the arc; this trims the arc along the same circle and retains the
   guide line, snapping its endpoint to the arc. The selected points must lie
   in the arc's plane.
3. Choose **Extensions > Create Special 3D Dimension…** and select the
   measurement type.
4. Guides selected in the panel are preselected in the creation dialog. For a
   perimeter, adjust the checked guide lines/arcs as needed and choose whether
   to include hole perimeters. For other measurements, choose the applicable
   arc, circle, or two guide lines. Circle diameter is measured across the
   selected full-circle guide.
5. Choose the angle format where applicable, then set the text and view
   options. Edit **Text prefix** to use a localized abbreviation or wording.
   Toggle **Show orange frame for created dimension** to control its selection
   outline. Export the model to GLB using IngeTrazo's normal export command.
6. To reposition a created label, choose **Extensions > Move Special Dimension
   Text**, then drag the text in the viewport. Older dimensions may need
   **Extensions > Update Special 3D Dimensions** first.

Perimeter guide endpoints must connect within a small geometric tolerance, and
every perimeter loop must be closed. Angle guide lines must intersect in 3D.
Guide-circle and guide-arc dimensions use the mathematical guide definitions,
not the faceted geometry displayed in the viewport.

Select one or more guides in the panel and use **Move** or **Delete** to edit
them. Uncheck **Show guides in viewport** to hide the guide overlays without
hiding the model or generated dimensions.

## Installation

Copy `special_dimensions_3d.py` into the folder opened by **Extensions > Open
plugins folder**, then restart IngeTrazo. No separate continuous-guide plugin
or integration API is needed.
