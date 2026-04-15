import * as cdk from 'aws-cdk-lib/core';
import * as events from "aws-cdk-lib/aws-events";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { BlockPublicAccess, Bucket } from 'aws-cdk-lib/aws-s3';
import { Queue } from 'aws-cdk-lib/aws-sqs';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import { PythonFunction } from '@aws-cdk/aws-lambda-python-alpha';
import { PolicyStatement, Effect } from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { FabricProps } from '../props';
import path = require('path');

interface WorkerStackProps extends FabricProps {
  agentEventBus: events.EventBus;
  agentRegisterTable: dynamodb.Table;
  governanceLedgerTable: dynamodb.Table;
  agentMetricsTable: dynamodb.Table;
}

export class WorkerStack extends cdk.Stack {
    public readonly workerAgentQueue: Queue;
    public readonly fabricatorQueue: Queue;
    public readonly toolsConfigTable: dynamodb.Table;
    public readonly workerCodeBucket: Bucket;

  constructor(scope: Construct, id: string, props: WorkerStackProps) {
    super(scope, id, props);

    // Dead letter queue for failed worker messages
    const workerAgentDLQ = new Queue(this, `workerAgentDLQ`, {
      queueName: `${props.appName}-worker-agent-dlq-${props.environment}`,
      retentionPeriod: cdk.Duration.days(14),
    });

    this.workerAgentQueue = new Queue(this, `workerAgentQueue`, {
      queueName: `${props.appName}-worker-agent-queue-${props.environment}`,
      visibilityTimeout: cdk.Duration.minutes(15),
      retentionPeriod: cdk.Duration.days(7),
      deadLetterQueue: {
        queue: workerAgentDLQ,
        maxReceiveCount: 3, // Retry 3 times before sending to DLQ
      },
    });

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
      description: 'Governance engine for Worker Wrapper Lambda',
    });

    this.workerCodeBucket = new Bucket(this, 'CodeBucket', {
      bucketName: `${props.appName}-code-${props.environment}-${this.account}-${this.region}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
      versioned: true, // Enable versioning for code files
    });

    const workerAgentWrapperLambda = new PythonFunction(this, 'WorkerAgentWrapper', {
      runtime: lambda.Runtime.PYTHON_3_13,
      entry: path.join(__dirname, '../../src/workerWrapper'),
      handler: 'lambda_handler',
      timeout: cdk.Duration.minutes(15),
      memorySize: 1024,
      layers: [governanceLayer],
      environment: {
        COMPLETION_BUS_NAME: props.agentEventBus.eventBusName,
        AGENT_CONFIG_TABLE: props.agentRegisterTable.tableName,
        AGENT_BUCKET_NAME: this.workerCodeBucket.bucketName,
        GOVERNANCE_LEDGER_TABLE: props.governanceLedgerTable.tableName,
        AGENT_METRICS_TABLE: props.agentMetricsTable.tableName,
      },
      initialPolicy: [
        new PolicyStatement({
          effect: Effect.ALLOW,
          actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
          resources: ['*'],
        }),
      ],
    });

    props.agentEventBus.grantPutEventsTo(workerAgentWrapperLambda);
    props.agentRegisterTable.grantReadData(workerAgentWrapperLambda);
    this.workerCodeBucket.grantRead(workerAgentWrapperLambda);
    props.governanceLedgerTable.grantWriteData(workerAgentWrapperLambda);
    props.agentMetricsTable.grantWriteData(workerAgentWrapperLambda);

    workerAgentWrapperLambda.addEventSource(new SqsEventSource(this.workerAgentQueue, {
      batchSize: 1, // Process one message at a time
      reportBatchItemFailures: true, // Enable partial batch responses
    }));


  }
}


