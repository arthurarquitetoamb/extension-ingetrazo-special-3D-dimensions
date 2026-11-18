# SPDX-License-Identifier: GPL-3.0-or-later
"""Create GLB-exportable perimeter, arc, radius, diameter, and angle dimensions."""

from __future__ import annotations

import logging
import math
import uuid

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QMatrix4x4,
    QPainterPath,
    QPen,
    QVector3D,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFontComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.group import Group
from core.history import Command
from core.layers import DEFAULT_LAYER, active_layer
from core.mesh import Edge, Face, Mesh
from tools.base import Tool, ToolContext

KEY = "special_dimensions_3d"
GUIDE_DATA_KEY = "special_guides"
COLOR = (0.05, 0.38, 0.95)
TAU = math.tau
log = logging.getLogger("ingetrazo.plugins.special_dimensions_3d")

MODE_PERIMETER = "perimeter"
MODE_ARC_LENGTH = "arc-length"
MODE_ARC_RADIUS = "arc-radius"
MODE_CIRCLE_RADIUS = "circle-radius"
MODE_CIRCLE_DIAMETER = "circle-diameter"
MODE_LINE_ANGLE = "line-angle"
MODE_ARC_ANGLE = "arc-angle"

_DEFAULT_PREFIXES = {
    MODE_PERIMETER: "Perimeter: ",
    MODE_ARC_LENGTH: "Arc length: ",
    MODE_ARC_RADIUS: "Radius: ",
    MODE_CIRCLE_RADIUS: "Radius: ",
    MODE_CIRCLE_DIAMETER: "Diameter: ",
    MODE_LINE_ANGLE: "Angle: ",
    MODE_ARC_ANGLE: "Arc angle: ",
}


def _default_dimension_prefix(mode: str) -> str:
    return _DEFAULT_PREFIXES.get(mode, "")


def _unit(vector: QVector3D) -> QVector3D:
    length = vector.length()
    if length <= 1e-12:
        raise ValueError("A zero-length direction cannot define a dimension.")
    return vector / length


def _vector(values) -> QVector3D:
    if not isinstance(values, (tuple, list)) or len(values) != 3:
        raise ValueError("A guide contains invalid coordinates.")
    return QVector3D(float(values[0]), float(values[1]), float(values[2]))


def _point_on_arc(arc: dict, angle: float) -> QVector3D:
    center = _vector(arc["center"])
    u = _unit(_vector(arc["u"]))
    v = _unit(_vector(arc["v"]))
    return center + (u * math.cos(angle) + v * math.sin(angle)) * float(
        arc["radius"]
    )


def _arc_start_end(arc: dict) -> tuple[QVector3D, QVector3D]:
    start = float(arc["a0"])
    return (
        _point_on_arc(arc, start),
        _point_on_arc(arc, start + float(arc["sweep"])),
    )


def _arc_points(arc: dict, reverse: bool = False) -> list[QVector3D]:
    sweep = float(arc["sweep"])
    count = max(16, min(256, int(math.ceil(abs(sweep) / (math.pi / 48.0)))))
    points = [
        _point_on_arc(arc, float(arc["a0"]) + sweep * index / count)
        for index in range(count + 1)
    ]
    return list(reversed(points)) if reverse else points


def _arc_length(arc: dict) -> float:
    return abs(float(arc["sweep"])) * float(arc["radius"])


def _plane_basis(normal: QVector3D) -> tuple[QVector3D, QVector3D, QVector3D]:
    normal = _unit(normal)
    reference = QVector3D(1.0, 0.0, 0.0)
    first = reference - normal * QVector3D.dotProduct(reference, normal)
    if first.length() <= 1e-8:
        reference = QVector3D(0.0, 1.0, 0.0)
        first = reference - normal * QVector3D.dotProduct(reference, normal)
    first = _unit(first)
    second = _unit(QVector3D.crossProduct(normal, first))
    return first, second, normal


def _arc_from_center(
    name: str,
    center: QVector3D,
    normal: QVector3D,
    radius: float,
    start_degrees: float,
    end_degrees: float,
) -> dict:
    if radius <= 1e-9:
        raise ValueError("Arc radius must be greater than zero.")
    first, second, normal = _plane_basis(normal)
    sweep = math.radians(end_degrees - start_degrees)
    if abs(sweep) <= 1e-9:
        sweep = TAU
    if abs(sweep) > TAU + 1e-9:
        raise ValueError("An arc sweep cannot exceed 360 degrees.")
    return {
        "uid": str(uuid.uuid4()),
        "name": name or "Arc",
        "center": [center.x(), center.y(), center.z()],
        "normal": [normal.x(), normal.y(), normal.z()],
        "u": [first.x(), first.y(), first.z()],
        "v": [second.x(), second.y(), second.z()],
        "radius": float(radius),
        "a0": math.radians(start_degrees),
        "sweep": sweep,
    }


def _arc_from_three_points(
    name: str,
    start: QVector3D,
    through: QVector3D,
    end: QVector3D,
) -> dict:
    normal = QVector3D.crossProduct(through - start, end - start)
    first, second, normal = _plane_basis(normal)

    def project(point):
        relative = point - start
        return (
            QVector3D.dotProduct(relative, first),
            QVector3D.dotProduct(relative, second),
        )

    a = (0.0, 0.0)
    b = project(through)
    c = project(end)
    denominator = 2.0 * (
        a[0] * (b[1] - c[1])
        + b[0] * (c[1] - a[1])
        + c[0] * (a[1] - b[1])
    )
    if abs(denominator) <= 1e-12:
        raise ValueError("The three points are collinear and cannot define an arc.")
    a_squared = a[0] ** 2 + a[1] ** 2
    b_squared = b[0] ** 2 + b[1] ** 2
    c_squared = c[0] ** 2 + c[1] ** 2
    center_x = (
        a_squared * (b[1] - c[1])
        + b_squared * (c[1] - a[1])
        + c_squared * (a[1] - b[1])
    ) / denominator
    center_y = (
        a_squared * (c[0] - b[0])
        + b_squared * (a[0] - c[0])
        + c_squared * (b[0] - a[0])
    ) / denominator
    center = start + first * center_x + second * center_y
    radius = (start - center).length()

    def angle_of(point):
        relative = point - center
        return math.atan2(
            QVector3D.dotProduct(relative, second),
            QVector3D.dotProduct(relative, first),
        )

    start_angle = angle_of(start)
    through_angle = (angle_of(through) - start_angle) % TAU
    end_angle = (angle_of(end) - start_angle) % TAU
    sweep = end_angle if through_angle <= end_angle else end_angle - TAU
    if abs(sweep) <= 1e-9:
        raise ValueError("The three points do not define a usable arc.")
    return {
        "uid": str(uuid.uuid4()),
        "name": name or "Arc",
        "center": [center.x(), center.y(), center.z()],
        "normal": [normal.x(), normal.y(), normal.z()],
        "u": [first.x(), first.y(), first.z()],
        "v": [second.x(), second.y(), second.z()],
        "radius": radius,
        "a0": start_angle,
        "sweep": sweep,
    }


def _guide_snapshot(app) -> dict:
    data = app.document_data({}) or {}
    guides = data.get(GUIDE_DATA_KEY, {}) if isinstance(data, dict) else {}
    if not isinstance(guides, dict):
        guides = {}
    return {
        "arcs": [
            dict(arc) for arc in guides.get("arcs", [])
            if isinstance(arc, dict)
        ],
        "lines": [
            dict(line) for line in guides.get("lines", [])
            if isinstance(line, dict)
        ],
    }


def _store_guides(app, state: dict) -> None:
    data = app.document_data({}) or {}
    if not isinstance(data, dict):
        raise ValueError("Special-dimension document data is invalid.")
    data[GUIDE_DATA_KEY] = {
        "arcs": [dict(arc) for arc in state["arcs"]],
        "lines": [dict(line) for line in state["lines"]],
    }
    app.set_document_data(data)


def _selected_mesh_transform(scene, selected, collection_name: str) \
        -> QMatrix4x4:
    candidates = []
    seen_meshes = set()
    edit_group = getattr(scene, "edit_group", None)
    for mesh in (
        getattr(scene, "loose_mesh", None),
        getattr(scene, "mesh", None),
    ):
        if mesh is getattr(edit_group, "mesh", None):
            continue
        if mesh is not None and id(mesh) not in seen_meshes:
            seen_meshes.add(id(mesh))
            if selected in getattr(mesh, collection_name, ()):
                candidates.append((QMatrix4x4(), mesh))

    def visit(group, parent_matrix):
        matrix = QMatrix4x4(parent_matrix)
        if group.xform is not None:
            matrix = matrix * group.xform
        if selected in getattr(group.mesh, collection_name, ()):
            candidates.append((matrix, group.mesh))
        for child in group.children or ():
            visit(child, matrix)

    for root in scene.groups:
        visit(root, QMatrix4x4())
    if len(candidates) != 1:
        if not candidates:
            raise ValueError("Could not locate the selected geometry in the document.")
        raise ValueError(
            "A selected edge belongs to multiple component placements. "
            "Make the component unique before converting it."
        )
    return candidates[0][0]


def _selected_edge_world_endpoints(scene, selected: Edge) \
        -> tuple[QVector3D, QVector3D]:
    matrix = _selected_mesh_transform(scene, selected, "edges")
    return matrix.map(QVector3D(selected.a)), matrix.map(QVector3D(selected.b))


def _selected_face_world_loops(scene, selected: Face) -> list[list[QVector3D]]:
    matrix = _selected_mesh_transform(scene, selected, "faces")
    loops = [selected.loop, *selected.hole_loops]
    return [
        [matrix.map(QVector3D(vertex.position)) for vertex in loop]
        for loop in loops
        if len(loop) >= 3
    ]


def _point_key(point: QVector3D) -> tuple[float, float, float]:
    return round(point.x(), 5), round(point.y(), 5), round(point.z(), 5)


