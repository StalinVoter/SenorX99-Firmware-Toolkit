from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

@dataclass
class StepRecord:
    name: str
    status: str
    details: str

@dataclass
class InspectionReport:
    rom_path: str
    rom_size: int
    rom_sha256: str
    tbu: dict[str, Any]
    secure_boot: dict[str, Any]
    fit: dict[str, Any] = field(default_factory=dict)
    optimized: dict[str, Any] = field(default_factory=dict)
    overclock: dict[str, Any] = field(default_factory=dict)
    splash: dict[str, Any] = field(default_factory=dict)
    rows: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class BuildReport:
    input_path: str
    output_path: str
    build_log_path: str = ""
    input_sha256: str = ""
    output_sha256: str = ""
    input_size: int = 0
    output_size: int = 0
    options: dict[str, Any] = field(default_factory=dict)
    initial_inspection: dict[str, Any] = field(default_factory=dict)
    final_inspection: dict[str, Any] = field(default_factory=dict)
    secure_boot_plan: list[dict[str, Any]] = field(default_factory=list)
    fit_repair: dict[str, Any] = field(default_factory=dict)
    steps: list[StepRecord] = field(default_factory=list)
    success: bool = False
    error: str = ""
    tool_output: dict[str, str] = field(default_factory=dict)
    def add(self, name: str, status: str, details: str) -> None:
        self.steps.append(StepRecord(name, status, details))
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

ProgressCallback = Callable[[str, str, str], None]
