from decimal import Decimal
import os
import time
from typing import Any
import boto3
from boto3.dynamodb.conditions import Key

CONFIG_TABLE = os.environ.get('AGENT_CONFIG_TABLE')
dynamodb = boto3.resource('dynamodb')

# Module-level cache for Lambda container reuse
_config_cache = None
_cache_expiry = 0
CACHE_TTL_SECONDS = 60

# Needed because DDB likes to throw decimals in
def parse_decimals(data: Any) -> Any:
    """Recursively converts Decimal instances to int (if whole) or float."""
    if isinstance(data, Decimal):
        return int(data) if data % 1 == 0 else float(data)
    elif isinstance(data, dict):
        return {k: parse_decimals(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [parse_decimals(item) for item in data]
    else:
        return data


def load_config_from_dynamodb(force_reload=False):
    global _config_cache, _cache_expiry

    if not force_reload and _config_cache is not None and time.time() < _cache_expiry:
        print(f"Using cached agent configs ({len(_config_cache['agents'])} agents)")
        return _config_cache

    print(f"Loading active agents from {CONFIG_TABLE} via state-index GSI")
    table = dynamodb.Table(CONFIG_TABLE)
    response = table.query(
        IndexName='state-index',
        KeyConditionExpression=Key('state').eq('active')
    )
    items = response['Items']
    configs = [item['config'] for item in items]
    print(f"Loaded {len(configs)} active agents")

    _config_cache = {'agents': configs}
    _cache_expiry = time.time() + CACHE_TTL_SECONDS
    return _config_cache


def create_agent_specs(agents_config):
    return [{
        "toolSpec": {
            "name": agent["name"],
            "description": agent["description"],
            "inputSchema": {"json": parse_decimals(agent["schema"])}
        }
    } for agent in agents_config["agents"]]