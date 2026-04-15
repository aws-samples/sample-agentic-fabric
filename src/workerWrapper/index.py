
import json
import os
import time
import boto3
import importlib.util
import sys

from governance_plugin import GovernedToolHandler

CONFIG_TABLE = os.environ.get('AGENT_CONFIG_TABLE')
dynamodb = boto3.resource('dynamodb')

def load_file_from_s3_into_tmp(bucket_name, file_name):
    import boto3
    s3 = boto3.client('s3')
    s3.download_file(bucket_name, f"agents/{file_name}", "/tmp/loaded_module.py")

def load_config_from_dynamodb(agent_name: str):
    print(CONFIG_TABLE)
    table = dynamodb.Table(CONFIG_TABLE)
    response = table.get_item(
        Key={
            'agentId': agent_name
        }
    )
    print(response)
    return response['Item']

def post_task_complete(response, agent_use_id, agent_name, workflow_id):
    client = boto3.client('events')

    COMPLETION_BUS_NAME = os.environ.get('COMPLETION_BUS_NAME')
    event = {
        'Source': 'task.completion',
        'DetailType': 'task.completion',
        'EventBusName': COMPLETION_BUS_NAME,
        'Detail': json.dumps({
            'workflow_id': workflow_id,
            'data': f"Task completed, details: {response}",
            'agent_use_id': agent_use_id,
            'node': agent_name
        })
    }
    print(f"posting event, {json.dumps(event)}")
    response = client.put_events(
        Entries=[
            event
        ]
    )
    print(f"event posted: {response}")
    return f"event posted: {event}"


def _inject_governance(agent_name, workflow_id, denied_tools):
    """
    Patch the Strands AgentToolHandler so dynamically-loaded agents
    get governance-aware tool handling. Returns a restore function.
    """
    try:
        import strands.agent.agent as agent_module
        from strands.handlers.tool_handler import AgentToolHandler
        original_class = AgentToolHandler

        # Create a factory that returns GovernedToolHandler instances
        def governed_handler_factory(tool_registry):
            return GovernedToolHandler(
                tool_registry=tool_registry,
                agent_id=agent_name,
                workflow_id=workflow_id,
                denied_tools=denied_tools,
            )

        # Patch: when Agent.__init__ calls AgentToolHandler(tool_registry=...), it gets our governed version
        agent_module.AgentToolHandler = governed_handler_factory
        print(f"Governance injected for agent '{agent_name}' with denied_tools={denied_tools}")

        def restore():
            agent_module.AgentToolHandler = original_class
        return restore
    except Exception as e:
        print(f"Could not inject governance handler: {e}")
        return lambda: None


def process_event(event, context):
    print("processing...")
    workflow_id = event["workflow_id"]
    agent_use_id = event["agent_use_id"]
    request = event["agent_input"]
    agent_name = event['node']

    agent = load_config_from_dynamodb(agent_name)
    config = agent['config']

    if isinstance(config, str):
        config = json.loads(config)

    # Extract denied tools from agent config if present
    denied_tools_list = agent.get('deniedTools', [])
    if isinstance(denied_tools_list, str):
        denied_tools_list = json.loads(denied_tools_list)
    denied_tools = set(denied_tools_list)

    fileName = config['filename']
    print("loading file from s3...")
    load_file_from_s3_into_tmp(os.environ["AGENT_BUCKET_NAME"], fileName)

    # Inject governance before loading the agent module
    restore_handler = _inject_governance(agent_name, workflow_id, denied_tools)

    print("importing module...")
    spec = importlib.util.spec_from_file_location("module.name", "/tmp/loaded_module.py")
    foo = importlib.util.module_from_spec(spec)
    sys.modules["module.name"] = foo
    spec.loader.exec_module(foo)
    try:
        print("attempting to use module")
        response = foo.handler(**request)
        print(f"response: {response}")
    except Exception as e:
        print(f"error running module: {e}")
        response = "The task could not be completed, this agent has issues, please ignore for now."
        # Record failure metric
        try:
            metrics_table = os.environ.get('AGENT_METRICS_TABLE')
            if metrics_table:
                from decimal import Decimal
                _ddb = boto3.resource('dynamodb')
                _ddb.Table(metrics_table).update_item(
                    Key={'agentId': agent_name},
                    UpdateExpression='ADD failureCount :one SET updatedAt = :ts',
                    ExpressionAttributeValues={':one': 1, ':ts': Decimal(str(time.time()))},
                )
        except Exception as me:
            print(f"Failed to record agent failure metric: {me}")
    finally:
        restore_handler()

    post_task_complete(response, agent_use_id, agent_name, workflow_id)


def lambda_handler(event, context):
    print(f"processing event {event}")
    batch_item_failures = []
    
    for record in event['Records']:
        try:
            message_body = json.loads(record['body'])
            print(f"Processing message: {record['messageId']}")
            process_event(message_body, context)
            print(f"Successfully processed message: {record['messageId']}")
        except Exception as e:
            print(f"Error processing message {record['messageId']}: {e}")
            # Add to batch failures so this message will be retried
            batch_item_failures.append({"itemIdentifier": record['messageId']})
    
    # Return batch item failures for partial batch response
    return {"batchItemFailures": batch_item_failures}

if __name__ == "__main__":
    lambda_handler(
        # Grab a record from your lambda and invoke, configuration will vary drastically
        {'Records': []}        ,{}
    )