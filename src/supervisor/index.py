
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

import json
from typing import Any
import boto3
import os
from agent_config import load_config_from_dynamodb, create_agent_specs, parse_decimals
from memory import (
    build_dispatch_context, increment_agent_invocation, increment_agent_deny,
    increment_agent_success, write_workflow_outcome, build_operational_context_block,
)
import uuid
import time

MODEL_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0"

EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME')
WORKFLOW_TABLE = os.environ.get('WORKFLOW_TABLE')
WORKER_STATE_TABLE = os.environ.get('WORKER_STATE_TABLE')
FABRICATOR_QUEUE_URL = os.environ.get('FABRICATOR_QUEUE_URL')
GOVERNANCE_BYPASS = os.environ.get('GOVERNANCE_BYPASS', 'false').lower() == 'true'
ESCALATION_TOPIC_ARN = os.environ.get('ESCALATION_TOPIC_ARN')

sqs = boto3.client('sqs')
sns_client = boto3.client('sns')
dynamodb = boto3.resource('dynamodb')
bedrock = boto3.client('bedrock-runtime', region_name='us-west-2')
events_client = boto3.client('events')



SYSTEM_PROMPT = [{
    "text": """You are the Supervisor Agent responsible for autonomously coordinating and completing workflows on behalf of the user. Your role is to translate user requests into actionable plans, delegate tasks to the most suitable agents, and ensure successful end-to-end delivery — even when all required steps are not known upfront.

Your responsibilities:

1. Interpret & Plan
   - Convert the user’s request into a clear objective and a structured execution plan.
   - If key details are missing, infer reasonable assumptions rather than asking the user.
   - Break work into parallel tasks whenever possible to optimise speed and efficiency.

2. Delegate & Orchestrate
   - Select the most appropriate agents for each task based on their capabilities.
   - Issue multiple agent calls in parallel when tasks are independent.
   - If an agent requires information that the user did not provide, you must generate or infer the required input yourself.

3. Monitor & Adapt
   - Track progress, validate outputs, and handle failure or ambiguity autonomously.
   - If a task returns unclear or incomplete results, refine the task or re-delegate.
   - Adjust the plan as new information emerges—tasks may be iterative or exploratory.

4. Quality & Completion**
   - Ensure final output meets the user’s intent and quality expectations.
   - Compile results, summarise outcomes, and deliver a coherent final response to the user.

Rules of Engagement:
- Do not ask the user follow-up questions after their initial request, unless clarification is absolutely required for safety or correctness.
- Prefer autonomy, initiative, and inference over user re-engagement.
- Use agents as the primary mechanism for action—not yourself.
- Always aim to complete the request in the fewest number of interaction rounds.
- If no agent exists for a required step, propose a workaround or simulated execution.

Your goal is to behave as a highly autonomous supervisory system that can manage uncertainty, discover required tasks on the fly, and drive efficient, agent-based execution to fulfill the user's intent."""
}]


def create_workflow_tracking_record(nodes: list[str]):
    request_id = str(uuid.uuid4())
    if len(nodes) == 0:
        return

    item = {
        "requestId": request_id,
    }

    data = {}

    for node in nodes:
        item[node] = False
        data[node] = None

    item['data'] = data

    table = dynamodb.Table(WORKER_STATE_TABLE)
    table.put_item(
        TableName=WORKER_STATE_TABLE,
        Item=item
    )

    return request_id


def update_workflow_tracking(node: str, request_id: str, data: Any) -> bool:
    table = dynamodb.Table(WORKER_STATE_TABLE)

    response = table.update_item(
        Key={
            "requestId": request_id
        },
        UpdateExpression="SET #node = :completed, #data.#node = :node_data",
        ExpressionAttributeNames={
            "#node": node,
            "#data": "data"
        },
        ExpressionAttributeValues={
            ":completed": True,
            ":node_data": data
        },
        ReturnValues="ALL_NEW"
    )

    updated_item = response.get("Attributes", {})
    all_completed = True

    for key, value in updated_item.items():
        if key not in ["requestId", "data"] and value is False:
            all_completed = False
            break

    return all_completed, response


