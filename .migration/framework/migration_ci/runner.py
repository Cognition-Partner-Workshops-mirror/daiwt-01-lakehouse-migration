"""Entrypoint uploaded with the vendored framework for serverless tasks."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(globals().get('__file__', sys.argv[0])).resolve().parents[1]))

from migration_ci.runtime import main

if __name__ == '__main__':
    main()
