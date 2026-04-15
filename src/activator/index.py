import json
import os
import time
import boto3

AGENT_CONFIG_TABLE = os.environ.get('AGENT_CONFIG_TABLE')
dynamodb = boto3.resource('dynamodb')


def activate_agent(agent_id, activated_by='manual'):
    """Set an agent's state to 'active' and record activation metadata."""
    table = dynamodb.Table(AGENT_CONFIG_TABLE)
    response = table.update_item(
        Key={'agentId': agent_id},
        UpdateExpression='SET #state = :active, activatedAt = :ts, activatedBy = :by',
        ExpressionAttributeNames={'#state': 'state'},
        ExpressionAttributeValues={
            ':active': 'active',
            ':ts': int(time.time()),
            ':by': activated_by,
        },
        ConditionExpression='attribute_exists(agentId)',
        ReturnValues='ALL_NEW'
    )
    return response.get('Attributes')


def suspend_agent(agent_id):
    """Set an agent's state to 'suspended'."""
    table = dynamodb.Table(AGENT_CONFIG_TABLE)
    response = table.update_item(
        Key={'agentId': agent_id},
        UpdateExpression='SET #state = :suspended',
        ExpressionAttributeNames={'#state': 'state'},
        ExpressionAttributeValues={':suspended': 'suspended'},
        ConditionExpression='attribute_exists(agentId)',
        ReturnValues='ALL_NEW'
    )
    return response.get('Attributes')


def handler(event, context):
    print(f"Received event: {json.dumps(event)}")

    detail = event.get('detail', {})
    if isinstance(detail, str):
        detail = json.loads(detail)

    agent_id = detail.get('agent_id')
    action = detail.get('action', 'activate')
    activated_by = detail.get('activated_by', 'manual')

    if not agent_id:
        print("No agent_id in event detail")
        return {'statusCode': 400, 'body': 'Missing agent_id'}

    try:
        if action == 'activate':
            result = activate_agent(agent_id, activated_by)
            print(f"Agent {agent_id} activated: {json.dumps(result, default=str)}")
        elif action == 'suspend':
            result = suspend_agent(agent_id)
            print(f"Agent {agent_id} suspended: {json.dumps(result, default=str)}")
        else:
            print(f"Unknown action: {action}")
            return {'statusCode': 400, 'body': f'Unknown action: {action}'}

        return {'statusCode': 200, 'body': json.dumps(result, default=str)}

    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        print(f"Agent {agent_id} does not exist")
        return {'statusCode': 404, 'body': f'Agent {agent_id} not found'}
    except Exception as e:
        print(f"Error processing activation: {e}")
        return {'statusCode': 500, 'body': str(e)}