def create_orchestration(conversation, callback=None, is_external=False):
    instance = int(time.time())

    item = {
        'workflowId': str(uuid.uuid4()),
        'instance': instance,
        'conversation': conversation,
        'isExternal': is_external,
    }
    
    if callback:
        item['callback'] = callback
    
    return item


def save_orchestration(orchestration):
    table = dynamodb.Table(WORKFLOW_TABLE)
    table.put_item(
        TableName=WORKFLOW_TABLE,
        Item=orchestration
    )


def load_orchestration(workflow_id=None):
    if workflow_id is None:
        return None
    else:
        table = dynamodb.Table(WORKFLOW_TABLE)
        response = table.get_item(Key={'workflowId': workflow_id})
        return response['Item']


def process_agent_call(agents_config, orchestration, agent_name, agent_input, agent_use_id):
    agent_config = next(
        (agent for agent in agents_config['agents'] if agent['name'] == agent_name), None)

    if agent_config is None:
        print(f"Agent {agent_name} not found in configuration.")
        return

    action = agent_config["action"]
    action_type = action["type"]
    target = action["target"]
    is_external = orchestration.get('isExternal', False)
    
    payload = {
        "agent_input": agent_input,
        "workflow_id": orchestration["workflowId"],
        "agent_use_id": agent_use_id,
        "node": agent_name,
        "isExternal": is_external
    }

    print(f"Sending payload to {action_type} queue: {target}")
    print(f"Payload: {json.dumps(payload, default=str)}")

    # Publish to EventBridge for chatter visibility
    if EVENT_BUS_NAME:
        try:
            events_client.put_events(
                Entries=[
                    {
                        'Source': 'supervisor',
                        'DetailType': 'chatter',
                        'Detail': json.dumps({
                            'action': 'agent_call',
                            'agent_name': agent_name,
                            'agent_input': agent_input,
                            'workflow_id': orchestration["workflowId"],
                            'agent_use_id': agent_use_id,
                            'target': target,
                            'timestamp': time.time()
                        }, default=str),
                        'EventBusName': EVENT_BUS_NAME
                    }
                ]
            )
            print(f"Published supervisor message to EventBridge")
        except Exception as e:
            print(f"Error publishing to EventBridge: {e}")

    # All agent types use SQS queues for async invocation
    # The queue's consumer Lambda handles the specific invocation logic
    if action_type in ["sqs", "agentcore", "a2a"]:
        response = sqs.send_message(
            QueueUrl=target,
            MessageBody=json.dumps(payload)
        )
        print(f"SQS send_message response: {json.dumps(response, default=str)}")
        return response
    else:
        print(f"Unknown action type: {action_type}")
        return None


