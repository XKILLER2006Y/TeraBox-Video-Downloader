"""
tests/test_storage_gc.py
~~~~~~~~~~~~~~~~~~~~~~~~
Tests for active transfer file protection & storage cleanup.
"""

import os
import tempfile
import pytest
from telegram_logic.bot import mark_file_active, unmark_file_active, is_file_active


def test_active_file_registration():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        path = f.name

    try:
        assert not is_file_active(path)
        mark_file_active(path)
        assert is_file_active(path)
        # Relative path resolution test
        assert is_file_active(os.path.relpath(path))

        unmark_file_active(path)
        assert not is_file_active(path)
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_active_file_edge_cases():
    assert not is_file_active(None)
    assert not is_file_active("")
    mark_file_active(None)
    unmark_file_active(None)
