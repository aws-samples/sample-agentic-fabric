import json
import os
import boto3
import cfnresponse

def handler(event, context):
    print('Event:', json.dumps(event))

    if event['RequestType'] == 'Delete':
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
        return

    try:
        dynamodb = boto3.resource('dynamodb')
        table_name = os.environ['AGENT_CONFIG_TABLE']
        worker_queue_url = os.environ['WORKER_QUEUE_URL']
        fabricator_queue_url = os.environ['FABRICATOR_QUEUE_URL']
        authority_units_table = os.environ.get('AUTHORITY_UNITS_TABLE')
        constitutional_layers_table = os.environ.get('CONSTITUTIONAL_LAYERS_TABLE')

        table = dynamodb.Table(table_name)

        # Seed fabricator agent
        fabricator_agent = {
            'agentId': 'fabricator',
            'config': {
                'name': 'fabricator',
                'description': 'Creates a capability that may be missing from the set of available tools.',
                'schema': {
                    'type': 'object',
                    'properties': {
                        'taskDetails': {
                            'type': 'string',
                            'description': 'A detailed task description for what the task should entail'
                        }
                    },
                    'required': ['taskDetails']
                },
                'version': '1',
                'action': {
                    'type': 'sqs',
                    'target': fabricator_queue_url
                }
            },
            'state': 'active',
            'categories': ['built-in', 'developer']
        }

        table.put_item(Item=fabricator_agent)
        print(f"Seeded agent: fabricator with queue: {fabricator_queue_url}")

        # Seed authority units for governance
        if authority_units_table:
            auth_table = dynamodb.Table(authority_units_table)

            # Arbiter authority: can invoke any agent in any domain
            auth_table.put_item(Item={
                'unitId': 'arbiter-invoke-all',
                'agentId': 'arbiter',
                'scope': json.dumps({
                    'decision_type': 'invoke_agent',
                    'domain': '*',
                    'conditions': {},
                    'limits': {},
                }),
                'riskRating': 'low',
                'revoked': False,
            })
            print("Seeded authority unit: arbiter-invoke-all")

            # Fabricator authority: can create agents
            auth_table.put_item(Item={
                'unitId': 'fabricator-create-agents',
                'agentId': 'fabricator',
                'scope': json.dumps({
                    'decision_type': 'create_agent',
                    'domain': '*',
                    'conditions': {},
                    'limits': {},
                }),
                'riskRating': 'low',
                'revoked': False,
            })
            print("Seeded authority unit: fabricator-create-agents")
        else:
            print("AUTHORITY_UNITS_TABLE not set, skipping authority unit seeding")

        # Seed global constitution
        if constitutional_layers_table:
            const_table = dynamodb.Table(constitutional_layers_table)

            const_table.put_item(Item={
                'layerId': 'global-constitution',
                'layerType': 'global',
                'appliesTo': json.dumps([]),
                'rules': json.dumps([
                    {
                        'field': 'audit.record_produced',
                        'operator': 'eq',
                        'value': True,
                        'description': 'no_irreversible_action_without_audit_trail'
                    },
                    {
                        'field': 'scope.expansion_under_unconfirmed_state',
                        'operator': 'eq',
                        'value': False,
                        'description': 'no_scope_expansion_under_unconfirmed_state'
                    },
                ]),
            })
            print("Seeded constitutional layer: global-constitution (2 invariants)")
        else:
            print("CONSTITUTIONAL_LAYERS_TABLE not set, skipping constitution seeding")

        cfnresponse.send(event, context, cfnresponse.SUCCESS, {
            'Message': 'Agent config, authority units, and constitution seeded successfully'
        })
    except Exception as e:
        print(f"Error seeding data: {str(e)}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {
            'Message': str(e)
        })