def governed_process_agent_call(agents_config, orchestration, agent_name, agent_input, agent_use_id):
    """
    Control-surface band: governance evaluation before every agent dispatch.
    Sits between 'Bedrock selects a tool' and 'SQS message is sent'.
    """
    # Always increment metrics on dispatch attempt (even in bypass mode)
    if GOVERNANCE_BYPASS:
        increment_agent_invocation(agent_name)
        return process_agent_call(agents_config, orchestration, agent_name, agent_input, agent_use_id)

    try:
        from governance.engine import GovernanceEngine
        from governance.hierarchy import load_governance_state
        from governance.ledger import write_finding
        from governance.models import DispatchRequest, ArbitrationDecision
    except ImportError as e:
        print(f"Governance layer not available ({e}), falling back to ungoverned dispatch")
        increment_agent_invocation(agent_name)
        return process_agent_call(agents_config, orchestration, agent_name, agent_input, agent_use_id)

    # Load governance state (cached per container)
    authority_units, contracts, case_law, constitutional_layers = load_governance_state()
    engine = GovernanceEngine(authority_units, contracts, case_law, constitutional_layers)

    # Resolve domain from agent config
    agent_cfg = next((a for a in agents_config.get('agents', []) if a['name'] == agent_name), None)
    domain = agent_cfg.get('domain', 'default') if agent_cfg else 'unknown'

    # Build dispatch context from Fabric Memory (activates governance conditions/limits)
    dispatch_context = build_dispatch_context(agent_name, orchestration["workflowId"], orchestration)

    # Build dispatch request
    request = DispatchRequest(
        requesting_agent_id="arbiter",
        target_agent_id=agent_name,
        action_type="invoke_agent",
        domain=domain,
        workflow_id=orchestration["workflowId"],
        agent_use_id=agent_use_id,
        context=dispatch_context,
        agent_input=agent_input,
    )

    # Evaluate (deterministic, no LLM)
    finding = engine.evaluate(request)

    # Write legibility record (mandatory — before dispatch or denial)
    try:
        write_finding(finding)
    except Exception as e:
        print(f"GOVERNANCE LEDGER WRITE FAILED: {e} — halting dispatch")
        return None

    # Act on decision
    if finding.decision == ArbitrationDecision.PERMIT:
        print(f"Governance PERMIT: {finding.reason} [finding:{finding.finding_id}]")
        increment_agent_invocation(agent_name)
        return process_agent_call(agents_config, orchestration, agent_name, agent_input, agent_use_id)

    elif finding.decision == ArbitrationDecision.DENY:
        print(f"Governance DENY: {finding.reason} [finding:{finding.finding_id}]")
        increment_agent_deny(agent_name)
        orchestration['_deny_count'] = orchestration.get('_deny_count', 0) + 1
        return {"denied": True, "reason": finding.reason, "finding_id": finding.finding_id}

    elif finding.decision in (ArbitrationDecision.ESCALATE, ArbitrationDecision.HALT):
        print(f"Governance ESCALATE: {finding.reason} [finding:{finding.finding_id}]")
        _route_escalation(finding)
        orchestration['_escalate_count'] = orchestration.get('_escalate_count', 0) + 1
        return {"escalated": True, "reason": finding.reason, "finding_id": finding.finding_id}

    increment_agent_invocation(agent_name)
    return process_agent_call(agents_config, orchestration, agent_name, agent_input, agent_use_id)


def _route_escalation(finding):
    """Route a governance escalation to the configured SNS topic."""
    if not ESCALATION_TOPIC_ARN:
        print("ESCALATION_TOPIC_ARN not configured, escalation logged only")
        return
    try:
        sns_client.publish(
            TopicArn=ESCALATION_TOPIC_ARN,
            Message=json.dumps({
                'finding_id': finding.finding_id,
                'workflow_id': finding.workflow_id,
                'reason': finding.reason,
                'requesting_agent': finding.requesting_agent,
                'target_agent': finding.target_agent,
                'contract_evaluated': finding.contract_evaluated,
            }, default=str),
            Subject=f"Governance Escalation: {finding.reason[:80]}"
        )
        print(f"Published escalation to SNS: {finding.finding_id}")
    except Exception as e:
        print(f"Error publishing escalation to SNS: {e}")


def trigger_fabrication(task_details, workflow_id, agent_use_id="fabrication-fallback"):
    """Enqueue a fabrication request when no agent can handle the task."""
    if not FABRICATOR_QUEUE_URL:
        print("FABRICATOR_QUEUE_URL not configured, cannot trigger fabrication")
        return False

    payload = {
        "agent_input": {"taskDetails": task_details},
        "workflow_id": workflow_id,
        "agent_use_id": agent_use_id,
        "node": "fabricator"
    }
    print(f"Triggering fabrication for missing capability: {task_details[:200]}")
    sqs.send_message(
        QueueUrl=FABRICATOR_QUEUE_URL,
        MessageBody=json.dumps(payload)
    )
    return True


