import importlib

import pytest


@pytest.mark.parametrize("lane", ["capture", "drivers", "plugins"])
def test_package_import(lane):
    assert importlib.import_module(f"fwc.{lane}").__name__ == f"fwc.{lane}"
