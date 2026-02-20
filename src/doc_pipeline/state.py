"""JSON state management per pipeline run (./runs/YYYY-MM/state.json)."""

from __future__ import annotations

import json
import os
from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class PhaseStatus(str, Enum):
    """Status of a pipeline phase."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PhaseState(BaseModel):
    """State of a single phase."""

    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: PhaseStatus = PhaseStatus.PENDING
    error: str | None = None


class RunState(BaseModel):
    """State for a single pipeline execution cycle."""

    run_id: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    phases: dict[str, PhaseState] = Field(default_factory=dict)

    def mark_started(self, phase: str) -> None:
        self.phases[phase] = PhaseState(
            started_at=datetime.now(), status=PhaseStatus.RUNNING
        )

    def mark_completed(self, phase: str) -> None:
        if phase not in self.phases:
            raise KeyError(f"Unknown phase: {phase}")
        self.phases[phase].completed_at = datetime.now()
        self.phases[phase].status = PhaseStatus.COMPLETED

    def mark_failed(self, phase: str, error: str) -> None:
        if phase not in self.phases:
            raise KeyError(f"Unknown phase: {phase}")
        self.phases[phase].completed_at = datetime.now()
        self.phases[phase].status = PhaseStatus.FAILED
        self.phases[phase].error = error

    def check_stale_running(self) -> list[str]:
        """Return phase names stuck in 'running' state (likely from a crash)."""
        return [name for name, phase in self.phases.items()
                if phase.status == PhaseStatus.RUNNING]

    def reset_phase(self, phase_name: str) -> None:
        """Reset a phase back to PENDING status."""
        if phase_name in self.phases:
            self.phases[phase_name] = PhaseState(status=PhaseStatus.PENDING)

    def reset_all(self) -> None:
        """Reset all phases to PENDING status."""
        for phase_name in self.phases:
            self.phases[phase_name] = PhaseState(status=PhaseStatus.PENDING)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))

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
