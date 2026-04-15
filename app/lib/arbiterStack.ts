import * as cdk from 'aws-cdk-lib/core';
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sns from "aws-cdk-lib/aws-sns";
import { Queue } from 'aws-cdk-lib/aws-sqs';
import { PolicyStatement, Effect } from 'aws-cdk-lib/aws-iam';
import { PythonFunction } from '@aws-cdk/aws-lambda-python-alpha';
import { Construct } from 'constructs';
import { FabricProps } from '../props';
import path = require('path');

interface ArbiterStackProps extends FabricProps {
  agentEventBus: events.EventBus;
  agentRegisterTable: dynamodb.Table;
  workflowTable: dynamodb.Table;
  workerStateTable: dynamodb.Table;
  fabricatorQueue: Queue;
  // Governance tables
  authorityUnitsTable: dynamodb.Table;
  compositionContractsTable: dynamodb.Table;
  caseLawTable: dynamodb.Table;
  constitutionalLayersTable: dynamodb.Table;
  governanceLedgerTable: dynamodb.Table;
  // Fabric Memory tables
  workflowOutcomesTable: dynamodb.Table;
  agentMetricsTable: dynamodb.Table;
}

export class ArbiterStack extends cdk.Stack {
  public readonly supervisor: PythonFunction;
  public readonly escalationTopic: sns.Topic;

  constructor(scope: Construct, id: string, props: ArbiterStackProps) {
    super(scope, id, props);

    // Governance Lambda Layer — created per-stack to avoid cross-stack export update issues
    const governanceLayer = new lambda.LayerVersion(this, 'GovernanceLayer', {
      code: lambda.Code.fromAsset(path.join(__dirname, '../../src/governance'), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_13.bundlingImage,
          command: [
            'bash', '-c',
            'mkdir -p /asset-output/python/governance && cp -r . /asset-output/python/governance/',
          ],
        },
      }),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_13],
      description: 'Governance engine for Arbiter Lambda',
    });

    // Escalation SNS topic for governance escalations
    this.escalationTopic = new sns.Topic(this, 'GovernanceEscalationTopic', {
      topicName: `${props.appName}-governance-escalation-${props.environment}`,
      displayName: 'Governance Escalation',
    });

    const supervisorLambda = new PythonFunction(this, 'SupervisorAgent', {
      runtime: lambda.Runtime.PYTHON_3_13,
      entry: path.join(__dirname, '../../src/supervisor'),
      handler: 'handler',
      timeout: cdk.Duration.minutes(10),
      memorySize: 1024,
      layers: [governanceLayer],
      environment: {
        WORKFLOW_TABLE: props.workflowTable.tableName,
        COMPLETION_BUS_NAME: props.agentEventBus.eventBusName,
        EVENT_BUS_NAME: props.agentEventBus.eventBusName,
        WORKER_STATE_TABLE: props.workerStateTable.tableName,
        AGENT_CONFIG_TABLE: props.agentRegisterTable.tableName,
        FABRICATOR_QUEUE_URL: props.fabricatorQueue.queueUrl,
        // Governance env vars
        AUTHORITY_UNITS_TABLE: props.authorityUnitsTable.tableName,
        COMPOSITION_CONTRACTS_TABLE: props.compositionContractsTable.tableName,
        CASE_LAW_TABLE: props.caseLawTable.tableName,
        CONSTITUTIONAL_LAYERS_TABLE: props.constitutionalLayersTable.tableName,
        GOVERNANCE_LEDGER_TABLE: props.governanceLedgerTable.tableName,
        ESCALATION_TOPIC_ARN: this.escalationTopic.topicArn,
        GOVERNANCE_BYPASS: 'true',  // Bootstrap: bypass until authority units are seeded
        // Fabric Memory env vars
        WORKFLOW_OUTCOMES_TABLE: props.workflowOutcomesTable.tableName,
        AGENT_METRICS_TABLE: props.agentMetricsTable.tableName,
      },
      initialPolicy: [
        new PolicyStatement({
          effect: Effect.ALLOW,
          actions: ['bedrock:InvokeModel'],
          resources: ['*'],
        }),
        new PolicyStatement({
          effect: Effect.ALLOW,
          actions: ['sqs:SendMessage', 'sqs:ReceiveMessage', 'sqs:DeleteMessage'],
          resources: ['*'],
        }),
      ],
    });

    props.workflowTable.grantReadWriteData(supervisorLambda);
    props.agentEventBus.grantPutEventsTo(supervisorLambda);
    props.workerStateTable.grantReadWriteData(supervisorLambda);
    props.agentRegisterTable.grantReadData(supervisorLambda);

    // Governance permissions
    props.authorityUnitsTable.grantReadData(supervisorLambda);
    props.compositionContractsTable.grantReadData(supervisorLambda);
    props.caseLawTable.grantReadData(supervisorLambda);
    props.constitutionalLayersTable.grantReadData(supervisorLambda);
    props.governanceLedgerTable.grantWriteData(supervisorLambda);
    this.escalationTopic.grantPublish(supervisorLambda);

    // Fabric Memory permissions
    props.workflowOutcomesTable.grantWriteData(supervisorLambda);
    props.agentMetricsTable.grantReadWriteData(supervisorLambda);

    // Arbiter event triggers
    const agentTaskRule = new events.Rule(this, 'TaskRequestRule', {
      eventBus: props.agentEventBus,
      eventPattern: {
        source: ['task.request', 'task.completion'],
      },
    });
    agentTaskRule.addTarget(new targets.LambdaFunction(supervisorLambda));

    // Re-trigger Arbiter when a new agent has been fabricated
    const fabricationCompleteRule = new events.Rule(this, 'FabricationCompleteRule', {
      eventBus: props.agentEventBus,
      eventPattern: {
        source: ['agent.fabricated'],
      },
    });
    fabricationCompleteRule.addTarget(new targets.LambdaFunction(supervisorLambda));

    // Agent Activator — handles activate/suspend events
    const activatorLambda = new lambda.Function(this, 'AgentActivator', {
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../src/activator')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        AGENT_CONFIG_TABLE: props.agentRegisterTable.tableName,
      },
    });

    props.agentRegisterTable.grantReadWriteData(activatorLambda);

    const agentActivateRule = new events.Rule(this, 'AgentActivateRule', {
      eventBus: props.agentEventBus,
      eventPattern: {
        source: ['agent.activate'],
      },
    });
    agentActivateRule.addTarget(new targets.LambdaFunction(activatorLambda));
  }
}
