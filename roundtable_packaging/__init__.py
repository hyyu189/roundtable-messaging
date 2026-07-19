"""Release installation support for Roundtable Messaging."""

from __future__ import annotations


VERSION = "0.1.0"
MANIFEST_SCHEMA = "roundtable.install.v1"
MANAGED_MARKER = ".roundtable-managed.json"

TOOLS = (
    "roundtable-init",
    "roundtable-smoke-no-cmux",
    "roundtable-uninstall",
    "rt-ack",
    "rt-claude",
    "rt-codex",
    "rt-codex-daemon",
    "rt-codex-wake",
    "rt-doctor",
    "rt-hermes",
    "rt-inbox",
    "rt-projects",
    "rt-refresh",
    "rt-resolve",
    "rt-say",
    "rt-startup-advisory",
    "rt-stop-gate",
    "rt-wait-inbox",
)

LAUNCH_AGENT_LABELS = (
    "com.roundtable.codex-wake",
    "com.roundtable.codex-app-server",
)
