"""Expand components (M2b).

For each ``components.<name>``:

* If ``placement.is_floating`` is False (or absent) and ``(x, y)`` are present,
  apply the affine transform
  ``abs = (x, y) + R(rotation) * (local_x, local_y)`` to every footprint pin
  and emit a ``fixed`` :class:`ExpandedPad`.
* If ``placement.type == "parametric_uv"``, register the component as a UV
  expansion with one ``uv_deferred`` pad per footprint pin (no abs coords yet)
  plus a :class:`UvMeta` describing the anchor.
* Compute a footprint bounding box (in absolute coordinates) for fixed
  components; this is consumed by ``obstacles.build_obstacles``.
"""

from __future__ import annotations

import math

from schema.v33 import Component, Footprint, V33Layout

from .models import BBox, ComponentExpansion, ExpandedPad, UvMeta


def expand_components(
    layout: V33Layout,
) -> dict[str, ComponentExpansion]:
    """Return ``{component_name: ComponentExpansion}`` for the whole layout."""

    out: dict[str, ComponentExpansion] = {}
    for name, component in layout.components.items():
        out[name] = expand_component(name, component, layout.footprints)
    return out


def expand_component(
    name: str,
    component: Component,
    footprints: dict[str, Footprint],
) -> ComponentExpansion:
    placement = component.placement
    footprint_ref = component.footprint_ref
    footprint = footprints.get(footprint_ref) if footprint_ref is not None else None

    if placement is not None and placement.type == "parametric_uv":
        return _expand_uv(name, component, footprint)

    if (
        placement is not None
        and getattr(placement, "is_floating", False) is True
        and (placement.x is None or placement.y is None)
    ):
        return _expand_floating(name, component, footprint)

    return _expand_fixed(name, component, footprint)


def _expand_floating(
    name: str,
    component: Component,
    footprint: Footprint | None,
) -> ComponentExpansion:
    """WI-J2 — Floating component: pads枚举 local 信息但无绝对坐标。

    phaseC 的 floating_placer 会赋予 (x, y, rotation) 并实例化 absolute pads。
    """
    pads: list[ExpandedPad] = []
    pin_items = footprint.pins.items() if footprint is not None else ()
    for pin_name, pin in pin_items:
        local_x = float(pin.local_x or 0.0)
        local_y = float(pin.local_y or 0.0)
        pad_w = (
            float(pin.pad_geometry.width)
            if pin.pad_geometry and pin.pad_geometry.width
            else None
        )
        pad_l = (
            float(pin.pad_geometry.length)
            if pin.pad_geometry and pin.pad_geometry.length
            else None
        )
        pads.append(
            ExpandedPad(
                component=name,
                pin=pin_name,
                abs_x=None,
                abs_y=None,
                orientation=None,
                kind="floating_deferred",
                local_x=local_x,
                local_y=local_y,
                pad_width=pad_w,
                pad_length=pad_l,
            )
        )
    fp_dims: tuple[float, float] | None = None
    if footprint is not None and footprint.dimensions is not None:
        w = footprint.dimensions.width
        ln = footprint.dimensions.length
        if w is not None and ln is not None:
            fp_dims = (float(w), float(ln))
    return ComponentExpansion(
        name=name,
        footprint_ref=component.footprint_ref,
        placement_kind="floating",
        pads=tuple(pads),
        bbox=None,
        uv_meta=None,
        pin_nets=dict(component.pin_nets or {}),
        footprint_dims_mm=fp_dims,
    )


