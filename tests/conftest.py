from __future__ import annotations

import os
import uuid
import tempfile
from pathlib import Path


def pytest_configure(config) -> None:
    """Force pytest temp files into the workspace on restricted Windows hosts."""
    temp_root = Path.cwd() / ".tmp"
    temp_root.mkdir(parents=True, exist_ok=True)

    tempfile.tempdir = str(temp_root)
    os.environ["TMPDIR"] = str(temp_root)
    os.environ["TEMP"] = str(temp_root)
    os.environ["TMP"] = str(temp_root)
    config.option.basetemp = str(temp_root / f"pytest-{uuid.uuid4().hex}")
