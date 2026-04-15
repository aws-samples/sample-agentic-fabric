"""
Governance ledger — write-once legibility records.

Every GovernanceFinding is written to DynamoDB immediately after evaluation.
The ledger is write-once: never updated, never deleted (TTL-managed retention).
If the write fails, the engine raises rather than proceeding silently.
"""

import os
import boto3
from .models import GovernanceFinding

_dynamodb = boto3.resource('dynamodb')

RETENTION_DAYS = 90


def write_finding(finding: GovernanceFinding) -> None:
    """Write a governance finding to the ledger table. Raises on failure."""
    table_name = os.environ.get('GOVERNANCE_LEDGER_TABLE')
    if not table_name:
        raise RuntimeError("GOVERNANCE_LEDGER_TABLE not configured — cannot produce legibility record")

    table = _dynamodb.Table(table_name)
    table.put_item(Item={
        'findingId': finding.finding_id,
        'workflowId': finding.workflow_id,
        'timestamp': str(finding.timestamp),
        'decision': finding.decision.value,
        'requestingAgent': finding.requesting_agent,
        'targetAgent': finding.target_agent,
        'scopeEvaluated': finding.scope_evaluated or 'none',
        'contractEvaluated': finding.contract_evaluated or 'none',
        'reason': finding.reason,
        'escalationTarget': finding.escalation_target or 'none',
        'residualAuthorityDenial': finding.residual_authority_denial,
        'ttl': int(finding.timestamp) + (RETENTION_DAYS * 24 * 3600),
    })
