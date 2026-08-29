#!/usr/bin/env python3
"""Compatibility guard for the already-migrated Alpha Engine v8.6 line.

The repository has moved beyond the original 8.6.0 baseline (for example the
8.6.1 math/risk layer). Re-running the historical text migrator against a newer
8.6.x engine used to fail because it expected the old 8.5 version anchor.

This module keeps the public helper contract used by
apply_alpha_v86_external_cockpit.py, but is deliberately fail-closed: it accepts
only an engine that already contains the v8.6 episode-ledger semantics and never
downgrades a newer 8.6.x model version.
"""
from __future__ import annotations

import re


COMPAT_BASELINE = "8.6.0-episode-ledger-cost-aware"


def replace_one(text, old, new, label):
    if new in text:
        return text, False
    if old not in text:
        raise RuntimeError(f"{label}: anchor not found")
    return text.replace(old, new, 1), True


def replace_unique(text, old, new, label):
    if new in text:
        return text, False
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1), True


def replace_regex(text, pattern, repl, label, marker=None):
    marker = marker or repl.splitlines()[0].strip()
    if marker and marker in text:
        return text, False
    new_text, n = re.subn(pattern, repl, text, count=1, flags=re.S)
    if n != 1:
        raise RuntimeError(f"{label}: expected 1 regex match, found {n}")
    return new_text, True


def _model_version(text):
    m = re.search(r'^MODEL_VERSION\s*=\s*["\']([^"\']+)["\']', text, flags=re.M)
    return m.group(1) if m else None


def patch_engine(text):
    """Validate modern v8.6 episode-ledger semantics without version rollback.

    The external cockpit migrator historically verifies the original 8.6.0
    baseline marker. For newer 8.6.x engines we add only a harmless compatibility
    comment; MODEL_VERSION itself is left untouched.
    """
    version = _model_version(text)
    if not version or not version.startswith("8.6."):
        raise RuntimeError(
            f"engine version: expected an already-migrated 8.6.x engine, got {version!r}"
        )

    required = [
        'LEARNING_LINEAGE = "ALPHA_V86_EPISODE_LEDGER_1"',
        '"schema_version": "8.6"',
        'ONE_ACTIVE_EPISODE_PER_TICKER_WITH_STABLE_SETUP_KEY',
        'def migrate_prediction_episodes(mem):',
        'model_completeness_score',
        'EXCLUDED_DUPLICATE',
    ]
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise RuntimeError(
            "v8.6 engine is missing required episode-ledger semantics: " + ", ".join(missing)
        )

    if COMPAT_BASELINE in text:
        return text, False

    anchor = re.search(r'^MODEL_VERSION\s*=\s*["\'][^"\']+["\']\s*$', text, flags=re.M)
    if not anchor:
        raise RuntimeError("engine version assignment not found")
    line = anchor.group(0)
    replacement = line + f"\n# Migration compatibility baseline: {COMPAT_BASELINE}"
    return text[:anchor.start()] + replacement + text[anchor.end():], True
