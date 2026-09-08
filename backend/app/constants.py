"""Small cross-module constants that don't belong to any one layer.

Kept separate from app/cli.py so the API layer (app/api/status.py) can
reference the same source key without importing the CLI module (which
pulls in argparse/CLI-only concerns).
"""

CHICAGO_SOURCE_KEY = "chicago-pd-open-data"
CHICAGO_CITY_NAME = "Chicago"