def invoke_agents_from_conversation(orchestration, agents_config):
    agent_ids = []
    output_message = orchestration["conversation"][-1]
    text_response = None

    print(f'Invoking agents from message: {json.dumps(output_message, default=str)}')
    print(f'Message content: {output_message.get("content", [])}')

    for content in output_message.get('content', []):
        print(f'Processing content item: {json.dumps(content, default=str)}')
        if 'toolUse' in content:
            tool_use = content['toolUse']
            print(f'Found toolUse: {json.dumps(tool_use, default=str)}')
            agent_ids.append(tool_use['name'])
            result = governed_process_agent_call(
                agents_config,
                orchestration,
                tool_use['name'],
                tool_use['input'],
                tool_use['toolUseId']
            )
            print(f'Agent call result: {result}')
        elif 'text' in content:
            text_response = content['text']
            print(f"Text response from model: {text_response}")

    print(f'Total agents invoked: {len(agent_ids)}')
    print(f'Agent IDs: {agent_ids}')

    if len(agent_ids) > 0:
        request_id = create_workflow_tracking_record(agent_ids)
        orchestration["request_id"] = request_id
        print(f'Created workflow tracking with request_id: {request_id}')
    else:
        print('No agents were invoked - model may have responded with text only')

        # Check if this is a fabrication-pending orchestration completing
        # (the model gave a final text response after fabrication retry)
        is_fabrication_retry = orchestration.get('pending_fabrication', False)

        # If this is NOT a fabrication retry and the model couldn't find an agent,
        # attempt to fabricate the missing capability
        if not is_fabrication_retry and text_response and FABRICATOR_QUEUE_URL:
            original_request = orchestration["conversation"][0]["content"][0].get("text", "")
            triggered = trigger_fabrication(
                task_details=original_request,
                workflow_id=orchestration["workflowId"]
            )
            if triggered:
                orchestration["pending_fabrication"] = True
                print("Fabrication triggered, orchestration will resume on agent.fabricated event")
                return

        # Write workflow outcome (terminal state)
        agents_used = list({
            c['toolUse']['name']
            for msg in orchestration.get('conversation', [])
            for c in msg.get('content', [])
            if 'toolUse' in c
        })
        write_workflow_outcome(
            orchestration=orchestration,
            status='completed' if text_response else 'failed',
            agents_used=agents_used,
            deny_count=orchestration.get('_deny_count', 0),
            escalate_count=orchestration.get('_escalate_count', 0),
        )

        # Send final response to callback if orchestration is complete
        callback_info = orchestration.get('callback')
        is_external = orchestration.get('isExternal', False)
        workflow_id = orchestration.get('workflowId')

        if text_response and callback_info:
            print(f"Orchestration complete, sending response to callback")
            send_response(text_response, callback=callback_info, is_external=is_external, workflow_id=workflow_id)

        # Publish supervisor feedback to EventBridge for chatter visibility
        if EVENT_BUS_NAME and text_response:
            try:
                events_client.put_events(
                    Entries=[
                        {
                            'Source': 'supervisor',
                            'DetailType': 'supervisor.feedback',
                            'Detail': json.dumps({
                                'action': 'direct_response',
                                'message': text_response,
                                'workflow_id': orchestration["workflowId"],
                                'timestamp': time.time(),
                                'isExternal': is_external
                            }, default=str),
                            'EventBusName': EVENT_BUS_NAME
                        }
                    ]
                )
                print(f"Published supervisor feedback to EventBridge")
            except Exception as e:
                print(f"Error publishing supervisor feedback to EventBridge: {e}")


def update_orchestration_with_results(results, orchestration):
    tool_results = []
    data_to_save = results['Attributes']['data']

    for key in data_to_save:
        data = data_to_save[key]
        tool_result = {
            "toolResult": {
                "toolUseId": data['agent_use_id'],
                "content": [{"json": {'data': data['data']}}],
            }
        }
        tool_results.append(tool_result)

    orchestration["conversation"].append({
        "role": "user",
        "content": tool_results
    })


