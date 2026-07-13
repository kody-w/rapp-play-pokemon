"""Credential-free test shim for the canonical RAPP BasicAgent contract."""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class BasicAgent:
    def __init__(self, name=None, metadata=None):
        if name is not None:
            self.name = name
        elif not hasattr(self, "name"):
            self.name = "BasicAgent"
        if metadata is not None:
            self.metadata = metadata
        elif not hasattr(self, "metadata"):
            self.metadata = {
                "name": self.name,
                "description": "Test-only canonical contract shim.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            }

    def perform(self, **kwargs):
        del kwargs
        return "Not implemented."

    def system_context(self):
        return None

    def to_tool(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.metadata.get("description", ""),
                "parameters": self.metadata.get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            },
        }


agents_module = types.ModuleType("agents")
agents_module.__path__ = []
basic_module = types.ModuleType("agents.basic_agent")
basic_module.BasicAgent = BasicAgent
agents_module.basic_agent = basic_module
sys.modules.setdefault("agents", agents_module)
sys.modules.setdefault("agents.basic_agent", basic_module)
