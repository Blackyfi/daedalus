# Importing `status_history` here registers the SQLAlchemy session
# listener that records every Task.status transition into
# task_status_events. Any process that touches the DB (API, hermes,
# CLI, talos) picks this up via `daedalus.db` imports.
from daedalus.db import status_history  # noqa: F401