def orchestrate(initial_message=None, orchestration=None, callback=None, is_external=False):
    if orchestration is None:
        orchestration = create_orchestration(
            conversation=[{
                "role": "user",
                "content": [{"text": initial_message}],
            }],
            callback=callback,
            is_external=is_external
        )

    agent_configs = load_config_from_dynamodb()
    print(f"Agent configs loaded: {json.dumps(agent_configs, default=str)}")

    # Check if there are any active agents
    if not agent_configs.get('agents') or len(agent_configs['agents']) == 0:
        # Send response back to requester that there are no active agents
        print("No active agents configured")
        callback_info = orchestration.get('callback')
        is_external = orchestration.get('isExternal', False)
        workflow_id = orchestration.get('workflowId')
        send_response("No active agents configured", callback=callback_info, is_external=is_external, workflow_id=workflow_id)
        return
    
    agent_specs = create_agent_specs(agent_configs)
    print(f"Agent specs created: {json.dumps(agent_specs, default=str)}")

    # Enrich system prompt with agent operational history from Fabric Memory
    operational_block = build_operational_context_block(agent_configs)
    dynamic_system_prompt = SYSTEM_PROMPT
    if operational_block:
        dynamic_system_prompt = [{"text": SYSTEM_PROMPT[0]["text"] + operational_block}]

    print(f"Calling Bedrock with conversation: {json.dumps(orchestration['conversation'], default=str)}")

    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=orchestration["conversation"],
        system=dynamic_system_prompt,
        inferenceConfig={
            "maxTokens": 2048,
            "temperature": 0,
        },
        toolConfig={
            "tools": agent_specs,
            # Allow model to automatically select tools
            "toolChoice": {"auto": {}}
        }
    )

    print(f"Bedrock response: {json.dumps(response, default=str)}")
    print(f"Response output message: {json.dumps(response['output']['message'], default=str)}")

    orchestration["conversation"].append(response['output']['message'])

    invoke_agents_from_conversation(
        orchestration, agent_configs
    )

    save_orchestration(orchestration=orchestration)

def send_response(message, callback=None, is_external=False, workflow_id=None):
    """Send response to the default event bus or to a specific callback address"""
    
    # If no callback specified, send to default event bus
    if not callback:
        if not EVENT_BUS_NAME:
            print("EVENT_BUS_NAME not configured and no callback provided")
            return
        
        try:
            events_client.put_events(
                Entries=[
                    {
                        'Source': 'supervisor',
                        'DetailType': 'task.response',
                        'Detail': json.dumps({
                            'message': message,
                            'timestamp': time.time(),
                            'isExternal': is_external,
                            'workflowId': workflow_id
                        }, default=str),
                        'EventBusName': EVENT_BUS_NAME
                    }
                ]
            )
            print(f"Published task response to EventBridge: {message}")
        except Exception as e:
            print(f"Error publishing task response to EventBridge: {e}")
        return
    
    # Handle callback-specific routing
    callback_type = callback.get('type')
    
    if callback_type == 'eventbridge':
        try:
            event_bus_name = callback.get('eventBusName', EVENT_BUS_NAME)
            source = callback.get('source', 'supervisor')
            detail_type = callback.get('detailType', 'task.response')
            
            events_client.put_events(
                Entries=[
                    {
                        'Source': source,
                        'DetailType': detail_type,
                        'Detail': json.dumps({
                            'message': message,
                            'timestamp': time.time(),
                            'callback': callback,
                            'isExternal': is_external,
                            'workflowId': workflow_id
                        }, default=str),
                        'EventBusName': event_bus_name
                    }
                ]
            )
            print(f"Published task response to EventBridge {event_bus_name}: {message}")
        except Exception as e:
            print(f"Error publishing to EventBridge callback: {e}")
    
    elif callback_type == 'sqs':
        try:
            queue_url = callback.get('queueUrl')
            if not queue_url:
                print("SQS callback missing queueUrl")
                return
            
            sqs.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps({
                    'message': message,
                    'timestamp': time.time(),
                    'callback': callback,
                    'isExternal': is_external,
                    'workflowId': workflow_id
                }, default=str)
            )
            print(f"Published task response to SQS {queue_url}: {message}")
        except Exception as e:
            print(f"Error publishing to SQS callback: {e}")
    
    elif callback_type == 'mcp':
        try:
            # MCP server callback - store for external polling or webhook
            # For now, log the callback details
            print(f"MCP callback requested: {json.dumps(callback, default=str)}")
            print(f"MCP response message: {message}")
            
            # TODO: Implement MCP server notification mechanism
            # This could be:
            # 1. Writing to a DynamoDB table that MCP servers poll
            # 2. Invoking a webhook URL if provided
            # 3. Publishing to a dedicated MCP notification queue
            
            mcp_endpoint = callback.get('endpoint')
            if mcp_endpoint:
                # If webhook endpoint provided, attempt HTTP POST
                import urllib.request
                import urllib.error
                
                data = json.dumps({
                    'message': message,
                    'timestamp': time.time(),
                    'callback': callback,
                    'isExternal': is_external,
                    'workflowId': workflow_id
                }).encode('utf-8')
                
                req = urllib.request.Request(
                    mcp_endpoint,
                    data=data,
                    headers={'Content-Type': 'application/json'}
                )
                
                try:
                    with urllib.request.urlopen(req, timeout=10) as response:
                        print(f"MCP webhook response: {response.status}")
                except urllib.error.URLError as e:
                    print(f"Error calling MCP webhook: {e}")
            else:
                print("MCP callback has no endpoint - response logged only")
                
        except Exception as e:
            print(f"Error handling MCP callback: {e}")
    
    else:
        print(f"Unknown callback type: {callback_type}")


