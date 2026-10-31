"""Choose the legacy warning only for its original, verified source context."""


def confirmation_key(plan, source_sha256, own_dump_hashes):
    secureboot_only = bool(plan and plan.get('secureboot') and
        not any(plan.get(key) for key in ('tbu', 'optimized', 'overclock', 'splash')))
    return 'fpt.confirm_secureboot' if secureboot_only and source_sha256 in own_dump_hashes else 'fpt.confirm'
