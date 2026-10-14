"""Trusted caller identity and agent-registry contracts for ALISA.

The provider must resolve the authenticated caller from trusted runtime context.
The registry is the authoritative source for registered agent identity and
faction membership. Neither identity nor faction should be accepted from
untrusted request fields.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AgentIdentity:
    """Authoritative identity record returned by the ALISA agent registry."""

    agent_id: str
    faction_id: str


class AgentIdentityProvider(Protocol):
    """Adapter to the runtime's authenticated current-agent context."""

    def resolve_current_agent_id(self) -> str | None:
        """Return the authenticated agent ID, or None if unavailable."""
        ...


class AgentRegistry(Protocol):
    """Lookup interface backed by ALISA's authoritative agent registry."""

    def get_agent(self, agent_id: str) -> AgentIdentity | None:
        """Return the registered identity, or None if the agent is unknown."""
        ...