def _edge_paths(
    segments: list[tuple[QVector3D, QVector3D]],
) -> list[tuple[list[QVector3D], bool]]:
    points = {}
    edges = []
    seen = set()
    for start, end in segments:
        a, b = _point_key(start), _point_key(end)
        if a == b:
            continue
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        points.setdefault(a, QVector3D(start))
        points.setdefault(b, QVector3D(end))
        edges.append((a, b))
    adjacency = {key: [] for key in points}
    for index, (a, b) in enumerate(edges):
        adjacency[a].append(index)
        adjacency[b].append(index)

    remaining = set(range(len(edges)))
    paths = []
    while remaining:
        edge_index = min(remaining)
        a, b = edges[edge_index]
        if len(adjacency[a]) != 2:
            start = a
        elif len(adjacency[b]) != 2:
            start = b
        else:
            start = a

        chain = [start]
        current = start
        while True:
            candidates = [
                index for index in adjacency[current] if index in remaining
            ]
            if not candidates:
                break
            next_edge = candidates[0]
            remaining.remove(next_edge)
            first, second = edges[next_edge]
            current = second if first == current else first
            chain.append(current)
            if current == start or len(adjacency[current]) != 2:
                break

        closed = len(chain) > 2 and chain[-1] == chain[0]
        if closed:
            chain.pop()
        if len(chain) >= 2:
            paths.append(([points[key] for key in chain], closed))
    return paths


def _wrapped_angle_difference(angle: float) -> float:
    return (angle + math.pi) % TAU - math.pi


def _circle_run_sweep(
    circle: dict,
    points: list[QVector3D],
    run: list[int],
    closed: bool,
) -> float:
    center = _vector(circle["center"])
    u = _unit(_vector(circle["u"]))
    v = _unit(_vector(circle["v"]))
    angles = []
    for index in run:
        direction = points[index] - center
        angles.append(math.atan2(
            QVector3D.dotProduct(direction, v),
            QVector3D.dotProduct(direction, u),
        ))
    sweep = sum(
        _wrapped_angle_difference(second - first)
        for first, second in zip(angles, angles[1:])
    )
    if closed and len(run) == len(points):
        sweep += _wrapped_angle_difference(angles[0] - angles[-1])
    return sweep


def _circular_runs(
    points: list[QVector3D], closed: bool
) -> list[tuple[dict, list[int], float, float]]:
    count = len(points)
    if count < 4:
        return []
    best_by_run = {}
    stride = max(1, math.ceil(count / 512))
    if closed:
        starts = range(0, count, stride)
    else:
        starts = list(range(0, count - 3, stride))
        if starts[-1] != count - 4:
            starts.append(count - 4)
    for start in starts:
        window = (
            [points[(start + offset) % count] for offset in range(4)]
            if closed
            else points[start:start + 4]
        )
        try:
            circle = _arc_from_three_points(
                "Detected arc", window[0], window[1], window[3]
            )
        except ValueError:
            continue
        center = _vector(circle["center"])
        normal = _unit(_vector(circle["normal"]))
        radius = float(circle["radius"])
        tolerance = max(1e-5, radius * 0.005)
        inliers = []
        residuals = []
        for point in points:
            delta = point - center
            plane_error = abs(QVector3D.dotProduct(delta, normal))
            radial_error = abs(delta.length() - radius)
            residual = max(plane_error, radial_error)
            residuals.append(residual)
            inliers.append(residual <= tolerance)

        if closed:
            if all(inliers):
                runs = [list(range(count))]
            else:
                runs = []
                for index in range(count):
                    if not inliers[index] or inliers[(index - 1) % count]:
                        continue
                    run = []
                    current = index
                    while inliers[current]:
                        run.append(current)
                        current = (current + 1) % count
                    runs.append(run)
        else:
            runs = []
            index = 0
            while index < count:
                if not inliers[index]:
                    index += 1
                    continue
                run = []
                while index < count and inliers[index]:
                    run.append(index)
                    index += 1
                runs.append(run)

        for run in runs:
            is_full_loop = closed and len(run) == count
            minimum_points = 8 if is_full_loop else 5
            if len(run) < minimum_points:
                continue
            sweep = _circle_run_sweep(circle, points, run, closed)
            if abs(sweep) < math.radians(8.0):
                continue
            error = sum(residuals[index] for index in run) / len(run)
            key = tuple(run)
            current = best_by_run.get(key)
            if current is None or error < current[2]:
                best_by_run[key] = (circle, run, error, sweep)

    return sorted(
        (
            (circle, run, error, sweep)
            for circle, run, error, sweep in best_by_run.values()
        ),
        key=lambda item: (-len(item[1]), item[2]),
    )


def _guides_from_path(
    points: list[QVector3D],
    closed: bool,
    arc_number: int,
    line_number: int,
) -> tuple[list[dict], list[dict]]:
    arcs = []
    lines = []
    count = len(points)
    if count < 2:
        return arcs, lines
    arc_edges = set()
    runs = _circular_runs(points, closed)
    if runs:
        circle, run, _error, sweep = runs[0]
        start = points[run[0]]
        first_direction = start - _vector(circle["center"])
        u = _unit(_vector(circle["u"]))
        v = _unit(_vector(circle["v"]))
        a0 = math.atan2(
            QVector3D.dotProduct(first_direction, v),
            QVector3D.dotProduct(first_direction, u),
        )
        full_circle = closed and len(run) == count
        if full_circle:
            sweep = math.copysign(TAU, sweep)
        arc = {
            "uid": str(uuid.uuid4()),
            "name": f"Guide Arc {arc_number}",
            "center": list(circle["center"]),
            "normal": list(circle["normal"]),
            "u": list(circle["u"]),
            "v": list(circle["v"]),
            "radius": float(circle["radius"]),
            "a0": a0,
            "sweep": sweep,
        }
        arcs.append(arc)
        pairs = (
            zip(run, run[1:] + ([run[0]] if full_circle else []))
            if full_circle
            else zip(run, run[1:])
        )
        arc_edges.update(
            (first % count, second % count) for first, second in pairs
        )

    edge_count = count if closed else count - 1
    for index in range(edge_count):
        next_index = (index + 1) % count
        if (index, next_index) in arc_edges:
            continue
        start, end = points[index], points[next_index]
        if (end - start).length() <= 1e-9:
            continue
        lines.append({
            "uid": str(uuid.uuid4()),
            "name": f"Guide Line {line_number + len(lines)}",
            "a": [start.x(), start.y(), start.z()],
            "b": [end.x(), end.y(), end.z()],
        })
    return arcs, lines


def _format_length(value: float, decimals: int) -> str:
    from core import units

    settings = units.model_units()
    unit = settings["length"]
    if unit in {"ft-in", "in-frac", "ft-in-frac"}:
        text = units.format_length(value, unit, decimals, show_unit=True)
    else:
        text = units.format_length(value, unit, decimals, show_unit=True)
    return units.localized(text, settings)


def _format_angle(radians: float, style: str, decimals: int) -> str:
    degrees = abs(math.degrees(radians))
    if style == "decimal":
        return f"{degrees:.{decimals}f}°"
    total_seconds = round(degrees * 3600.0)
    whole_degrees, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{whole_degrees}°{minutes:02d}′{seconds:02d}″"


def _view_basis(view_name: str, camera) -> tuple[QVector3D, QVector3D, QVector3D]:
    views = {
        "top": (QVector3D(0.0, 0.0, 1.0), QVector3D(0.0, 1.0, 0.0)),
        "front": (QVector3D(0.0, -1.0, 0.0), QVector3D(0.0, 0.0, 1.0)),
        "right": (QVector3D(1.0, 0.0, 0.0), QVector3D(0.0, 0.0, 1.0)),
        "left": (QVector3D(-1.0, 0.0, 0.0), QVector3D(0.0, 0.0, 1.0)),
        "back": (QVector3D(0.0, 1.0, 0.0), QVector3D(0.0, 0.0, 1.0)),
        "bottom": (QVector3D(0.0, 0.0, -1.0), QVector3D(0.0, -1.0, 0.0)),
    }
    if view_name == "camera":
        if camera is None:
            raise ValueError("The viewport camera is not available.")
        normal = -_unit(QVector3D(camera.forward()))
        up = QVector3D(camera.up_vector())
        up -= normal * QVector3D.dotProduct(up, normal)
        up = _unit(up)
    else:
        try:
            normal, up = views[view_name]
        except KeyError as exc:
            raise ValueError(f'Unknown dimension view "{view_name}".') from exc
    return _unit(QVector3D.crossProduct(up, normal)), up, normal


def _set_face_color(face, color: tuple[float, float, float]) -> None:
    face.attrs["color"] = list(color)
    face.attrs["back"] = True


def _add_face(
    mesh: Mesh,
    points,
    color: tuple[float, float, float],
    holes=None,
) -> None:
    _set_face_color(mesh.add_face(points, hole_loops=holes or None), color)


