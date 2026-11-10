"""Resolve replay drops from the published golden manifest."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from parity.parity_check import GOLDEN_PATH, GoldenStore, manifest_drops


def load_drops(client=None):
    if client is None:
        from databricks.sdk import WorkspaceClient
        client = WorkspaceClient()
    return manifest_drops(GoldenStore(client, GOLDEN_PATH).manifest())


if __name__ == "__main__":
    print("\n".join(load_drops()))
