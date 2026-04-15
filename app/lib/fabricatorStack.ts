import * as cdk from 'aws-cdk-lib/core';
import * as events from "aws-cdk-lib/aws-events";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Bucket } from 'aws-cdk-lib/aws-s3';
import { Queue } from 'aws-cdk-lib/aws-sqs';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import { PythonFunction } from '@aws-cdk/aws-lambda-python-alpha';
import { PolicyStatement, Effect } from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { FabricProps } from '../props';
import path = require('path');

interface FabricatorStackProps extends FabricProps {
  agentEventBus: events.EventBus;
  agentRegisterTable: dynamodb.Table;
  workerAgentQueue: Queue;
  workerCodeBucket: Bucket;
  authorityUnitsTable: dynamodb.Table;
  constitutionalLayersTable: dynamodb.Table;
}

export class FabricatorStack extends cdk.Stack {
    public readonly fabricatorQueue: Queue;
    public readonly toolsConfigTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props: FabricatorStackProps) {
    super(scope, id, props);

    this.fabricatorQueue = new Queue(this, `fabricatorQueue`, {
      queueName: `${props.appName}-fabricator-queue-${props.environment}`,
      visibilityTimeout: cdk.Duration.minutes(15),
      retentionPeriod: cdk.Duration.days(7),
    });

    //Fabricator
    this.toolsConfigTable = new dynamodb.Table(this, 'ToolsConfigTable', {
      tableName: `${props.appName}-tools-${props.environment}`,
      partitionKey: { name: 'toolId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const fabricatorLambda = new PythonFunction(this, 'FabricatorAgent', {
      runtime: lambda.Runtime.PYTHON_3_13,
      entry: path.join(__dirname, '../../src/fabricator'),
      handler: 'lambda_handler',
      timeout: cdk.Duration.minutes(15),
      memorySize: 1024,
      environment: {
        COMPLETION_BUS_NAME: props.agentEventBus.eventBusName,
        AGENT_CONFIG_TABLE: props.agentRegisterTable.tableName,
        TOOL_CONFIG_TABLE: this.toolsConfigTable.tableName,
        AGENT_BUCKET_NAME: props.workerCodeBucket.bucketName,
        WORKER_QUEUE_URL: props.workerAgentQueue.queueUrl,
      },
      initialPolicy: [
        new PolicyStatement({
          effect: Effect.ALLOW,
          actions: ['bedrock:*'],
          resources: ['*'],
        }),
      ],
    });

    props.agentEventBus.grantPutEventsTo(fabricatorLambda);
    props.agentRegisterTable.grantReadWriteData(fabricatorLambda);
    props.workerCodeBucket.grantReadWrite(fabricatorLambda);
    this.toolsConfigTable.grantReadWriteData(fabricatorLambda);

    fabricatorLambda.addEventSource(new SqsEventSource(this.fabricatorQueue));

    // Seed initial agent configuration
    const seedAgentConfigLambda = new lambda.Function(this, 'SeedAgentConfigFunction', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../src/seedConfig')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        AGENT_CONFIG_TABLE: props.agentRegisterTable.tableName,
        WORKER_QUEUE_URL: props.workerAgentQueue.queueUrl,
        FABRICATOR_QUEUE_URL: this.fabricatorQueue.queueUrl,
        AUTHORITY_UNITS_TABLE: props.authorityUnitsTable.tableName,
        CONSTITUTIONAL_LAYERS_TABLE: props.constitutionalLayersTable.tableName,
      },
    });

    props.agentRegisterTable.grantWriteData(seedAgentConfigLambda);
    props.authorityUnitsTable.grantWriteData(seedAgentConfigLambda);
    props.constitutionalLayersTable.grantWriteData(seedAgentConfigLambda);

    // Invoke the Custom Resource to seed agent config table
    // This must come after fabricatorQueue is created since we pass its URL
    const seedAgentConfigResource = new cdk.CustomResource(this, 'SeedAgentConfigResource', {
      serviceToken: seedAgentConfigLambda.functionArn,
      properties: {
        // Trigger update when these values change
        Timestamp: Date.now().toString(),
      },
    });

    // Ensure the Custom Resource runs after the table and queue are created
    seedAgentConfigResource.node.addDependency(props.agentRegisterTable);
    seedAgentConfigResource.node.addDependency(this.fabricatorQueue);
    seedAgentConfigResource.node.addDependency(props.authorityUnitsTable);
    seedAgentConfigResource.node.addDependency(props.constitutionalLayersTable);



  }
}


