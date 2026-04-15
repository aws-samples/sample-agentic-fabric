"""
Fabric Memory — structured operational memory for the Arbiter.

Provides cross-workflow context by tracking agent metrics, workflow outcomes,
and building the dispatch context dict that populates DispatchRequest.context
for governance evaluation.

All functions are deterministic — no LLM calls. Error posture: metric writes
are best-effort (try/except with logging). A failure to write metrics must
never block dispatch or cause a Lambda failure.
"""

import json
import os
import time
import boto3
from decimal import Decimal
from typing import Any, Optional

_dynamodb = boto3.resource('dynamodb')

AGENT_METRICS_TABLE = os.environ.get('AGENT_METRICS_TABLE')
WORKFLOW_OUTCOMES_TABLE = os.environ.get('WORKFLOW_OUTCOMES_TABLE')
GOVERNANCE_LEDGER_TABLE = os.environ.get('GOVERNANCE_LEDGER_TABLE')


# --- Agent Metrics ---

def load_agent_metrics(agent_id: str) -> Optional[dict]:
    """Point read from AGENT_METRICS_TABLE. Returns dict or None."""
    if not AGENT_METRICS_TABLE:
        return None
    try:
        table = _dynamodb.Table(AGENT_METRICS_TABLE)
        response = table.get_item(Key={'agentId': agent_id})
        return response.get('Item')
    except Exception as e:
        print(f"Failed to load agent metrics for {agent_id}: {e}")
        return None


def increment_agent_invocation(agent_name: str):
    """Atomic ADD invocationCount, SET lastInvokedAt. Called after governance PERMIT."""
    if not AGENT_METRICS_TABLE:
        return
    try:
        table = _dynamodb.Table(AGENT_METRICS_TABLE)
        table.update_item(
            Key={'agentId': agent_name},
            UpdateExpression='ADD invocationCount :one SET lastInvokedAt = :ts, updatedAt = :ts',
            ExpressionAttributeValues={
                ':one': 1,
                ':ts': Decimal(str(time.time())),
            },
        )
    except Exception as e:
        print(f"Failed to increment invocation for {agent_name}: {e}")


def increment_agent_success(agent_name: str, duration_ms: int = 0):
    """Atomic ADD successCount + totalDurationMs. Called on task.completion."""
    if not AGENT_METRICS_TABLE:
        return
    try:
        table = _dynamodb.Table(AGENT_METRICS_TABLE)
        table.update_item(
            Key={'agentId': agent_name},
            UpdateExpression='ADD successCount :one, totalDurationMs :dur SET updatedAt = :ts',
            ExpressionAttributeValues={
                ':one': 1,
                ':dur': duration_ms,
                ':ts': Decimal(str(time.time())),
            },
        )
    except Exception as e:
        print(f"Failed to increment success for {agent_name}: {e}")


def increment_agent_failure(agent_name: str):
    """Atomic ADD failureCount. Called on workerWrapper exception."""
    if not AGENT_METRICS_TABLE:
        return
    try:
        table = _dynamodb.Table(AGENT_METRICS_TABLE)
        table.update_item(
            Key={'agentId': agent_name},
            UpdateExpression='ADD failureCount :one SET updatedAt = :ts',
            ExpressionAttributeValues={
                ':one': 1,
                ':ts': Decimal(str(time.time())),
            },
        )
    except Exception as e:
        print(f"Failed to increment failure for {agent_name}: {e}")


def increment_agent_deny(agent_name: str):
    """Atomic ADD governanceDenyCount. Called after governance DENY."""
    if not AGENT_METRICS_TABLE:
        return
    try:
        table = _dynamodb.Table(AGENT_METRICS_TABLE)
        table.update_item(
            Key={'agentId': agent_name},
            UpdateExpression='ADD governanceDenyCount :one SET updatedAt = :ts',
            ExpressionAttributeValues={
                ':one': 1,
                ':ts': Decimal(str(time.time())),
            },
        )
    except Exception as e:
        print(f"Failed to increment deny for {agent_name}: {e}")


# --- Dispatch Context Builder ---

