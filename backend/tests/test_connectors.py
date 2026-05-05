import json
from pathlib import Path

from jsonschema import Draft202012Validator

from daedalus.connectors.schema import CONNECTOR_SCHEMA


def test_bundled_connectors_validate() -> None:
    validator = Draft202012Validator(CONNECTOR_SCHEMA)
    connector_dir = Path(__file__).resolve().parents[2] / "connectors"

    for path in sorted(connector_dir.glob("*.json")):
        spec = json.loads(path.read_text())
        errors = sorted(validator.iter_errors(spec), key=lambda error: list(error.path))
        assert not errors, f"{path.name} failed validation: {[error.message for error in errors]}"
