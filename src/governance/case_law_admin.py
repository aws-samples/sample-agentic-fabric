"""
Case law administration utility.

Encodes, lists, and verifies case law entries in DynamoDB.
This is the mechanism for the case law feedback loop:
  1. Governance engine escalates a conflict it cannot resolve
  2. Human reviews the escalation (via SNS notification)
  3. Human determines resolution and encodes it using this utility
  4. Governance engine handles that conflict class deterministically next time

Usage:
    # Set environment
    export AWS_PROFILE=default
    export CASE_LAW_TABLE=agentic-fabric-case-law

    # Encode a new resolution
    python case_law_admin.py encode \
        --pattern '{"target_agent_id": "risky-agent"}' \
        --resolution deny \
        --reason "Agent risky-agent blocked after security review" \
        --encoded-by "admin@example.com" \
        --precedence 100

    # List all entries
    python case_law_admin.py list

    # Verify an entry matches a test case
    python case_law_admin.py verify --case-id case-abc123 \
        --test '{"target_agent_id": "risky-agent", "action_type": "invoke_agent"}'

    # Revoke an entry
    python case_law_admin.py revoke --case-id case-abc123
"""

import argparse
import json
import os
import sys
import time
import uuid
import boto3
from decimal import Decimal


def get_table():
    table_name = os.environ.get('CASE_LAW_TABLE')
    if not table_name:
        print("ERROR: CASE_LAW_TABLE environment variable not set")
        sys.exit(1)
    dynamodb = boto3.resource('dynamodb')
    return dynamodb.Table(table_name)


def encode(args):
    """Encode a new case law entry from a human-adjudicated escalation."""
    table = get_table()

    pattern = json.loads(args.pattern)
    scope = json.loads(args.scope) if args.scope else {}

    case_id = f"case-{uuid.uuid4().hex[:12]}"
    item = {
        'caseId': case_id,
        'pattern': json.dumps(pattern),
        'resolution': args.resolution,
        'encodedAt': Decimal(str(time.time())),
        'encodedBy': args.encoded_by,
        'scopeOfApplicability': json.dumps(scope),
        'precedence': args.precedence,
        'reason': args.reason,
        'active': True,
    }

    table.put_item(Item=item)
    print(f"Encoded case law entry: {case_id}")
    print(f"  Pattern: {json.dumps(pattern, indent=2)}")
    print(f"  Resolution: {args.resolution}")
    print(f"  Precedence: {args.precedence}")
    print(f"  Encoded by: {args.encoded_by}")


def list_entries(args):
    """List all case law entries."""
    table = get_table()
    response = table.scan()
    items = sorted(response['Items'], key=lambda x: -int(x.get('precedence', 0)))

    if not items:
        print("No case law entries found.")
        return

    for item in items:
        active = item.get('active', True)
        status = "ACTIVE" if active else "REVOKED"
        print(f"[{status}] {item['caseId']} (precedence: {item.get('precedence', 0)})")
        print(f"  Resolution: {item['resolution']}")
        print(f"  Pattern: {item.get('pattern', '{}')}")
        print(f"  Reason: {item.get('reason', 'N/A')}")
        print(f"  Encoded by: {item.get('encodedBy', 'unknown')} at {item.get('encodedAt', 'unknown')}")
        print()


def verify(args):
    """Verify that a case law entry matches a test case."""
    table = get_table()
    response = table.get_item(Key={'caseId': args.case_id})

    if 'Item' not in response:
        print(f"Case law entry {args.case_id} not found.")
        return

    item = response['Item']
    pattern = json.loads(item['pattern']) if isinstance(item['pattern'], str) else item['pattern']
    test_case = json.loads(args.test)

    match = True
    for key, expected in pattern.items():
        actual = test_case.get(key)
        if actual != expected:
            print(f"  MISMATCH: {key} expected={expected}, actual={actual}")
            match = False

    if match:
        print(f"MATCH: Case {args.case_id} would fire with resolution={item['resolution']}")
    else:
        print(f"NO MATCH: Case {args.case_id} would not fire for this test case")


def revoke(args):
    """Revoke a case law entry (soft delete — marks as inactive)."""
    table = get_table()
    response = table.update_item(
        Key={'caseId': args.case_id},
        UpdateExpression='SET active = :inactive, revokedAt = :ts',
        ExpressionAttributeValues={
            ':inactive': False,
            ':ts': Decimal(str(time.time())),
        },
        ConditionExpression='attribute_exists(caseId)',
        ReturnValues='ALL_NEW',
    )
    print(f"Revoked case law entry: {args.case_id}")


def main():
    parser = argparse.ArgumentParser(description='Case law administration utility')
    subparsers = parser.add_subparsers(dest='command', required=True)

    # encode
    enc = subparsers.add_parser('encode', help='Encode a new case law entry')
    enc.add_argument('--pattern', required=True, help='JSON pattern matching conditions')
    enc.add_argument('--resolution', required=True, choices=['permit', 'deny', 'escalate', 'halt'])
    enc.add_argument('--reason', required=True, help='Human-readable reason for this resolution')
    enc.add_argument('--encoded-by', required=True, help='Identifier of the person encoding')
    enc.add_argument('--precedence', type=int, default=100, help='Higher = evaluated first (default: 100)')
    enc.add_argument('--scope', default=None, help='JSON scope of applicability constraints')

    # list
    subparsers.add_parser('list', help='List all case law entries')

    # verify
    ver = subparsers.add_parser('verify', help='Verify a case law entry against a test case')
    ver.add_argument('--case-id', required=True)
    ver.add_argument('--test', required=True, help='JSON test case to match against')

    # revoke
    rev = subparsers.add_parser('revoke', help='Revoke a case law entry')
    rev.add_argument('--case-id', required=True)

    args = parser.parse_args()

    if args.command == 'encode':
        encode(args)
    elif args.command == 'list':
        list_entries(args)
    elif args.command == 'verify':
        verify(args)
    elif args.command == 'revoke':
        revoke(args)


if __name__ == '__main__':
    main()
