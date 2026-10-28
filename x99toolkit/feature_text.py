"""User-facing explanations of the existing firmware changes."""
from html import escape
import re
from .optimized import OPTIMIZED_SETTINGS

PERFORMANCE_DEFAULTS_DESCRIPTION = 'Applies performance-oriented default settings in BIOS.'


def logo_check_failure_reason(reason: str) -> str:
    reason = re.sub(r'^(?:(?:ValueError|RuntimeError|OSError|FileNotFoundError|PermissionError):\s*)+', '', reason).strip()
    if 'ROM is byte-identical' in reason:
        return 'ChangeLogo did not change the BIOS file even though a different logo was needed.'
    if 'test-fit command did not positively confirm' in reason:
        return 'ChangeLogo could not confirm that the new logo fits inside this BIOS.'
    return reason or 'The check returned no result. Recheck the tools and reopen the ROM.'


TBU_CPU_WARNING = '''Turbo Boost Unlock only works on supported Xeon E5-26xx v3 CPUs.

It does not work on Xeon E5 v4 CPUs. Applying this hack to a BIOS used with a v4 CPU may make the computer unable to POST, meaning it cannot complete its startup checks or boot into Windows.

Continue only if this ROM will be used with a compatible Xeon E5-26xx v3 CPU.'''

SETTING_REASONS = {
    'ACPI T-States': 'Disables power-saving CPU throttling through ACPI T-states to favor performance.',
    'SATA AHCI ALPM': 'Disables aggressive link power saving on the main SATA controller to avoid link wake-up delays.',
    'sSATA AHCI ALPM': 'Disables aggressive link power saving on the secondary SATA controller for the same reason.',
    'Patrol Scrub': 'Stops periodic background ECC-memory scans and corrective write-backs, reducing background memory maintenance at the cost of that protection.',
    'Demand Scrub': 'Disables corrective write-back to ECC memory when a normal memory read encounters a correctable error. This reduces scrubbing work but removes that memory-maintenance protection.',
    'Memory Power Savings Mode': 'Disables memory power-saving modes to avoid their entry and wake-up transitions.',
    'MDLL Off': 'Prevents the memory delay-locked loop from being shut down during self-refresh, avoiding that extra power-state transition.',
    'Energy Performance Tuning': 'Uses the BIOS energy/performance-bias setting rather than leaving that tuning choice to the operating system.',
    'Energy Performance BIAS': 'Selects Performance to favor processor performance over energy efficiency.',
    'SATA AHCI LPM': 'Disables link power saving on the main SATA controller to avoid link wake-up delays.',
    'sSATA AHCI LPM': 'Disables link power saving on the secondary SATA controller for the same reason.',
}


def performance_defaults_explanation(inspection: dict | None = None) -> str:
    settings = (inspection or {}).get('settings') or OPTIMIZED_SETTINGS
    rows = []
    for setting in settings:
        target = {'Disable': 'Disabled', 'Enable': 'Enabled'}.get(setting['desired_label'], setting['desired_label'])
        current = ' Already set to this value.' if setting.get('matches') else ''
        rows.append('<li><b>' + escape(setting['name']) + ' → ' + escape(target) + '</b><br>'
                    + escape(SETTING_REASONS[setting['name']] + current) + '</li>')
    return (
        '<p>These changes make the BIOS default settings favor performance and responsiveness. '
        'They are the values restored when you load BIOS defaults. A setting already at its target value is kept.</p>'
        '<ul>' + ''.join(rows) + '</ul>'
        '<p>Disabling power-saving options can increase idle power use. Disabling memory scrubbing removes '
        'those specific ECC-memory maintenance features; the ECC operating mode itself is not changed.</p>'
    )
