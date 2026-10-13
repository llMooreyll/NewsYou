#!/usr/bin/env python3
"""One module per source.

Every module exposes ``collect(limit=..., fulltext=..., engine=..., **_) -> list[dict]``
and returns items built with ``base.item(...)``. ``registry.py`` wires them up.
"""
