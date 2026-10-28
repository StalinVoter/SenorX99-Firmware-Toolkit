from __future__ import annotations

class BuildProgressTracker:
    def __init__(self, options: dict):
        self.stages = ['Input preflight', 'Initial inspection']
        for option, stages in [
            ('apply_tbu', ['Remove 06F2 microcode', 'Set primary IntelSetup C-states', 'Set NV-Internal C-state defaults', 'Set AMITSESetupData C-state default', 'Validate complete MiyConst C-states', 'Apply TBU payload']),
            ('apply_optimized', ['Apply Optimized BIOS Settings: primary IntelSetup', 'Apply Optimized BIOS Settings: NV-Internal', 'Apply Optimized BIOS Settings: AMITSESetupData', 'Validate Optimized BIOS Settings']),
            ('apply_overclock', ['Apply multiplier OC: primary IntelSetup', 'Apply multiplier OC: NV-Internal', 'Apply multiplier OC: AMITSESetupData', 'Validate multiplier OC compatibility']),
            ('apply_secureboot', ['Apply Secure Boot 2023']),
            ('apply_splash', ['Replace splash logo']),
            ('repair_fit', ['Repair / validate FIT']),
        ]:
            if options.get(option): self.stages.extend(stages)
        self.stages.extend(['Final validation', 'Reports'])
        self.completed = set()
        self.current_percent = 0
        self.failed = False

    def update(self, name: str, status: str, details: str = '') -> int:
        if status == 'FAIL': self.failed = True
        if not self.failed and status in ('PASS','SKIP') and name in self.stages:
            self.completed.add(name)
        value = round(100 * len(self.completed) / len(self.stages))
        # Reports and accepted final ROM are prerequisites for completion.
        if not set(self.stages).issubset(self.completed): value = min(99, value)
        self.current_percent = max(self.current_percent, value)
        return self.current_percent