def _expand_fixed(
    name: str,
    component: Component,
    footprint: Footprint | None,
) -> ComponentExpansion:
    placement = component.placement
    if placement is None or placement.x is None or placement.y is None:
        return ComponentExpansion(
            name=name,
            footprint_ref=component.footprint_ref,
            placement_kind="unknown",
            pads=(),
            bbox=None,
            uv_meta=None,
        )

    rotation = float(placement.rotation or 0.0)
    cos_t, sin_t = _rot(rotation)

    pads: list[ExpandedPad] = []
    xs: list[float] = []
    ys: list[float] = []

    pin_items = footprint.pins.items() if footprint is not None else ()
    for pin_name, pin in pin_items:
        local_x = float(pin.local_x or 0.0)
        local_y = float(pin.local_y or 0.0)
        abs_x = placement.x + cos_t * local_x - sin_t * local_y
        abs_y = placement.y + sin_t * local_x + cos_t * local_y
        local_orientation = float(pin.local_orientation or 0.0)
        pad_w = (
            float(pin.pad_geometry.width)
            if pin.pad_geometry and pin.pad_geometry.width
            else None
        )
        pad_l = (
            float(pin.pad_geometry.length)
            if pin.pad_geometry and pin.pad_geometry.length
            else None
        )
        pads.append(
            ExpandedPad(
                component=name,
                pin=pin_name,
                abs_x=abs_x,
                abs_y=abs_y,
                orientation=rotation + local_orientation,
                kind="fixed",
                local_x=local_x,
                local_y=local_y,
                pad_width=pad_w,
                pad_length=pad_l,
            )
        )
        xs.append(abs_x)
        ys.append(abs_y)

    bbox: BBox | None = None
    if footprint is not None and footprint.dimensions is not None:
        width = footprint.dimensions.width
        length = footprint.dimensions.length
        if width is not None and length is not None:
            bbox = _rotated_bbox(placement.x, placement.y, width, length, rotation)
    if bbox is None and xs:
        bbox = BBox(min(xs), min(ys), max(xs), max(ys))

    fp_dims: tuple[float, float] | None = None
    if footprint is not None and footprint.dimensions is not None:
        w = footprint.dimensions.width
        ln = footprint.dimensions.length
        if w is not None and ln is not None:
            fp_dims = (float(w), float(ln))

    return ComponentExpansion(
        name=name,
        footprint_ref=component.footprint_ref,
        placement_kind="fixed",
        pads=tuple(pads),
        bbox=bbox,
        uv_meta=None,
        pin_nets=dict(component.pin_nets or {}),
        footprint_dims_mm=fp_dims,
    )


def _expand_uv(
    name: str,
    component: Component,
    footprint: Footprint | None,
) -> ComponentExpansion:
    placement = component.placement
    pads: list[ExpandedPad] = []
    pin_items = footprint.pins.items() if footprint is not None else ()
    for pin_name, pin in pin_items:
        local_x = float(pin.local_x or 0.0)
        local_y = float(pin.local_y or 0.0)
        pad_w = (
            float(pin.pad_geometry.width)
            if pin.pad_geometry and pin.pad_geometry.width
            else None
        )
        pad_l = (
            float(pin.pad_geometry.length)
            if pin.pad_geometry and pin.pad_geometry.length
            else None
        )
        pads.append(
            ExpandedPad(
                component=name,
                pin=pin_name,
                abs_x=None,
                abs_y=None,
                orientation=None,
                kind="uv_deferred",
                local_x=local_x,
                local_y=local_y,
                pad_width=pad_w,
                pad_length=pad_l,
            )
        )
    uv_meta = UvMeta(
        anchor_pin=(placement.anchor_pin if placement is not None else "") or "",
        reference_net=(placement.reference_net if placement is not None else None),
    )
    fp_dims: tuple[float, float] | None = None
    if footprint is not None and footprint.dimensions is not None:
        w = footprint.dimensions.width
        ln = footprint.dimensions.length
        if w is not None and ln is not None:
            fp_dims = (float(w), float(ln))
    return ComponentExpansion(
        name=name,
        footprint_ref=component.footprint_ref,
        placement_kind="parametric_uv",
        pads=tuple(pads),
        bbox=None,
        uv_meta=uv_meta,
        pin_nets=dict(component.pin_nets or {}),
        footprint_dims_mm=fp_dims,
    )


def _rot(rotation_deg: float) -> tuple[float, float]:
    theta = math.radians(rotation_deg)
    return math.cos(theta), math.sin(theta)


def _rotated_bbox(
    cx: float,
    cy: float,
    width: float,
    length: float,
    rotation_deg: float,
) -> BBox:
    half_w = width / 2.0
    half_l = length / 2.0
    cos_t, sin_t = _rot(rotation_deg)
    corners = [
        (-half_w, -half_l),
        (half_w, -half_l),
        (half_w, half_l),
        (-half_w, half_l),
    ]
    xs = [cx + cos_t * lx - sin_t * ly for lx, ly in corners]
    ys = [cy + sin_t * lx + cos_t * ly for lx, ly in corners]
    return BBox(min(xs), min(ys), max(xs), max(ys))


__all__ = ["expand_component", "expand_components"]
