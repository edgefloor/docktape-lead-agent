from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def checkpoint_path(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "workflow-checkpoints.sqlite"


def open_checkpointer(output_dir: Path):
    return SqliteSaver.from_conn_string(str(checkpoint_path(output_dir)))
