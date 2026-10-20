"""Temporary Windows firewall rules used for one interactive client session.

Covers two traffic classes:
* UDP 5353 in/out for mDNS discovery of the main node.
* TCP 52021 out for the main-node artifact data plane used by conv2d uploads/downloads.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from core.constants import DEFAULT_DATA_PLANE_PORT, DEFAULT_DISCOVERY_PORT
from core.logging_setup import get_logger


logger = get_logger("firewall")


@dataclass(frozen=True)
class _FirewallRule:
    name: str
    direction: str
    protocol: str
    port_argument: str


_RULES = (
    _FirewallRule(
        name=f"Allow SuperWeb-Client UDP {DEFAULT_DISCOVERY_PORT} Inbound",
        direction="in",
        protocol="UDP",
        port_argument=f"localport={DEFAULT_DISCOVERY_PORT}",
    ),
    _FirewallRule(
        name=f"Allow SuperWeb-Client UDP {DEFAULT_DISCOVERY_PORT} Outbound",
        direction="out",
        protocol="UDP",
        port_argument=f"remoteport={DEFAULT_DISCOVERY_PORT}",
    ),
    _FirewallRule(
        name=f"Allow SuperWeb-Client TCP {DEFAULT_DATA_PLANE_PORT} Outbound",
        direction="out",
        protocol="TCP",
        port_argument=f"remoteport={DEFAULT_DATA_PLANE_PORT}",
    ),
)


def _run_netsh(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["netsh", "advfirewall", "firewall", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def add_mdns_rules() -> None:
    """Allow temporary firewall traffic for one interactive client session."""

    for rule in _RULES:
        result = _run_netsh(
            "add",
            "rule",
            f"name={rule.name}",
            f"dir={rule.direction}",
            "action=allow",
            f"protocol={rule.protocol}",
            rule.port_argument,
            "enable=yes",
            "profile=any",
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            logger.error("firewall add failed rule=%s detail=%s", rule.name, detail)
            raise RuntimeError(f"unable to add Windows firewall rule '{rule.name}': {detail}")
        logger.info("firewall rule added name=%s direction=%s %s", rule.name, rule.direction, rule.port_argument)


def remove_mdns_rules() -> None:
    """Remove the temporary firewall rules created for one client run."""

    for rule in _RULES:
        result = _run_netsh("delete", "rule", f"name={rule.name}")
        if result.returncode == 0:
            logger.info("firewall rule removed name=%s", rule.name)
        else:
            detail = (result.stderr or result.stdout).strip()
            logger.warning("firewall rule removal returned non-zero name=%s detail=%s", rule.name, detail)


class WindowsFirewallGuard:
    """Add temporary firewall rules for one interactive client session."""

    def __enter__(self) -> "WindowsFirewallGuard":
        logger.info("installing temporary client firewall rules (mDNS discovery + artifact data plane)")
        add_mdns_rules()
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        logger.info("removing temporary client firewall rules")
        remove_mdns_rules()