def build_dispatch_context(
    agent_name: str,
    workflow_id: str,
    orchestration: dict,
) -> dict:
    """
    Build the context dict for DispatchRequest.context.
    All values are deterministic facts — no LLM, no interpretation.
    This is what activates governance scope conditions and limits.
    """
    context = {}

    # Per-agent metrics
    metrics = load_agent_metrics(agent_name)
    if metrics:
        invocations = int(metrics.get('invocationCount', 0))
        denies = int(metrics.get('governanceDenyCount', 0))
        failures = int(metrics.get('failureCount', 0))
        successes = int(metrics.get('successCount', 0))
        total_duration = int(metrics.get('totalDurationMs', 0))

        context['agent_invocation_count'] = invocations
        context['agent_deny_rate_pct'] = (denies / max(invocations, 1)) * 100
        context['agent_failure_rate_pct'] = (failures / max(invocations, 1)) * 100
        context['agent_avg_duration_ms'] = total_duration / max(successes, 1)

    # Per-workflow state
    context['workflow_fabrication_pending'] = orchestration.get('pending_fabrication', False)

    # Count distinct agents used in this workflow from conversation history
    agents_used = set()
    for msg in orchestration.get('conversation', []):
        for content in msg.get('content', []):
            if 'toolUse' in content:
                agents_used.add(content['toolUse']['name'])
    context['workflow_agent_count'] = len(agents_used)

    # Per-workflow governance summary (from accumulated deny/escalate tracking)
    context['workflow_deny_count'] = orchestration.get('_deny_count', 0)
    context['workflow_escalate_count'] = orchestration.get('_escalate_count', 0)

    return context


# --- Workflow Outcome Writer ---

def write_workflow_outcome(
    orchestration: dict,
    status: str,
    agents_used: list,
    deny_count: int = 0,
    escalate_count: int = 0,
) -> None:
    """Terminal write to WORKFLOW_OUTCOMES_TABLE. Called once when workflow reaches terminal state."""
    if not WORKFLOW_OUTCOMES_TABLE:
        return
    try:
        table = _dynamodb.Table(WORKFLOW_OUTCOMES_TABLE)
        started_at = orchestration.get('instance', 0)
        completed_at = int(time.time())
        original_task = ''
        conversation = orchestration.get('conversation', [])
        if conversation:
            first_content = conversation[0].get('content', [])
            if first_content and 'text' in first_content[0]:
                original_task = first_content[0]['text'][:500]

        table.put_item(Item={
            'workflowId': orchestration['workflowId'],
            'status': status,
            'startedAt': started_at,
            'completedAt': completed_at,
            'durationSeconds': completed_at - started_at if started_at else 0,
            'agentsUsed': agents_used,
            'fabricationTriggered': orchestration.get('pending_fabrication', False),
            'governanceDenyCount': deny_count,
            'governanceEscalateCount': escalate_count,
            'taskSummary': original_task,
            'ttl': completed_at + (90 * 24 * 3600),
        })
        print(f"Workflow outcome written: {orchestration['workflowId']} status={status}")
    except Exception as e:
        print(f"Failed to write workflow outcome: {e}")


# --- System Prompt Enrichment ---

def build_operational_context_block(agents_config: dict) -> str:
    """
    Build a text block summarising operational agent health for the system prompt.
    Based purely on AGENT_METRICS_TABLE reads — no LLM, no embeddings.
    """
    if not AGENT_METRICS_TABLE:
        return ''

    lines = []
    for agent in agents_config.get('agents', []):
        metrics = load_agent_metrics(agent['name'])
        if metrics:
            invocations = int(metrics.get('invocationCount', 0))
            if invocations == 0:
                continue
            deny_rate = (int(metrics.get('governanceDenyCount', 0)) / invocations) * 100
            failure_rate = (int(metrics.get('failureCount', 0)) / invocations) * 100
            lines.append(
                f"- {agent['name']}: {invocations} invocations, "
                f"{deny_rate:.0f}% governance deny rate, {failure_rate:.0f}% failure rate"
            )

    if lines:
        return "\n\nAgent operational history (current cycle):\n" + "\n".join(lines)
    return ''
