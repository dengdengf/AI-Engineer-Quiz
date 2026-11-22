"""Run the Node tests for web/filters.js from pytest (skip if Node is absent)."""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_web_filter_helpers():
    result = subprocess.run(
        ["node", "--test", str(REPO_ROOT / "tests" / "test_web_filters.mjs")],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fail 0" in result.stdout or "pass" in result.stdout
