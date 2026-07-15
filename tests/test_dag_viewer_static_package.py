from __future__ import annotations

import re

import pytest

from tau_coding.dag_viewer.static_files import read_static_viewer_file


def test_packaged_static_index_references_available_local_assets() -> None:
    index = read_static_viewer_file("/")
    assert index.content_type.startswith("text/html")
    assert b'<div id="root"></div>' in index.body
    paths = re.findall(rb'(?:src|href)="(/assets/[^"]+)"', index.body)
    assert paths
    for path in paths:
        asset = read_static_viewer_file(path.decode())
        assert asset.body


def test_packaged_static_reader_blocks_traversal_and_missing_assets() -> None:
    with pytest.raises(RuntimeError, match="dag_viewer_static_path_invalid"):
        read_static_viewer_file("/assets/../../secret")
    with pytest.raises(RuntimeError, match="dag_viewer_static_not_found"):
        read_static_viewer_file("/assets/missing.js")
