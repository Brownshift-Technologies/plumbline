import tomllib, pathlib
from google.api_core import version as api_core_version

PYPROJECT = pathlib.Path(__file__).parents[1] / "pyproject.toml"

def test_api_core_is_pinned_below_2_35():
    deps = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    pin = next(d for d in deps if d.startswith("google-api-core"))
    assert ">=2.34.0,<2.35.0" in pin, f"api-core pin was loosened: {pin!r}"

def test_installed_api_core_does_not_encode_firestore_paths():
    major, minor, *_ = (int(x) for x in api_core_version.__version__.split("."))
    assert (major, minor) < (2, 35), (
        f"google-api-core {api_core_version.__version__} percent-encodes "
        "(default) into %28default%29 and 400s every Firestore query.")