def handler(event, lambda_context):
    print(f"Received event: {json.dumps(event)}")
    
    # Check if this is a task completion event from a worker agent
    if 'source' in event and event['source'] == 'task.completion':
        workflow_id = event['detail']['workflow_id']
        try:
            orchestration = load_orchestration(workflow_id)
        except Exception as e:
            print(f"Error loading orchestration: {e}")
            return
        request_id = orchestration['request_id']
        print(f"request id: {request_id}")
        node = event['detail']['node']
        increment_agent_success(node)
        all_completed, results = update_workflow_tracking(
            node, request_id, event['detail'])

        if (all_completed):
            update_orchestration_with_results(
                results=results, orchestration=orchestration)
            
            # Check if this is the final completion and send callback
            parsed_orchestration = parse_decimals(orchestration)
            
            # Continue orchestration to get final response from supervisor
            orchestrate(orchestration=parsed_orchestration)
    
    # Check if a new agent was fabricated — retry the pending orchestration
    elif 'source' in event and event['source'] == 'agent.fabricated':
        print("Agent fabricated event received, retrying pending orchestration")
        workflow_id = event.get('detail', {}).get('workflow_id')
        if workflow_id and workflow_id != '0':
            try:
                orchestration = load_orchestration(workflow_id)
                if orchestration and orchestration.get('pending_fabrication'):
                    # Clear fabrication flag, reload agent configs (cache will refresh), and retry
                    orchestration['pending_fabrication'] = False
                    parsed_orchestration = parse_decimals(orchestration)
                    load_config_from_dynamodb(force_reload=True)
                    orchestrate(orchestration=parsed_orchestration)
                else:
                    print(f"Orchestration {workflow_id} not found or not pending fabrication")
            except Exception as e:
                print(f"Error retrying after fabrication: {e}")
        else:
            print("No workflow_id in fabrication event or direct request (id=0), skipping retry")

    # Check if this is a new task request
    elif 'source' in event and event['source'] == 'task.request':
        print("Processing new task request")
        task_details = event['detail'].get('task', '')
        callback = event['detail'].get('callback')
        is_external = event['detail'].get('isExternal', False)
        
        if callback:
            print(f"Task request includes callback: {json.dumps(callback, default=str)}")
        
        if is_external:
            print(f"Task request is from external agent (isExternal: {is_external})")
        
        if task_details:
            orchestrate(initial_message=task_details, callback=callback, is_external=is_external)
        else:
            print("No task details found in event")
    
    # Fallback for other event types with detail
    elif 'detail' in event:
        print("Processing generic detail event")
        orchestrate(initial_message=json.dumps(event["detail"]))


if __name__ == "__main__":
    handler({
        "source": "task.request",
        "DetailType": "System-Task",
        "detail": "{\"orderId\": \"12345\", \"customerId\": \"C-1234\", \"items\": [\"cheesecake\"]}",
        "EventBusName": "orchestration-bus"
    }, {})