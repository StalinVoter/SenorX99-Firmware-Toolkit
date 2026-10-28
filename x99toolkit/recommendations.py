"""Pure recommendation policy. Build and flash code do not depend on host CPUs."""
from .cpu import CpuDetection

FEATURES = ('tbu', 'secureboot', 'optimized', 'overclock', 'splash')


def firmware_availability(report):
    if report is None:
        return dict.fromkeys(FEATURES, False)
    return dict(tbu=bool(report.tbu.get('cstates', {}).get('supported') and report.tbu.get('target_count') == 1),
        secureboot=bool(report.secure_boot.get('all_variables_valid')),
        optimized=bool(report.optimized.get('supported')), overclock=bool(report.overclock.get('supported')),
        splash=bool(report.splash.get('supported')))


def recommended_modifications(cpu: CpuDetection, report):
    available = firmware_availability(report)
    if report is None:
        return ()
    current_sb = report.secure_boot.get('has_complete_2023_set') and not report.secure_boot.get('has_ami_test_pk')
    needed = dict(tbu=cpu.recommend_tbu and report.tbu.get('status') != 'PASS',
        secureboot=not current_sb, optimized=not report.optimized.get('matches'),
        overclock=cpu.recommend_oc and not report.overclock.get('matches'),
        splash=not report.splash.get('already_present'))
    return tuple(key for key in FEATURES if available[key] and needed[key])
