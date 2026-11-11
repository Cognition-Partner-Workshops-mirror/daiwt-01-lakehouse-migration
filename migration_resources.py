import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / ".migration/framework"))
from migration_ci.bundle import load_resources
