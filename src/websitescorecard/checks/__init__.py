"""Check registry."""

from __future__ import annotations

from typing import Callable

from websitescorecard.checks.base import Check
from websitescorecard.checks.ssl import SSLCheck
from websitescorecard.checks.footer_vendor import FooterVendorCheck

CheckFactory = Callable[[], Check]

CHECK_REGISTRY: dict[str, CheckFactory] = {
    "ssl": SSLCheck,
    "footer_vendor": FooterVendorCheck,
}


def get_check(name: str, **kwargs) -> Check:
    factory = CHECK_REGISTRY.get(name)
    if factory is None:
        raise ValueError(f"Unknown check: {name!r}")
    return factory(**kwargs)


def resolve_checks(names: list[str], **kwargs) -> list[Check]:
    seen: set[str] = set()
    checks: list[Check] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        checks.append(get_check(name, **kwargs))
    return checks
