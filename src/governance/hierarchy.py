"""
Constitutional hierarchy loader.

Loads authority units, composition contracts, and case law from DynamoDB
at Lambda cold start. Cached per container lifetime.
"""

import json
import os
import time
from typing import List, Tuple
import boto3

from .models import (
    AuthorityUnit, AuthorityScope, CompositionContract,
    CaseLawEntry, ArbitrationDecision, ConflictResolution,
    ConstitutionalLayer,
)

_dynamodb = boto3.resource('dynamodb')

# Module-level cache (persists for Lambda container lifetime)
_authority_units: List[AuthorityUnit] = []
_contracts: List[CompositionContract] = []
_case_law: List[CaseLawEntry] = []
_constitutional_layers: List[ConstitutionalLayer] = []
_loaded = False
_load_time = 0
CACHE_TTL_SECONDS = 300  # 5 minutes


def load_governance_state(force_reload=False) -> Tuple[
    List[AuthorityUnit], List[CompositionContract], List[CaseLawEntry], List[ConstitutionalLayer]
]:
    """
    Load authority units, contracts, case law, and constitutional layers from DynamoDB.
    Cached per Lambda container with TTL.
    """
    global _authority_units, _contracts, _case_law, _constitutional_layers, _loaded, _load_time

    if not force_reload and _loaded and time.time() < _load_time + CACHE_TTL_SECONDS:
        return _authority_units, _contracts, _case_law, _constitutional_layers

    _authority_units = _load_authority_units()
    _contracts = _load_contracts()
    _case_law = _load_case_law()
    _constitutional_layers = _load_constitutional_layers()
    _loaded = True
    _load_time = time.time()

    print(f"Governance state loaded: {len(_authority_units)} units, "
          f"{len(_contracts)} contracts, {len(_case_law)} case law entries, "
          f"{len(_constitutional_layers)} constitutional layers")

    return _authority_units, _contracts, _case_law, _constitutional_layers


def _load_authority_units() -> List[AuthorityUnit]:
    table_name = os.environ.get('AUTHORITY_UNITS_TABLE')
    if not table_name:
        return []
    table = _dynamodb.Table(table_name)
    items = table.scan()['Items']
    result = []
    for item in items:
        scope_data = item.get('scope', '{}')
        if isinstance(scope_data, str):
            scope_data = json.loads(scope_data)
        scope = AuthorityScope(
            decision_type=scope_data.get('decision_type', '*'),
            domain=scope_data.get('domain', '*'),
            conditions=scope_data.get('conditions', {}),
            limits=scope_data.get('limits', {}),
        )
        result.append(AuthorityUnit(
            unit_id=item['unitId'],
            agent_id=item['agentId'],
            scope=scope,
            delegation_source=item.get('delegationSource'),
            can_redelegate=item.get('canRedelegate', False),
            expiry_timestamp=float(item['expiryTimestamp']) if item.get('expiryTimestamp') else None,
            revoked=item.get('revoked', False),
            risk_rating=item.get('riskRating', 'low'),
        ))
    return result


def _load_contracts() -> List[CompositionContract]:
    table_name = os.environ.get('COMPOSITION_CONTRACTS_TABLE')
    if not table_name:
        return []
    table = _dynamodb.Table(table_name)
    items = table.scan()['Items']
    result = []
    for item in items:
        scope_data = item.get('scope', '{}')
        if isinstance(scope_data, str):
            scope_data = json.loads(scope_data)
        scope = AuthorityScope(
            decision_type=scope_data.get('decision_type', '*'),
            domain=scope_data.get('domain', '*'),
            conditions=scope_data.get('conditions', {}),
            limits=scope_data.get('limits', {}),
        )
        result.append(CompositionContract(
            contract_id=item['contractId'],
            party_a=item['partyA'],
            party_b=item['partyB'],
            authority_precedence=item.get('authorityPrecedence', 'none'),
            invariants=json.loads(item['invariants']) if isinstance(item.get('invariants'), str) else item.get('invariants', []),
            conflict_resolution=ConflictResolution(item.get('conflictResolution', 'default_deny')),
            stop_rights=json.loads(item['stopRights']) if isinstance(item.get('stopRights'), str) else item.get('stopRights', []),
            scope=scope,
            escalation_path=item.get('escalationPath'),
        ))
    return result


def _load_case_law() -> List[CaseLawEntry]:
    table_name = os.environ.get('CASE_LAW_TABLE')
    if not table_name:
        return []
    table = _dynamodb.Table(table_name)
    items = table.scan()['Items']
    result = []
    for item in items:
        # Skip revoked entries
        if not item.get('active', True):
            continue
        result.append(CaseLawEntry(
            case_id=item['caseId'],
            pattern=json.loads(item['pattern']) if isinstance(item.get('pattern'), str) else item.get('pattern', {}),
            resolution=ArbitrationDecision(item['resolution']),
            encoded_at=float(item.get('encodedAt', 0)),
            encoded_by=item.get('encodedBy', 'unknown'),
            scope_of_applicability=json.loads(item['scopeOfApplicability']) if isinstance(item.get('scopeOfApplicability'), str) else item.get('scopeOfApplicability', {}),
            precedence=int(item.get('precedence', 0)),
        ))
    return sorted(result, key=lambda e: -e.precedence)


def _load_constitutional_layers() -> List[ConstitutionalLayer]:
    table_name = os.environ.get('CONSTITUTIONAL_LAYERS_TABLE')
    if not table_name:
        return []
    table = _dynamodb.Table(table_name)
    items = table.scan()['Items']
    result = []
    for item in items:
        rules_data = item.get('rules', '[]')
        if isinstance(rules_data, str):
            rules_data = json.loads(rules_data)
        applies_to = item.get('appliesTo', '[]')
        if isinstance(applies_to, str):
            applies_to = json.loads(applies_to)
        result.append(ConstitutionalLayer(
            layer_id=item['layerId'],
            layer_type=item.get('layerType', 'global'),
            applies_to=applies_to,
            rules=rules_data,
            parent_layer_id=item.get('parentLayerId'),
        ))
    return result
