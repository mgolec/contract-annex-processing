"""JSON state management per pipeline run (./runs/YYYY-MM/state.json)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field


class PhaseState(BaseModel):
    """State of a single phase."""

    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: str = "pending"  # pending | running | completed | failed
    error: str | None = None


class RunState(BaseModel):
    """State for a single pipeline execution cycle."""

    run_id: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    phases: dict[str, PhaseState] = Field(default_factory=dict)

    def mark_started(self, phase: str) -> None:
        self.phases[phase] = PhaseState(
            started_at=datetime.now(), status="running"
        )

    def mark_completed(self, phase: str) -> None:
        if phase in self.phases:
            self.phases[phase].completed_at = datetime.now()
            self.phases[phase].status = "completed"

    def mark_failed(self, phase: str, error: str) -> None:
        if phase in self.phases:
            self.phases[phase].completed_at = datetime.now()
            self.phases[phase].status = "failed"
            self.phases[phase].error = error

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> RunState:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def get_run_dir(base: Path, run_id: str | None = None) -> Path:
    """Get or create the run directory for the current execution cycle."""
    if run_id is None:
        run_id = datetime.now().strftime("%Y-%m")
    return base / "runs" / run_id


def load_or_create_state(base: Path, run_id: str | None = None) -> tuple[RunState, Path]:
    """Load existing state or create a new one for this run."""
    run_dir = get_run_dir(base, run_id)
    state_path = run_dir / "state.json"

    if state_path.exists():
        state = RunState.load(state_path)
    else:
        if run_id is None:
            run_id = datetime.now().strftime("%Y-%m")
        state = RunState(run_id=run_id)

    return state, state_path
