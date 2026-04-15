"""
Worker-level governance plugin for the Agentic Fabric.

Subclasses AgentToolHandler to intercept every tool call within a Strands agent
and evaluate it against the governance policy. This is the fine-grained,
agent-level governance layer — distinct from the Arbiter-level dispatch governance.

Uses the preprocess() hook in Strands 0.1.x AgentToolHandler.
"""

import os
import time
import uuid
import json
import boto3
from typing import Any, Optional, Set

from strands.handlers.tool_handler import AgentToolHandler
from strands.types.tools import ToolUse, ToolResult, ToolConfig


class GovernedToolHandler(AgentToolHandler):
    """
    Tool handler that evaluates governance policy before every tool execution.
    If preprocess() returns a ToolResult, the tool call is short-circuited.
    If preprocess() returns None, the tool executes normally.
    """

    def __init__(
        self,
        tool_registry,
        agent_id: str,
        workflow_id: str,
        denied_tools: Set[str] = None,
    ):
        super().__init__(tool_registry)
        self.agent_id = agent_id
        self.workflow_id = workflow_id
        self.denied_tools = denied_tools or set()

        # Load from env var if not passed directly
        if not self.denied_tools:
            denied_env = os.environ.get('DENIED_TOOLS', '')
            self.denied_tools = set(t.strip() for t in denied_env.split(',') if t.strip())

        self._ledger_table_name = os.environ.get('GOVERNANCE_LEDGER_TABLE')
        self._dynamodb = boto3.resource('dynamodb') if self._ledger_table_name else None

    def preprocess(
        self,
        tool: ToolUse,
        tool_config: ToolConfig,
        **kwargs: Any,
    ) -> Optional[ToolResult]:
        """
        Pre-execution governance check. Deterministic — no LLM.
        Returns a denial ToolResult if the tool call is not permitted.
        Returns None to allow the tool call to proceed.
        """
        tool_name = tool.get('name', '')
        tool_use_id = tool.get('toolUseId', '')

        if tool_name in self.denied_tools:
            finding_id = str(uuid.uuid4())
            self._write_finding(
                finding_id=finding_id,
                tool_name=tool_name,
                decision='deny',
                reason=f'tool_denied:explicit_deny_list:{tool_name}',
            )
            print(f"Governance DENY tool '{tool_name}' for agent '{self.agent_id}' [finding:{finding_id}]")
            return ToolResult(
                toolUseId=tool_use_id,
                status='error',
                content=[{
                    'text': f"Tool '{tool_name}' is not authorised for this agent. Governance finding: {finding_id}"
                }],
            )

        # Permit — write legibility record
        finding_id = str(uuid.uuid4())
        self._write_finding(
            finding_id=finding_id,
            tool_name=tool_name,
            decision='permit',
            reason='tool_permitted:no_constraints_violated',
        )

        return None  # Allow execution to proceed

    def _write_finding(self, finding_id: str, tool_name: str, decision: str, reason: str):
        """Write a legibility record for this tool call evaluation."""
        if not self._dynamodb or not self._ledger_table_name:
            return
        try:
            table = self._dynamodb.Table(self._ledger_table_name)
            table.put_item(Item={
                'findingId': finding_id,
                'workflowId': self.workflow_id,
                'timestamp': str(time.time()),
                'decision': decision,
                'requestingAgent': self.agent_id,
                'targetAgent': f'tool:{tool_name}',
                'scopeEvaluated': 'worker-tool-handler',
                'contractEvaluated': 'none',
                'reason': reason,
                'escalationTarget': 'none',
                'residualAuthorityDenial': False,
                'ttl': int(time.time()) + (90 * 24 * 3600),
            })
        except Exception as e:
            print(f"GOVERNANCE LEDGER WRITE FAILED: {e}")
