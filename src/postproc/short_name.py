"""Shared short-id formatter for SVG labels across postproc + tools."""

from __future__ import annotations

import re

# Matches "<comp_id>_pin<N>_" → "p<N>_".  Component id is letters then optional
# digits (e.g. IC1, U2, Q1).  Keeps trailing underscore so downstream replaces
# (_seg → s) still anchor correctly.
_PIN_PREFIX_RE = re.compile(r"\b[A-Za-z]+\d+_pin(\d+)_")


def short_id(name: str) -> str:
    """Compress verbose engine ids for SVG label rendering.

    Generic rules (applied in order, all string-level so they compose safely
    and stay idempotent for already-short ids):

    - ``<COMP>_pin<N>_`` → ``p<N>_`` (handles IC1/U2/Q3 etc., generic)
    - ``_universal_node`` → ``_u``
    - ``_end_split_pad`` → ``_sp``
    - ``_start_combiner`` → ``_sc``
    - ``_seg`` → ``s``
    - ``_to_`` → ``->``
    - ``.PIN_`` → ``.``
    """
    out = _PIN_PREFIX_RE.sub(lambda m: f"p{m.group(1)}_", name)
    out = out.replace("_universal_node", "_u")
    out = out.replace("_end_split_pad", "_sp")
    out = out.replace("_start_combiner", "_sc")
    out = out.replace("_seg", "s")
    out = out.replace("_to_", "->")
    out = out.replace(".PIN_", ".")
    return out


__all__ = ["short_id"]