def _add_beam(
    mesh: Mesh,
    start: QVector3D,
    end: QVector3D,
    width: float,
    color: tuple[float, float, float],
) -> None:
    axis_vector = end - start
    if axis_vector.length() <= 1e-9:
        return
    axis = _unit(axis_vector)
    up = QVector3D(0.0, 0.0, 1.0)
    side = up - axis * QVector3D.dotProduct(up, axis)
    if side.length() <= 1e-8:
        up = QVector3D(0.0, 1.0, 0.0)
        side = up - axis * QVector3D.dotProduct(up, axis)
    side = _unit(side)
    normal = _unit(QVector3D.crossProduct(axis, side))
    half_side = side * (width * 0.5)
    half_depth = normal * (width * 0.5)
    center = (start + end) * 0.5
    half_length = axis_vector * 0.5
    points = [
        center - half_length - half_side - half_depth,
        center + half_length - half_side - half_depth,
        center + half_length + half_side - half_depth,
        center - half_length + half_side - half_depth,
        center - half_length - half_side + half_depth,
        center + half_length - half_side + half_depth,
        center + half_length + half_side + half_depth,
        center - half_length + half_side + half_depth,
    ]
    for loop in (
        (4, 5, 6, 7), (0, 3, 2, 1), (0, 1, 5, 4),
        (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
    ):
        _add_face(mesh, [points[index] for index in loop], color)


def _add_polyline(
    mesh: Mesh,
    points: list[QVector3D],
    width: float,
    color: tuple[float, float, float],
    closed: bool = False,
) -> None:
    pairs = list(zip(points, points[1:]))
    if closed and len(points) > 2:
        pairs.append((points[-1], points[0]))
    for start, end in pairs:
        _add_beam(mesh, start, end, width, color)


def _add_center_mark(
    mesh: Mesh,
    center: QVector3D,
    right: QVector3D,
    up: QVector3D,
    size: float,
    width: float,
    color: tuple[float, float, float],
) -> None:
    _add_beam(mesh, center - right * size, center + right * size, width, color)
    _add_beam(mesh, center - up * size, center + up * size, width, color)


def _add_text(
    mesh: Mesh,
    text: str,
    center: QVector3D,
    right: QVector3D,
    up: QVector3D,
    normal: QVector3D,
    height: float,
    extrusion: float,
    font_family: str,
    color: tuple[float, float, float],
) -> float:
    from core.text3d import build_text_mesh

    text_mesh = build_text_mesh(
        text, font_family=font_family, height=height,
        thickness=extrusion, bold=True,
    )
    if not text_mesh.vertices:
        raise ValueError(
            f'The font "{font_family or "system default"}" produced no 3D text.'
        )
    xs = [vertex.position.x() for vertex in text_mesh.vertices]
    midpoint = (min(xs) + max(xs)) * 0.5
    width = max(xs) - min(xs)
    depth_axis = -normal
    for face in text_mesh.faces:
        outer = [
            center + right * (point.x() - midpoint)
            + depth_axis * point.y() + up * point.z()
            for point in face.vertices
        ]
        holes = [
            [
                center + right * (point.x() - midpoint)
                + depth_axis * point.y() + up * point.z()
                for point in hole
            ]
            for hole in face.holes
        ]
        _add_face(mesh, outer, color, holes)
    return width


def _node_for(point: QVector3D, nodes: list[QVector3D], tolerance: float) -> int:
    for index, node in enumerate(nodes):
        if (point - node).length() <= tolerance:
            return index
    nodes.append(QVector3D(point))
    return len(nodes) - 1


def _loop_area_and_plane(points: list[QVector3D]) -> dict:
    center = sum(points, QVector3D()) / len(points)
    normal = QVector3D()
    for first, second in zip(points, points[1:] + points[:1]):
        normal.setX(normal.x() + (first.y() - second.y()) * (first.z() + second.z()))
        normal.setY(normal.y() + (first.z() - second.z()) * (first.x() + second.x()))
        normal.setZ(normal.z() + (first.x() - second.x()) * (first.y() + second.y()))
    normal = _unit(normal)
    axis = points[0] - center
    axis -= normal * QVector3D.dotProduct(axis, normal)
    if axis.length() <= 1e-8:
        axis = QVector3D.crossProduct(normal, QVector3D(0.0, 0.0, 1.0))
        if axis.length() <= 1e-8:
            axis = QVector3D.crossProduct(normal, QVector3D(0.0, 1.0, 0.0))
    axis = _unit(axis)
    second_axis = _unit(QVector3D.crossProduct(normal, axis))
    projected = [
        (
            QVector3D.dotProduct(point - center, axis),
            QVector3D.dotProduct(point - center, second_axis),
        )
        for point in points
    ]
    signed_area = sum(
        a[0] * b[1] - b[0] * a[1]
        for a, b in zip(projected, projected[1:] + projected[:1])
    ) * 0.5
    return {
        "center": center,
        "normal": normal,
        "axis": axis,
        "second_axis": second_axis,
        "projected": projected,
        "area": abs(signed_area),
    }


def _point_in_polygon(point, polygon) -> bool:
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = current
        x2, y2 = previous
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def _selected_perimeter_loops(
    lines: list[dict], arcs: list[dict], include_holes: bool
) -> list[dict]:
    primitives = []
    all_points = []
    for line in lines:
        start, end = _vector(line["a"]), _vector(line["b"])
        primitives.append({
            "kind": "line",
            "data": line,
            "start": start,
            "end": end,
            "length": (end - start).length(),
        })
        all_points.extend((start, end))
    for arc in arcs:
        start, end = _arc_start_end(arc)
        if abs(abs(float(arc["sweep"])) - TAU) <= 1e-6:
            points = _arc_points(arc)[:-1]
            primitives.append({
                "kind": "closed-arc",
                "data": arc,
                "points": points,
                "length": _arc_length(arc),
            })
        else:
            primitives.append({
                "kind": "arc",
                "data": arc,
                "start": start,
                "end": end,
                "length": _arc_length(arc),
            })
        all_points.extend(_arc_points(arc))
    if not primitives:
        raise ValueError("Add or convert guide lines and arcs first.")

    scale = max((point.length() for point in all_points), default=1.0)
    tolerance = max(scale * 1e-7, 1e-6)
    nodes: list[QVector3D] = []
    adjacency: dict[int, list[int]] = {}
    graph_edges = []
    loops = []
    for primitive in primitives:
        if primitive["kind"] == "closed-arc":
            points = primitive["points"]
            loops.append({
                "points": points,
                "length": primitive["length"],
                "uids": [primitive["data"]["uid"]],
            })
            continue
        first = _node_for(primitive["start"], nodes, tolerance)
        second = _node_for(primitive["end"], nodes, tolerance)
        edge_index = len(graph_edges)
        graph_edges.append((first, second, primitive))
        adjacency.setdefault(first, []).append(edge_index)
        adjacency.setdefault(second, []).append(edge_index)

    unseen = set(range(len(graph_edges)))
    while unseen:
        seed = next(iter(unseen))
        component = set()
        stack = [seed]
        while stack:
            edge_index = stack.pop()
            if edge_index in component:
                continue
            component.add(edge_index)
            first, second, _primitive = graph_edges[edge_index]
            for node in (first, second):
                stack.extend(adjacency[node])
        unseen -= component
        component_nodes = {
            node
            for edge_index in component
            for node in graph_edges[edge_index][:2]
        }
        if any(
            sum(edge in component for edge in adjacency[node]) != 2
            for node in component_nodes
        ):
            raise ValueError(
                "Perimeter guides must form closed loops: every endpoint must "
                "connect to exactly two selected guides."
            )

        remaining = set(component)
        while remaining:
            first_edge = next(iter(remaining))
            start_node = graph_edges[first_edge][0]
            current_node = start_node
            ordered_points = []
            used = []
            while True:
                choices = [
                    edge for edge in adjacency[current_node]
                    if edge in remaining
                ]
                if not choices:
                    raise ValueError("A selected guide loop is not closed.")
                edge_index = choices[0]
                remaining.remove(edge_index)
                used.append(edge_index)
                node_a, node_b, primitive = graph_edges[edge_index]
                reverse = current_node == node_b
                if primitive["kind"] == "line":
                    segment = [primitive["end"], primitive["start"]] \
                        if reverse else [primitive["start"], primitive["end"]]
                else:
                    segment = _arc_points(primitive["data"], reverse=reverse)
                if ordered_points:
                    ordered_points.extend(segment[1:])
                else:
                    ordered_points.extend(segment)
                current_node = node_a if reverse else node_b
                if current_node == start_node:
                    break
            if len(ordered_points) > 1 and (
                ordered_points[-1] - ordered_points[0]
            ).length() <= tolerance:
                ordered_points.pop()
            loops.append({
                "points": ordered_points,
                "length": sum(graph_edges[index][2]["length"] for index in used),
                "uids": [
                    graph_edges[index][2]["data"]["uid"] for index in used
                ],
            })

    if include_holes:
        return loops
    loop_geometry = [_loop_area_and_plane(loop["points"]) for loop in loops]
    included = []
    for index, geometry in enumerate(loop_geometry):
        depth = 0
        point = geometry["projected"][0]
        for other_index, other in enumerate(loop_geometry):
            if index == other_index or other["area"] <= geometry["area"]:
                continue
            if abs(QVector3D.dotProduct(
                geometry["normal"], other["normal"]
            )) < 1.0 - 1e-5:
                continue
            plane_distance = abs(QVector3D.dotProduct(
                geometry["center"] - other["center"], other["normal"]
            ))
            if plane_distance > tolerance * 10.0:
                continue
            other_axis = other["axis"]
            other_v = other["second_axis"]
            relative = geometry["center"] - other["center"]
            sample_world = geometry["center"] \
                + geometry["axis"] * point[0] \
                + geometry["second_axis"] * point[1]
            sample_relative = sample_world - other["center"]
            projected_point = (
                QVector3D.dotProduct(sample_relative, other_axis),
                QVector3D.dotProduct(sample_relative, other_v),
            )
            if _point_in_polygon(projected_point, other["projected"]):
                depth += 1
        if depth % 2 == 0:
            included.append(loops[index])
    if not included:
        raise ValueError("No outer closed perimeter was found.")
    return included


def _line_intersection(lines: list[dict]) -> tuple[QVector3D, QVector3D, QVector3D]:
    if len(lines) != 2 or lines[0]["uid"] == lines[1]["uid"]:
        raise ValueError("Choose two different guide lines for an angle.")
    p1, p2 = _vector(lines[0]["a"]), _vector(lines[0]["b"])
    q1, q2 = _vector(lines[1]["a"]), _vector(lines[1]["b"])
    u, v = p2 - p1, q2 - q1
    a = QVector3D.dotProduct(u, u)
    b = QVector3D.dotProduct(u, v)
    c = QVector3D.dotProduct(v, v)
    w = p1 - q1
    d = QVector3D.dotProduct(u, w)
    e = QVector3D.dotProduct(v, w)
    denominator = a * c - b * b
    if a <= 1e-12 or c <= 1e-12 or denominator <= 1e-12:
        raise ValueError("The selected guide lines are parallel or degenerate.")
    s = (b * e - c * d) / denominator
    t = (a * e - b * d) / denominator
    on_first, on_second = p1 + u * s, q1 + v * t
    if (on_first - on_second).length() > max(u.length(), v.length(), 1.0) * 1e-5:
        raise ValueError("The selected guide lines do not intersect in 3D.")
    vertex = (on_first + on_second) * 0.5

    def outward_direction(start, end):
        direction = start - vertex if (start - vertex).length() >= \
            (end - vertex).length() else end - vertex
        return _unit(direction)

    return vertex, outward_direction(p1, p2), outward_direction(q1, q2)


def _angle_arc_points(
    vertex: QVector3D,
    first: QVector3D,
    second: QVector3D,
    radius: float,
) -> tuple[list[QVector3D], float]:
    angle = math.acos(max(-1.0, min(1.0, QVector3D.dotProduct(first, second))))
    if angle <= 1e-8 or abs(math.pi - angle) <= 1e-8:
        raise ValueError("The selected guides do not define a visible angle.")
    axis = _unit(QVector3D.crossProduct(first, second))
    start_axis = first
    steps = max(12, int(math.ceil(angle / (math.pi / 48.0))))
    points = []
    for index in range(steps + 1):
        current = first * math.cos(angle * index / steps) \
            + QVector3D.crossProduct(axis, start_axis) \
            * math.sin(angle * index / steps) \
            + axis * QVector3D.dotProduct(axis, start_axis) \
            * (1.0 - math.cos(angle * index / steps))
        points.append(vertex + _unit(current) * radius)
    return points, angle


class _InsertGroupsCommand(Command):
    def __init__(
        self, scene, groups: list[Group], select_created: bool = True
    ):
        self._groups = groups
        self._selection = list(scene.selection)
        self._select_created = select_created

    def do(self, scene) -> None:
        scene.groups.extend(self._groups)
        if self._select_created:
            scene.selection.clear()
            for group in self._groups:
                scene.selection.add(group)
        scene.version = getattr(scene, "version", 0) + 1

    def undo(self, scene) -> None:
        for group in self._groups:
            if group in scene.groups:
                scene.groups.remove(group)
        scene.selection.clear()
        for item in self._selection:
            scene.selection.add(item)
        scene.version = getattr(scene, "version", 0) + 1


class _ReplaceGroupsCommand(Command):
    def __init__(self, scene, replacements: list[tuple]):
        self._replacements = replacements
        self._selection = list(scene.selection)

    @staticmethod
    def _apply(scene, replacements, new_index: int) -> None:
        containers = {}
        for parent, old, new, index in replacements:
            containers.setdefault(id(parent), (parent, [], []))
            containers[id(parent)][1].append(old)
            containers[id(parent)][2].append((index, new))
        for parent, olds, news in containers.values():
            for old in olds:
                if old in parent:
                    parent.remove(old)
            for index, new in sorted(news, key=lambda item: item[0]):
                parent.insert(min(index, len(parent)), new)
        scene.selection.clear()
        for _parent, old, new, _index in replacements:
            if not new_index:
                scene.selection.add(old)
                continue
            data = (new.ext or {}).get(KEY, {})
            options = data.get("options", {})
            if options.get("show_frame", True):
                scene.selection.add(new)
        scene.version = getattr(scene, "version", 0) + 1

    def do(self, scene) -> None:
        self._apply(scene, self._replacements, 1)

    def undo(self, scene) -> None:
        reverse = [
            (parent, new, old, index)
            for parent, old, new, index in self._replacements
        ]
        self._apply(scene, reverse, 0)
        scene.selection.clear()
        for item in self._selection:
            scene.selection.add(item)


class _GuideDialog(QDialog):
    def __init__(self, kind: str, parent=None, guide: dict | None = None):
        super().__init__(parent)
        self.kind = kind
        self.guide = guide
        self.setWindowTitle(
            f"{'Edit' if guide else 'Add'} "
            f"{'Arc' if kind == 'arc' else 'Guide Line'}"
        )
        self._fields = {}
        self._rows = {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.name = QLineEdit(
            str(guide.get("name", "Arc" if kind == "arc" else "Line"))
            if guide else ("Arc" if kind == "arc" else "Line")
        )
        form.addRow("Name:", self.name)

        def vector_row(key, label, values):
            row = QWidget(self)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            fields = []
            for value in values:
                field = QDoubleSpinBox(row)
                field.setRange(-100000.0, 100000.0)
                field.setDecimals(5)
                field.setValue(float(value))
                row_layout.addWidget(field)
                fields.append(field)
            form.addRow(label, row)
            self._fields[key] = fields
            self._rows[key] = (form.labelForField(row), row)

        if kind == "line":
            vector_row("a", "Start point X,Y,Z (m):",
                       guide.get("a", [0.0, 0.0, 0.0]) if guide else [0, 0, 0])
            vector_row("b", "End point X,Y,Z (m):",
                       guide.get("b", [1.0, 0.0, 0.0]) if guide else [1, 0, 0])
        else:
            self.arc_mode = QComboBox(self)
            self.arc_mode.addItem("Center, radius, start/end angles", "center")
            self.arc_mode.addItem("Three points (start, through, end)", "points")
            form.addRow("Construction:", self.arc_mode)
            center = guide.get("center", [0.0, 0.0, 0.0]) if guide else [0, 0, 0]
            normal = guide.get("normal", [0.0, 0.0, 1.0]) if guide else [0, 0, 1]
            vector_row("center", "Center X,Y,Z (m):", center)
            vector_row("normal", "Plane normal X,Y,Z:", normal)
            self.radius = QDoubleSpinBox(self)
            self.radius.setRange(0.000001, 100000.0)
            self.radius.setDecimals(5)
            self.radius.setValue(
                float(guide.get("radius", 1.0)) if guide else 1.0
            )
            form.addRow("Radius (m):", self.radius)
            self.start_angle = QDoubleSpinBox(self)
            self.start_angle.setRange(-360.0, 360.0)
            self.start_angle.setDecimals(4)
            self.end_angle = QDoubleSpinBox(self)
            self.end_angle.setRange(-360.0, 360.0)
            self.end_angle.setDecimals(4)
            start = math.degrees(float(guide.get("a0", 0.0))) if guide else 0.0
            sweep = math.degrees(float(guide.get("sweep", math.pi / 2))) \
                if guide else 90.0
            self.start_angle.setValue(start)
            self.end_angle.setValue(max(-360.0, min(360.0, start + sweep)))
            form.addRow("Start angle (degrees):", self.start_angle)
            form.addRow("End angle (degrees):", self.end_angle)
            self._rows["center_arc"] = (
                form.labelForField(self.radius), self.radius
            )
            self._rows["start_angle"] = (
                form.labelForField(self.start_angle), self.start_angle
            )
            self._rows["end_angle"] = (
                form.labelForField(self.end_angle), self.end_angle
            )
            for key, label in (
                ("start", "Arc start X,Y,Z (m):"),
                ("through", "Point on arc X,Y,Z (m):"),
                ("end", "Arc end X,Y,Z (m):"),
            ):
                defaults = guide.get(key) if guide else None
                if defaults is None and guide:
                    fraction = {
                        "start": 0.0,
                        "through": 0.5,
                        "end": 1.0,
                    }[key]
                    point = _point_on_arc(
                        guide,
                        float(guide["a0"]) + float(guide["sweep"]) * fraction,
                    )
                    defaults = [point.x(), point.y(), point.z()]
                if defaults is None:
                    defaults = {
                        "start": [1.0, 0.0, 0.0],
                        "through": [0.7071, 0.7071, 0.0],
                        "end": [0.0, 1.0, 0.0],
                    }[key]
                vector_row(key, label, defaults)
            self.arc_mode.currentIndexChanged.connect(self._update_arc_fields)
            self._update_arc_fields()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_arc_fields(self):
        center_mode = self.arc_mode.currentData() == "center"
        for key in ("center", "normal", "center_arc", "start_angle", "end_angle"):
            label, widget = self._rows[key]
            label.setVisible(center_mode)
            widget.setVisible(center_mode)
        for key in ("start", "through", "end"):
            label, widget = self._rows[key]
            label.setVisible(not center_mode)
            widget.setVisible(not center_mode)

    def _values(self, key) -> list[float]:
        return [float(field.value()) for field in self._fields[key]]

    def build(self) -> dict:
        name = self.name.text().strip()
        if self.kind == "line":
            start, end = _vector(self._values("a")), _vector(self._values("b"))
            if (end - start).length() <= 1e-9:
                raise ValueError("Guide-line endpoints must be different.")
            return {
                "uid": self.guide["uid"] if self.guide else str(uuid.uuid4()),
                "name": name or "Line",
                "a": [start.x(), start.y(), start.z()],
                "b": [end.x(), end.y(), end.z()],
            }
        if self.arc_mode.currentData() == "center":
            arc = _arc_from_center(
                name,
                _vector(self._values("center")),
                _vector(self._values("normal")),
                self.radius.value(),
                self.start_angle.value(),
                self.end_angle.value(),
            )
        else:
            arc = _arc_from_three_points(
                name,
                _vector(self._values("start")),
                _vector(self._values("through")),
                _vector(self._values("end")),
            )
        if self.guide:
            arc["uid"] = self.guide["uid"]
        return arc


class _MoveGuideDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Move Guide")
        form = QFormLayout(self)
        self.delta = []
        for label in ("X", "Y", "Z"):
            field = QDoubleSpinBox(self)
            field.setRange(-100000.0, 100000.0)
            field.setDecimals(5)
            field.setSuffix(" m")
            self.delta.append(field)
            form.addRow(f"{label} offset:", field)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


def _adjust_arc_with_guide_line(
    arc: dict, line: dict, operation: str
) -> tuple[dict, list[tuple[QVector3D, QVector3D]], float]:
    radius = float(arc["radius"])
    sweep = float(arc["sweep"])
    if radius <= 1e-9 or abs(sweep) <= 1e-9:
        raise ValueError("The selected guide arc is degenerate.")
    if abs(sweep) >= TAU - 1e-6:
        raise ValueError("Choose a partial guide arc, not a full circle.")

    tolerance = max(1e-5, radius * 0.005)
    angle_tolerance = max(1e-6, tolerance / radius)
    center = _vector(arc["center"])
    normal = _unit(_vector(arc["normal"]))
    u = _unit(_vector(arc["u"]))
    v = _unit(_vector(arc["v"]))

    def point_angle(point: QVector3D) -> float:
        relative = point - center
        plane_offset = QVector3D.dotProduct(relative, normal)
        if abs(plane_offset) > tolerance:
            raise ValueError(
                "The guide-line endpoint must lie in the arc's plane."
            )
        projected = relative - normal * plane_offset
        if projected.length() <= tolerance:
            raise ValueError(
                "The guide-line endpoint is too close to the arc center."
            )
        return math.atan2(
            QVector3D.dotProduct(projected, v),
            QVector3D.dotProduct(projected, u),
        )

    start_angle = float(arc["a0"])
    end_angle = start_angle + sweep
    start_point = _point_on_arc(arc, start_angle)
    end_point = _point_on_arc(arc, end_angle)
    line_points = (_vector(line["a"]), _vector(line["b"]))
    matches = [
        (side, line_point)
        for line_point in line_points
        for side, arc_point in (("start", start_point), ("end", end_point))
        if (line_point - arc_point).length() <= tolerance
    ]
    if len(matches) != 1:
        raise ValueError(
            "The guide line must meet exactly one endpoint of the selected arc."
        )

    side, attached_point = matches[0]
    target_point = (
        line_points[1]
        if (line_points[0] - attached_point).length() <= tolerance
        else line_points[0]
    )
    target_angle = point_angle(target_point)
    direction = 1.0 if sweep > 0.0 else -1.0
    extent = abs(sweep)

    def directed_delta(first: float, second: float) -> float:
        if direction > 0.0:
            return (second - first) % TAU
        return -((first - second) % TAU)

    position = abs(directed_delta(start_angle, target_angle))
    if operation == "add":
        if (
            position <= angle_tolerance
            or abs(position - extent) <= angle_tolerance
        ):
            raise ValueError("The line does not extend the selected arc.")
        if position < extent:
            raise ValueError(
                "The line endpoint is already inside the arc; use Arc - Line."
            )
        extension = (
            directed_delta(end_angle, target_angle)
            if side == "end"
            else directed_delta(target_angle, start_angle)
        )
        new_a0 = target_angle if side == "start" else start_angle
        new_sweep = sweep + extension
        if abs(new_sweep) > TAU + angle_tolerance:
            raise ValueError("Absorbing this line would exceed a full circle.")
        if abs(new_sweep) > TAU:
            new_sweep = direction * TAU
        adjusted = dict(arc, a0=new_a0, sweep=new_sweep)
        new_start = _point_on_arc(adjusted, new_a0)
        new_end = _point_on_arc(adjusted, new_a0 + new_sweep)
        snaps = [
            (attached_point, start_point if side == "start" else end_point),
            (target_point, new_start if side == "start" else new_end),
        ]
    elif operation == "subtract":
        if (
            position <= angle_tolerance
            or position >= extent - angle_tolerance
        ):
            raise ValueError(
                "The line must connect an arc endpoint to a point inside the arc."
            )
        signed_position = directed_delta(start_angle, target_angle)
        new_a0 = target_angle if side == "start" else start_angle
        new_sweep = (
            sweep - signed_position
            if side == "start"
            else signed_position
        )
        adjusted = dict(arc, a0=new_a0, sweep=new_sweep)
        new_boundary = _point_on_arc(
            adjusted, new_a0 if side == "start" else new_a0 + new_sweep
        )
        snaps = [(target_point, new_boundary)]
    else:
        raise ValueError(f'Unknown arc/line operation "{operation}".')

    return adjusted, snaps, tolerance


class _GuidePanel(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setMinimumWidth(310)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Convert selected model edges/faces to guides, then manage them here."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.show_guides = QCheckBox("Show guides in viewport", self)
        self.show_guides.setChecked(True)
        self.show_guides.toggled.connect(
            lambda _visible: self.app.viewport.update()
        )
        layout.addWidget(self.show_guides)
        self.items = QListWidget(self)
        self.items.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
        )
        self.items.itemSelectionChanged.connect(
            lambda: self.app.viewport.update()
        )
        layout.addWidget(self.items, 1)
        row = QHBoxLayout()
        for text, callback in (
            ("Edit", self.edit_selected),
            ("Rename", self.rename_selected),
            ("Move", self.move_selected),
        ):
            button = QPushButton(text, self)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        for text, operation, tip in (
            (
                "Arc + Line",
                "add",
                "Extend the selected arc and absorb the selected guide line.",
            ),
            (
                "Arc - Line",
                "subtract",
                "Trim the selected arc and keep the guide line as an edge.",
            ),
        ):
            button = QPushButton(text, self)
            button.setToolTip(tip)
            button.clicked.connect(
                lambda _checked=False, action=operation:
                    self.adjust_selected_arc(action)
            )
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        for text, callback in (
            ("Duplicate", self.duplicate_selected),
            ("Delete", self.delete_selected),
            ("Delete all", self.delete_all),
            (
                "Convert selection",
                lambda: convert_selected_edges_to_guides(self.app, self),
            ),
        ):
            button = QPushButton(text, self)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        on_document_changed = getattr(app, "on_document_changed", None)
        if callable(on_document_changed):
            on_document_changed(self.refresh)
        self.refresh()

    def _selected(self):
        item = self.items.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selected_guides(self) -> list[tuple[str, str]]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.items.selectedItems()
        ]

    def adjust_selected_arc(self, operation: str) -> None:
        selected = self.selected_guides()
        arc_uids = [uid for kind, uid in selected if kind == "arc"]
        line_uids = [uid for kind, uid in selected if kind == "line"]
        if len(selected) != 2 or len(arc_uids) != 1 or len(line_uids) != 1:
            QMessageBox.information(
                self,
                "Adjust Guide Arc",
                "Select exactly one guide arc and one guide line.",
            )
            return

        state = _guide_snapshot(self.app)
        arc = next(
            (item for item in state["arcs"] if item["uid"] == arc_uids[0]),
            None,
        )
        line = next(
            (item for item in state["lines"] if item["uid"] == line_uids[0]),
            None,
        )
        if arc is None or line is None:
            QMessageBox.warning(
                self, "Adjust Guide Arc", "A selected guide no longer exists."
            )
            return

        try:
            adjusted, snaps, tolerance = _adjust_arc_with_guide_line(
                arc, line, operation
            )
            state["arcs"][:] = [
                adjusted if item["uid"] == arc["uid"] else item
                for item in state["arcs"]
            ]
            for guide in state["lines"]:
                if operation == "add" and guide["uid"] == line["uid"]:
                    continue
                for key in ("a", "b"):
                    point = _vector(guide[key])
                    for old_point, new_point in snaps:
                        if (point - old_point).length() <= tolerance:
                            guide[key] = [
                                new_point.x(),
                                new_point.y(),
                                new_point.z(),
                            ]
                            break
            if operation == "add":
                state["lines"][:] = [
                    item
                    for item in state["lines"]
                    if item["uid"] != line["uid"]
                ]
            _store_guides(self.app, state)
            self.refresh()
            self.status.setText(
                "Arc updated; guide line deleted."
                if operation == "add"
                else "Arc shortened; guide line kept as an edge."
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Adjust Guide Arc", str(exc))

    def refresh(self):
        state = _guide_snapshot(self.app)
        selected = set(self.selected_guides())
        current = self._selected()
        self.items.clear()
        for kind, entries in (("arc", state["arcs"]), ("line", state["lines"])):
            for guide in entries:
                label = guide.get("name", kind.title())
                if kind == "arc":
                    label += f"  R={float(guide['radius']):.4f} m"
                else:
                    label += f"  L={(_vector(guide['b']) - _vector(guide['a'])).length():.4f} m"
                item = QListWidgetItem(f"{kind.title()}: {label}")
                item.setData(Qt.ItemDataRole.UserRole, (kind, guide["uid"]))
                self.items.addItem(item)
                selection = (kind, guide["uid"])
                item.setSelected(selection in selected)
                if selection == current:
                    self.items.setCurrentItem(item)
        if self.items.currentItem() is None and selected:
            for index in range(self.items.count()):
                item = self.items.item(index)
                if item.data(Qt.ItemDataRole.UserRole) in selected:
                    self.items.setCurrentItem(item)
                    break
        self.status.setText(
            f"{len(state['lines'])} guide line(s), {len(state['arcs'])} arc(s)."
        )
        self.app.viewport.update()

    def _add_or_edit(self, kind, guide=None):
        dialog = _GuideDialog(kind, self, guide)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            updated = dialog.build()
            state = _guide_snapshot(self.app)
            collection = state["arcs"] if kind == "arc" else state["lines"]
            if guide:
                collection[:] = [
                    updated if item["uid"] == guide["uid"] else item
                    for item in collection
                ]
            else:
                collection.append(updated)
            _store_guides(self.app, state)
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Special Dimension Guides", str(exc))

    def add_line(self):
        self._add_or_edit("line")

    def add_arc(self):
        self._add_or_edit("arc")

    def edit_selected(self):
        if len(self.selected_guides()) != 1:
            QMessageBox.information(
                self,
                "Special Dimension Guides",
                "Select exactly one guide to edit.",
            )
            return
        state = _guide_snapshot(self.app)
        try:
            kind, guide = self._get_selected_guide(state)
        except ValueError as exc:
            QMessageBox.information(self, "Special Dimension Guides", str(exc))
            return
        self._add_or_edit(kind, guide)

    def rename_selected(self) -> None:
        selected = self.selected_guides()
        if len(selected) != 1:
            QMessageBox.information(
                self,
                "Special Dimension Guides",
                "Select exactly one guide to rename.",
            )
            return

        state = _guide_snapshot(self.app)
        kind, uid = selected[0]
        collection = state["arcs"] if kind == "arc" else state["lines"]
        guide = next((item for item in collection if item["uid"] == uid), None)
        if guide is None:
            QMessageBox.warning(
                self,
                "Special Dimension Guides",
                "The selected guide no longer exists.",
            )
            return

        default_name = guide.get("name") or ("Arc" if kind == "arc" else "Line")
        name, accepted = QInputDialog.getText(
            self,
            "Rename Guide",
            "Name:",
            QLineEdit.EchoMode.Normal,
            str(default_name),
        )
        if not accepted:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(
                self,
                "Rename Guide",
                "Guide names cannot be empty.",
            )
            return

        guide["name"] = name
        try:
            _store_guides(self.app, state)
        except ValueError as exc:
            QMessageBox.warning(self, "Rename Guide", str(exc))
            return
        self.refresh()
        self.status.setText("Guide renamed.")

    def _get_selected_guide(self, state):
        selection = self._selected()
        if selection is None:
            raise ValueError("Select a guide first.")
        kind, uid = selection
        collection = state["arcs"] if kind == "arc" else state["lines"]
        guide = next((item for item in collection if item["uid"] == uid), None)
        if guide is None:
            raise ValueError("The selected guide no longer exists.")
        return kind, guide

    def move_selected(self):
        state = _guide_snapshot(self.app)
        selected = self.selected_guides()
        if not selected:
            QMessageBox.information(
                self, "Special Dimension Guides", "Select one or more guides first."
            )
            return
        dialog = _MoveGuideDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        delta = QVector3D(*(field.value() for field in dialog.delta))
        for kind, uid in selected:
            collection = state["arcs"] if kind == "arc" else state["lines"]
            guide = next((item for item in collection if item["uid"] == uid), None)
            if guide is None:
                continue
            keys = ("center",) if kind == "arc" else ("a", "b")
            for key in keys:
                point = _vector(guide[key]) + delta
                guide[key] = [point.x(), point.y(), point.z()]
        _store_guides(self.app, state)
        self.refresh()

    def duplicate_selected(self):
        state = _guide_snapshot(self.app)
        selected = self.selected_guides()
        if not selected:
            QMessageBox.information(
                self, "Special Dimension Guides", "Select one or more guides first."
            )
            return
        for kind, uid in selected:
            collection = state["arcs"] if kind == "arc" else state["lines"]
            guide = next((item for item in collection if item["uid"] == uid), None)
            if guide is None:
                continue
            copy = dict(guide)
            copy["uid"] = str(uuid.uuid4())
            copy["name"] = f"{guide.get('name', kind.title())} copy"
            collection.append(copy)
        _store_guides(self.app, state)
        self.refresh()

    def delete_selected(self):
        state = _guide_snapshot(self.app)
        selected = set(self.selected_guides())
        if not selected:
            QMessageBox.information(
                self, "Special Dimension Guides", "Select one or more guides first."
            )
            return
        state["arcs"][:] = [
            item for item in state["arcs"]
            if ("arc", item["uid"]) not in selected
        ]
        state["lines"][:] = [
            item for item in state["lines"]
            if ("line", item["uid"]) not in selected
        ]
        _store_guides(self.app, state)
        self.refresh()

    def delete_all(self):
        state = _guide_snapshot(self.app)
        if not state["arcs"] and not state["lines"]:
            return
        answer = QMessageBox.question(
            self,
            "Special Dimension Guides",
            "Delete all guide lines and arcs?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        _store_guides(self.app, {"arcs": [], "lines": []})
        self.refresh()


def _draw_guides(app, viewport, painter, panel=None) -> None:
    if panel is not None and not panel.show_guides.isChecked():
        return
    state = _guide_snapshot(app)
    if not state["lines"] and not state["arcs"]:
        return
    selected = set(panel.selected_guides()) if panel is not None else set()
    world_to_pixel = getattr(viewport, "_world_to_pixel", None)
    if not callable(world_to_pixel):
        return
    painter.save()
    try:
        for line in state["lines"]:
            is_selected = ("line", line["uid"]) in selected
            color = QColor.fromRgbF(1.0, 0.85, 0.25) if is_selected else QColor.fromRgbF(0.35, 0.95, 0.55)
            painter.setPen(QPen(color, 2.8 if is_selected else 1.6, Qt.PenStyle.DashDotLine))
            start = world_to_pixel(_vector(line["a"]))
            end = world_to_pixel(_vector(line["b"]))
            if start is not None and end is not None:
                painter.drawLine(QPointF(*start), QPointF(*end))
        for arc in state["arcs"]:
            is_selected = ("arc", arc["uid"]) in selected
            color = QColor.fromRgbF(1.0, 0.85, 0.25) if is_selected else QColor.fromRgbF(0.20, 0.75, 0.95)
            painter.setPen(QPen(color, 2.8 if is_selected else 1.6, Qt.PenStyle.DashLine))
            path = QPainterPath()
            started = False
            for point in _arc_points(arc):
                screen = world_to_pixel(point)
                if screen is None:
                    started = False
                    continue
                if started:
                    path.lineTo(QPointF(*screen))
                else:
                    path.moveTo(QPointF(*screen))
                    started = True
            painter.drawPath(path)
            center = world_to_pixel(_vector(arc["center"]))
            if center is not None:
                cx, cy = center
                painter.drawLine(QPointF(cx - 4, cy), QPointF(cx + 4, cy))
                painter.drawLine(QPointF(cx, cy - 4), QPointF(cx, cy + 4))
    finally:
        painter.restore()


def _dimension_text_hit(scene, viewport, x: float, y: float):
    world_to_pixel = getattr(viewport, "_world_to_pixel", None)
    if not callable(world_to_pixel):
        return None

    def walk(container):
        for index, group in enumerate(list(container)):
            yield group, container, index
            yield from walk(group.children or [])

    candidates = []
    for group, parent, index in walk(scene.groups):
        data = (group.ext or {}).get(KEY)
        if not isinstance(data, dict) or "label_center" not in data:
            continue
        options = data.get("options", {})
        try:
            center = _vector(data["label_center"])
            right = _unit(_vector(data["label_right"]))
            up = _unit(_vector(data["label_up"]))
            half_width = float(data["label_width"]) * 0.5
            height = float(options["height"])
        except (KeyError, TypeError, ValueError):
            continue
        corners = (
            center - right * half_width,
            center + right * half_width,
            center - right * half_width + up * height,
            center + right * half_width + up * height,
        )
        pixels = [world_to_pixel(point) for point in corners]
        if any(point is None for point in pixels):
            continue
        left = min(float(point[0]) for point in pixels) - 6.0
        right_edge = max(float(point[0]) for point in pixels) + 6.0
        top = min(float(point[1]) for point in pixels) - 6.0
        bottom = max(float(point[1]) for point in pixels) + 6.0
        if left <= x <= right_edge and top <= y <= bottom:
            center_screen = world_to_pixel(center + up * (height * 0.5))
            distance = (
                math.hypot(x - center_screen[0], y - center_screen[1])
                if center_screen is not None
                else 0.0
            )
            candidates.append((distance, group, parent, index, data))
    if not candidates:
        return None
    _, group, parent, index, data = min(candidates, key=lambda item: item[0])
    return group, parent, index, data


class _MoveDimensionTextTool(Tool):
    name = "Move Special Dimension Text"
    shortcut = None
    description = "Drag a generated dimension label to reposition it."
    uses_snap = False

    def __init__(self, app):
        self.app = app
        self._target = None
        self._start_world = None
        self._last_world = None
        self._start_screen = None
        self._last_screen = None

    def on_activate(self, viewport) -> None:
        self._forget()

    def on_deactivate(self, viewport) -> None:
        self._forget()

    def on_click(self, ctx: ToolContext) -> None:
        self._target = _dimension_text_hit(
            ctx.viewport.scene, ctx.viewport, ctx.screen.x(), ctx.screen.y()
        )
        if self._target is None:
            self._forget()
            ctx.viewport.flash_status(
                "Click on a special-dimension text label to move it.", 3000
            )
            return
        self._start_world = QVector3D(ctx.world)
        self._last_world = QVector3D(ctx.world)
        self._start_screen = (ctx.screen.x(), ctx.screen.y())
        self._last_screen = self._start_screen
        ctx.viewport.flash_status("Drag to reposition the dimension text.", 3000)

    def on_hover(self, ctx: ToolContext) -> None:
        if self._target is None:
            return
        self._last_world = QVector3D(ctx.world)
        self._last_screen = (ctx.screen.x(), ctx.screen.y())

    def on_release(self, viewport) -> None:
        target = self._target
        start = self._start_world
        last = self._last_world
        start_screen = self._start_screen
        last_screen = self._last_screen
        self._forget()
        if (
            target is None
            or start is None
            or last is None
            or start_screen is None
            or last_screen is None
            or math.hypot(
                last_screen[0] - start_screen[0],
                last_screen[1] - start_screen[1],
            ) < 2.0
        ):
            return
        delta = last - start
        if delta.length() <= 1e-8:
            return
        try:
            _move_dimension_text(self.app, viewport, target, delta)
        except Exception as exc:
            log.exception("Could not move special-dimension text")
            QMessageBox.critical(
                None, "Move Special Dimension Text", str(exc)
            )

    def on_cancel(self, viewport) -> None:
        self._forget()
        viewport.update()

    def _forget(self) -> None:
        self._target = None
        self._start_world = None
        self._last_world = None
        self._start_screen = None
        self._last_screen = None


class _DimensionDialog(QDialog):
    def __init__(self, parent, state: dict, scene, selected_guides=()):
        from core import units

        super().__init__(parent)
        self.setWindowTitle("Special 3D Dimensions")
        self._guides = state
        unit = units.model_unit()
        self._unit = unit
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.mode = QComboBox(self)
        for label, value in (
            ("Perimeter", MODE_PERIMETER),
            ("Arc length", MODE_ARC_LENGTH),
            ("Arc radius", MODE_ARC_RADIUS),
            ("Circle radius", MODE_CIRCLE_RADIUS),
            ("Circle diameter", MODE_CIRCLE_DIAMETER),
            ("Angle between guide lines", MODE_LINE_ANGLE),
            ("Arc sweep angle", MODE_ARC_ANGLE),
        ):
            self.mode.addItem(label, value)
        form.addRow("Dimension:", self.mode)
        self._prefix_values = {}
        self._prefix_mode = self.mode.currentData()
        self.prefix = QLineEdit(
            _default_dimension_prefix(self._prefix_mode), self
        )
        form.addRow("Text prefix:", self.prefix)

        self.guide_list = QListWidget(self)
        self.guide_list.setMinimumHeight(130)
        selected_guides = set(selected_guides)
        for kind, entries in (("line", state["lines"]), ("arc", state["arcs"])):
            for guide in entries:
                label = guide.get("name", kind.title())
                if kind == "arc" and abs(abs(float(guide["sweep"])) - TAU) <= 1e-6:
                    label += " (circle)"
                item = QListWidgetItem(f"{kind.title()}: {label}", self.guide_list)
                guide_ref = (kind, guide["uid"])
                item.setData(Qt.ItemDataRole.UserRole, guide_ref)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked
                    if guide_ref in selected_guides
                    else Qt.CheckState.Unchecked
                )
        form.addRow("Perimeter guides:", self.guide_list)

        self.arc_combo = QComboBox(self)
        self.circle_combo = QComboBox(self)
        for arc in state["arcs"]:
            self.arc_combo.addItem(arc.get("name", "Arc"), arc["uid"])
            if abs(abs(float(arc["sweep"])) - TAU) <= 1e-6:
                self.circle_combo.addItem(arc.get("name", "Circle"), arc["uid"])
        form.addRow("Arc:", self.arc_combo)
        form.addRow("Circle:", self.circle_combo)

        self.first_line = QComboBox(self)
        self.second_line = QComboBox(self)
        for line in state["lines"]:
            self.first_line.addItem(line.get("name", "Guide line"), line["uid"])
            self.second_line.addItem(line.get("name", "Guide line"), line["uid"])
        selected_arcs = [
            arc["uid"]
            for arc in state["arcs"]
            if ("arc", arc["uid"]) in selected_guides
        ]
        selected_circles = [
            arc["uid"]
            for arc in state["arcs"]
            if ("arc", arc["uid"]) in selected_guides
            and abs(abs(float(arc["sweep"])) - TAU) <= 1e-6
        ]
        selected_lines = [
            line["uid"]
            for line in state["lines"]
            if ("line", line["uid"]) in selected_guides
        ]
        if selected_arcs:
            self.arc_combo.setCurrentIndex(
                self.arc_combo.findData(selected_arcs[0])
            )
        if selected_circles:
            self.circle_combo.setCurrentIndex(
                self.circle_combo.findData(selected_circles[0])
            )
        if selected_lines:
            self.first_line.setCurrentIndex(
                self.first_line.findData(selected_lines[0])
            )
            if len(selected_lines) > 1:
                self.second_line.setCurrentIndex(
                    self.second_line.findData(selected_lines[1])
                )
            else:
                self.second_line.setCurrentIndex(-1)
        form.addRow("First guide line:", self.first_line)
        form.addRow("Second guide line:", self.second_line)

        self.include_holes = QCheckBox("Include hole perimeters", self)
        self.include_holes.setChecked(True)
        form.addRow("", self.include_holes)
        self.angle_style = QComboBox(self)
        self.angle_style.addItem("Decimal degrees", "decimal")
        self.angle_style.addItem("Degrees-minutes-seconds", "dms")
        form.addRow("Angle format:", self.angle_style)
        self.decimals = QSpinBox(self)
        self.decimals.setRange(0, 6)
        self.decimals.setValue(2)
        form.addRow("Decimal places:", self.decimals)

        self.font = QFontComboBox(self)
        form.addRow("Font:", self.font)
        self.height = QDoubleSpinBox(self)
        self.height.setDecimals(4)
        self.height.setRange(
            units.convert(0.001, "m", unit), units.convert(10.0, "m", unit)
        )
        self.height.setValue(units.convert(0.1, "m", unit))
        self.extrusion = QDoubleSpinBox(self)
        self.extrusion.setDecimals(4)
        self.extrusion.setRange(0.0, units.convert(10.0, "m", unit))
        self.extrusion.setValue(units.convert(0.01, "m", unit))
        self.offset = QDoubleSpinBox(self)
        self.offset.setDecimals(4)
        self.offset.setRange(units.convert(-10.0, "m", unit),
                             units.convert(10.0, "m", unit))
        self.offset.setValue(units.convert(0.15, "m", unit))
        form.addRow(f"Text height ({unit}):", self.height)
        form.addRow(f"Text extrusion ({unit}):", self.extrusion)
        form.addRow(f"Text offset ({unit}):", self.offset)
        self.view = QComboBox(self)
        for label, value in (
            ("Top", "top"), ("Front", "front"), ("Right", "right"),
            ("Left", "left"), ("Back", "back"), ("Bottom", "bottom"),
            ("Camera", "camera"),
        ):
            self.view.addItem(label, value)
        form.addRow("Dimension view:", self.view)
        self.color = COLOR
        self.color_button = QPushButton("Choose color…", self)
        self.color_button.clicked.connect(self._choose_color)
        form.addRow("Color:", self.color_button)
        self.show_lines = QCheckBox("Show dimension geometry", self)
        self.show_lines.setChecked(True)
        form.addRow("", self.show_lines)
        self.select_created = QCheckBox(
            "Show orange frame for created dimension", self
        )
        self.select_created.setChecked(True)
        form.addRow("", self.select_created)

        self.mode.currentIndexChanged.connect(self._update_controls)
        self._update_controls()
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_color(self):
        color = QColorDialog.getColor(
            QColor.fromRgbF(*self.color), self, "Special dimension color"
        )
        if color.isValid():
            self.color = (color.redF(), color.greenF(), color.blueF())
            self.color_button.setStyleSheet(
                f"background-color: {color.name()};"
            )

    def _update_controls(self):
        mode = self.mode.currentData()
        if mode != self._prefix_mode:
            self._prefix_values[self._prefix_mode] = self.prefix.text()
            self.prefix.setText(
                self._prefix_values.get(mode, _default_dimension_prefix(mode))
            )
            self._prefix_mode = mode
        self.include_holes.setEnabled(mode == MODE_PERIMETER)
        self.arc_combo.setEnabled(
            mode in {MODE_ARC_LENGTH, MODE_ARC_RADIUS, MODE_ARC_ANGLE}
        )
        self.circle_combo.setEnabled(
            mode in {MODE_CIRCLE_RADIUS, MODE_CIRCLE_DIAMETER}
        )
        for combo in (self.first_line, self.second_line):
            combo.setEnabled(mode == MODE_LINE_ANGLE)
        self.angle_style.setEnabled(
            mode in {MODE_LINE_ANGLE, MODE_ARC_ANGLE}
        )

    def options(self) -> dict:
        from core import units

        factor = units.convert(1.0, self._unit, "m")
        selected_guides = [
            item.data(Qt.ItemDataRole.UserRole)
            for index in range(self.guide_list.count())
            for item in [self.guide_list.item(index)]
            if item.checkState() == Qt.CheckState.Checked
        ]
        return {
            "mode": self.mode.currentData(),
            "prefix": self.prefix.text(),
            "guide_refs": selected_guides,
            "arc_uid": self.arc_combo.currentData(),
            "circle_uid": self.circle_combo.currentData(),
            "line_uids": [
                self.first_line.currentData(),
                self.second_line.currentData(),
            ],
            "include_holes": self.include_holes.isChecked(),
            "angle_style": self.angle_style.currentData(),
            "decimals": self.decimals.value(),
            "font": self.font.currentFont().family(),
            "height": self.height.value() * factor,
            "extrusion": self.extrusion.value() * factor,
            "offset": self.offset.value() * factor,
            "text_offset": [0.0, 0.0, 0.0],
            "view": self.view.currentData(),
            "color": self.color,
            "show_lines": self.show_lines.isChecked(),
            "show_frame": self.select_created.isChecked(),
        }


def _make_group(state: dict, options: dict, camera) -> Group:
    mode = str(options["mode"])
    arcs_by_uid = {arc["uid"]: arc for arc in state["arcs"]}
    lines_by_uid = {line["uid"]: line for line in state["lines"]}
    right, up, normal = _view_basis(str(options["view"]), camera)
    height = float(options["height"])
    extrusion = float(options["extrusion"])
    offset = float(options["offset"])
    text_offset = _vector(options.get("text_offset", [0.0, 0.0, 0.0]))
    color = tuple(float(channel) for channel in options["color"])
    line_width = max(height * 0.025, 0.0005)
    mesh = Mesh()
    show_lines = bool(options["show_lines"])

    def add_beam(start, end):
        if show_lines:
            _add_beam(mesh, start, end, line_width, color)

    def add_polyline(points, closed=False):
        if show_lines:
            _add_polyline(mesh, points, line_width, color, closed)

    def add_center_mark(center):
        if show_lines:
            _add_center_mark(
                mesh, center, right, up, height * 0.32, line_width, color
            )

    source_uids: list[str] = []
    points_for_label: list[QVector3D] = []
    perimeter_anchor: QVector3D | None = None

    if mode == MODE_PERIMETER:
        selected_lines = []
        selected_arcs = []
        for kind, uid in options["guide_refs"]:
            if kind == "line" and uid in lines_by_uid:
                selected_lines.append(lines_by_uid[uid])
            elif kind == "arc" and uid in arcs_by_uid:
                selected_arcs.append(arcs_by_uid[uid])
        loops = _selected_perimeter_loops(
            selected_lines, selected_arcs, bool(options["include_holes"])
        )
        value = sum(loop["length"] for loop in loops)
        for loop in loops:
            add_polyline(loop["points"], closed=True)
            if perimeter_anchor is None and len(loop["points"]) > 1:
                perimeter_anchor = (
                    loop["points"][0] + loop["points"][1]
                ) * 0.5
            points_for_label.extend(loop["points"])
            source_uids.extend(loop["uids"])
        measurement = _format_length(value, options["decimals"])
    elif mode in {MODE_ARC_LENGTH, MODE_ARC_RADIUS, MODE_ARC_ANGLE}:
        arc = arcs_by_uid.get(options.get("arc_uid"))
        if arc is None:
            raise ValueError("Select an available continuous arc.")
        source_uids = [arc["uid"]]
        arc_points = _arc_points(arc)
        center = _vector(arc["center"])
        radius = float(arc["radius"])
        middle = _point_on_arc(
            arc, float(arc["a0"]) + float(arc["sweep"]) * 0.5
        )
        points_for_label = arc_points
        if mode == MODE_ARC_LENGTH:
            value = _arc_length(arc)
            add_polyline(arc_points)
            measurement = _format_length(value, options["decimals"])
        elif mode == MODE_ARC_RADIUS:
            add_polyline(arc_points)
            add_beam(center, middle)
            add_center_mark(center)
            measurement = _format_length(radius, options["decimals"])
            points_for_label = [middle]
        else:
            value = float(arc["sweep"])
            add_polyline(arc_points)
            measurement = _format_angle(
                value, options["angle_style"], options["decimals"]
            )
    elif mode in {MODE_CIRCLE_RADIUS, MODE_CIRCLE_DIAMETER}:
        circle = arcs_by_uid.get(options.get("circle_uid"))
        if circle is None or abs(abs(float(circle["sweep"])) - TAU) > 1e-6:
            raise ValueError("Select a full-circle continuous guide.")
        source_uids = [circle["uid"]]
        center = _vector(circle["center"])
        start = _point_on_arc(circle, float(circle["a0"]))
        circle_points = _arc_points(circle)[:-1]
        add_polyline(circle_points, closed=True)
        if mode == MODE_CIRCLE_RADIUS:
            add_beam(center, start)
            measured_value = float(circle["radius"])
        else:
            opposite = _point_on_arc(circle, float(circle["a0"]) + math.pi)
            add_beam(start, opposite)
            measured_value = float(circle["radius"]) * 2.0
        add_center_mark(center)
        points_for_label = [start]
        measurement = _format_length(measured_value, options["decimals"])
    elif mode == MODE_LINE_ANGLE:
        selected = [
            lines_by_uid.get(uid) for uid in options["line_uids"]
        ]
        if any(line is None for line in selected):
            raise ValueError("Choose two available guide lines.")
        vertex, first, second = _line_intersection(selected)
        radius = max(height * 3.0, abs(offset) * 0.4)
        angle_points, angle = _angle_arc_points(vertex, first, second, radius)
        add_beam(_vector(selected[0]["a"]), _vector(selected[0]["b"]))
        add_beam(_vector(selected[1]["a"]), _vector(selected[1]["b"]))
        add_polyline(angle_points)
        add_center_mark(vertex)
        points_for_label = [angle_points[len(angle_points) // 2]]
        source_uids = [line["uid"] for line in selected]
        measurement = _format_angle(
            angle, options["angle_style"], options["decimals"]
        )
    else:
        raise ValueError(f'Unknown special dimension "{mode}".')

    if not points_for_label:
        raise ValueError("Could not determine a label position for this dimension.")
    label = (
        str(options.get("prefix", _default_dimension_prefix(mode)))
        + measurement
    )
    anchor = (
        perimeter_anchor
        if perimeter_anchor is not None
        else sum(points_for_label, QVector3D()) / len(points_for_label)
    )
    label_center = (
        anchor
        + up * offset
        + normal * max(extrusion, height * 0.15)
        + text_offset
    )
    if show_lines:
        _add_beam(mesh, anchor, label_center, line_width, color)
    text_width = _add_text(
        mesh, label, label_center, right, up, normal,
        height, extrusion, str(options["font"]), color,
    )

    layer_name = str(options.get("layer", DEFAULT_LAYER))
    group = Group(mesh, name=f"3D {label}"[:60])
    group.layer = None if layer_name == DEFAULT_LAYER else layer_name
    group.ext = {
        KEY: {
            "version": 1,
            "mode": mode,
            "source_uids": source_uids,
            "options": {
                key: value
                for key, value in options.items()
                if key not in {"guide_refs", "line_uids"}
            },
            "guide_refs": options.get("guide_refs", []),
            "line_uids": options.get("line_uids", []),
            "layer": layer_name,
            "label": label,
            "label_center": [label_center.x(), label_center.y(), label_center.z()],
            "label_right": [right.x(), right.y(), right.z()],
            "label_up": [up.x(), up.y(), up.z()],
            "label_width": text_width,
        }
    }
    return group


def _notify(viewport) -> None:
    notify = getattr(viewport, "notify_scene_changed", None)
    if callable(notify):
        notify()
    viewport.update()


def _execute(viewport, command) -> None:
    viewport.history.execute(command)
    error = getattr(viewport.history, "last_error", None)
    if error:
        raise RuntimeError(error)
    _notify(viewport)


def _move_dimension_text(app, viewport, target, delta: QVector3D) -> None:
    group, parent, index, data = target
    mode = str(data.get("mode", ""))
    options = dict(data.get("options", {}))
    options["mode"] = mode
    options["layer"] = str(data.get("layer", group.layer or DEFAULT_LAYER))
    if mode == MODE_PERIMETER:
        options["guide_refs"] = list(data.get("guide_refs", []))
    elif mode == MODE_LINE_ANGLE:
        options["line_uids"] = list(
            data.get("line_uids", data.get("source_uids", []))
        )
    elif mode in {MODE_ARC_LENGTH, MODE_ARC_RADIUS, MODE_ARC_ANGLE}:
        source_uids = list(data.get("source_uids", []))
        options["arc_uid"] = options.get("arc_uid") or (
            source_uids[0] if source_uids else None
        )
    elif mode in {MODE_CIRCLE_RADIUS, MODE_CIRCLE_DIAMETER}:
        source_uids = list(data.get("source_uids", []))
        options["circle_uid"] = options.get("circle_uid") or (
            source_uids[0] if source_uids else None
        )

    text_offset = (
        _vector(options.get("text_offset", [0.0, 0.0, 0.0])) + delta
    )
    options["text_offset"] = [
        text_offset.x(),
        text_offset.y(),
        text_offset.z(),
    ]
    state = _guide_snapshot(app)
    updated = _make_group(state, options, getattr(viewport, "camera", None))
    _execute(
        viewport,
        _ReplaceGroupsCommand(
            viewport.scene, [(parent, group, updated, index)]
        ),
    )


def activate_dimension_text_move(app) -> None:
    viewport = app.viewport
    try:
        viewport.set_active_tool(_MoveDimensionTextTool(app))
        viewport.setFocus()
        viewport.flash_status(
            "Drag a special-dimension text label to reposition it.", 4000
        )
    except Exception as exc:
        log.exception("Could not activate dimension-text movement")
        QMessageBox.critical(None, "Move Special Dimension Text", str(exc))


def create_special_dimensions(app, selected_guides=()) -> None:
    viewport = app.viewport
    scene = viewport.scene
    try:
        state = _guide_snapshot(app)
        if not state["arcs"] and not state["lines"]:
            raise ValueError("There are no continuous guide lines or arcs to measure.")
        dialog = _DimensionDialog(
            viewport.window(), state, scene, selected_guides
        )
        if dialog.exec() != QDialog.Accepted:
            return
        options = dialog.options()
        options["layer"] = active_layer(scene) or DEFAULT_LAYER
        group = _make_group(state, options, getattr(viewport, "camera", None))
        _execute(
            viewport,
            _InsertGroupsCommand(
                scene, [group], options.get("show_frame", True)
            ),
        )
    except Exception as exc:
        log.exception("Could not create special 3D dimension")
        QMessageBox.critical(None, "Special 3D Dimensions", str(exc))


def convert_selected_edges_to_guides(app, panel=None) -> None:
    viewport = app.viewport
    try:
        selection = list(viewport.scene.selection)
        if not selection:
            raise ValueError("Select model edges or faces in the viewport first.")

        edge_segments = []
        face_edge_counts = {}
        face_edge_segments = {}
        unsupported = []
        for item in selection:
            if isinstance(item, Edge):
                edge_segments.append(
                    _selected_edge_world_endpoints(viewport.scene, item)
                )
            elif isinstance(item, Face):
                for loop in _selected_face_world_loops(viewport.scene, item):
                    for index, start in enumerate(loop):
                        end = loop[(index + 1) % len(loop)]
                        key = tuple(sorted((_point_key(start), _point_key(end))))
                        face_edge_counts[key] = face_edge_counts.get(key, 0) + 1
                        face_edge_segments[key] = (start, end)
            else:
                unsupported.append(item)
        if unsupported:
            raise ValueError(
                "Select model edges and/or faces only. Groups and other "
                "selection types cannot be converted directly."
            )
        edge_segments.extend(
            segment
            for key, segment in face_edge_segments.items()
            if face_edge_counts[key] == 1
        )
        if not edge_segments:
            raise ValueError("The selection contains no convertible boundary edges.")

        state = _guide_snapshot(app)
        arcs = []
        lines = []
        next_arc = len(state["arcs"]) + 1
        next_line = len(state["lines"]) + 1
        for points, closed in _edge_paths(edge_segments):
            path_arcs, path_lines = _guides_from_path(
                points, closed, next_arc, next_line
            )
            arcs.extend(path_arcs)
            lines.extend(path_lines)
            next_arc += len(path_arcs)
            next_line += len(path_lines)
        if not arcs and not lines:
            raise ValueError("No guide lines or arcs could be built from the selection.")
        state["arcs"].extend(arcs)
        state["lines"].extend(lines)
        _store_guides(app, state)
        if panel is not None:
            panel.refresh()
        viewport.flash_status(
            f"Created {len(arcs)} guide arc(s) and {len(lines)} guide line(s).",
            3000,
        )
        viewport.update()
    except Exception as exc:
        log.exception("Could not convert selected edges to guide lines")
        QMessageBox.critical(None, "Special 3D Dimensions", str(exc))


def update_special_dimensions(app) -> None:
    viewport = app.viewport
    scene = viewport.scene
    try:
        state = _guide_snapshot(app)
        replacements = []
        arcs_by_uid = {arc["uid"]: arc for arc in state["arcs"]}
        lines_by_uid = {line["uid"]: line for line in state["lines"]}

        def walk(container):
            for index, group in enumerate(list(container)):
                yield group, container, index
                yield from walk(group.children or [])

        for group, parent, index in walk(scene.groups):
            data = (group.ext or {}).get(KEY)
            if not isinstance(data, dict) or data.get("version") != 1:
                continue
            options = dict(data.get("options", {}))
            mode = data.get("mode")
            source_uids = list(data.get("source_uids", []))
            if mode == MODE_PERIMETER:
                guide_refs = list(data.get("guide_refs", []))
                for kind, uid in guide_refs:
                    table = lines_by_uid if kind == "line" else arcs_by_uid
                    if uid not in table:
                        raise ValueError(
                            "A guide used by a special perimeter dimension no "
                            "longer exists."
                        )
                options["guide_refs"] = guide_refs
            elif mode == MODE_LINE_ANGLE:
                if any(uid not in lines_by_uid for uid in source_uids):
                    raise ValueError("A guide line used by an angle no longer exists.")
                options["line_uids"] = source_uids
            elif mode in {MODE_ARC_LENGTH, MODE_ARC_RADIUS, MODE_ARC_ANGLE}:
                if not source_uids or source_uids[0] not in arcs_by_uid:
                    raise ValueError("The measured arc no longer exists.")
                options["arc_uid"] = source_uids[0]
            elif mode in {MODE_CIRCLE_RADIUS, MODE_CIRCLE_DIAMETER}:
                if not source_uids or source_uids[0] not in arcs_by_uid:
                    raise ValueError("The measured circle no longer exists.")
                options["circle_uid"] = source_uids[0]
            options["mode"] = mode
            options["layer"] = str(data.get("layer", group.layer or DEFAULT_LAYER))
            updated = _make_group(
                state, options, getattr(viewport, "camera", None)
            )
            replacements.append((parent, group, updated, index))
        if not replacements:
            raise ValueError("No special 3D dimension groups were found.")
        _execute(viewport, _ReplaceGroupsCommand(scene, replacements))
    except Exception as exc:
        log.exception("Could not update special 3D dimensions")
        QMessageBox.critical(None, "Update Special 3D Dimensions", str(exc))


def setup(app) -> None:
    panel = _GuidePanel(app)
    app.add_panel("Special Dimension Guides", panel)
    app.add_overlay(
        lambda viewport, painter: _draw_guides(app, viewport, painter, panel)
    )
    app.add_menu_action(
        "Convert Selected Edges/Faces to Guides",
        lambda: convert_selected_edges_to_guides(app),
        tip="Convert selected model edges/faces, including segmented arcs, to guides.",
    )
    app.add_menu_action(
        "Create Special 3D Dimension…",
        lambda: create_special_dimensions(app, panel.selected_guides()),
        tip=(
            "Create GLB-exportable perimeter, arc length/radius, circle "
            "radius/diameter, and angle dimensions from continuous guides."
        ),
    )
    app.add_menu_action(
        "Move Special Dimension Text",
        lambda: activate_dimension_text_move(app),
        tip="Drag the label of a generated dimension to move it in the viewport.",
    )
    app.add_menu_action(
        "Update Special 3D Dimensions",
        lambda: update_special_dimensions(app),
        tip="Refresh special dimensions from their current continuous guides.",
    )
    log.info(
        "Special 3D Dimensions extension loaded (key=%s)",
        getattr(app, "key", "?"),
    )
