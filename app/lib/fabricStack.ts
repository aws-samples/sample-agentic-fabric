import * as cdk from 'aws-cdk-lib/core';
import * as events from "aws-cdk-lib/aws-events";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb"
import { Construct } from 'constructs';
import { FabricProps } from '../props';


export class FabricStack extends cdk.Stack {
   public readonly agentEventBus: events.EventBus;
   public readonly agentRegisterTable: dynamodb.Table;
   public readonly workflowTable: dynamodb.Table;
   public readonly workerStateTable: dynamodb.Table;

   // Governance tables
   public readonly authorityUnitsTable: dynamodb.Table;
   public readonly compositionContractsTable: dynamodb.Table;
   public readonly caseLawTable: dynamodb.Table;
   public readonly constitutionalLayersTable: dynamodb.Table;
   public readonly governanceLedgerTable: dynamodb.Table;

   // Fabric Memory tables
   public readonly workflowOutcomesTable: dynamodb.Table;
   public readonly agentMetricsTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props: FabricProps) {
    super(scope, id, props);

    // EventBridge for agent coordination
    this.agentEventBus = new events.EventBus(this, "AgentEventBus", {
      eventBusName: `${props.appName}-${props.environment}`,
    });

    this.agentRegisterTable = new dynamodb.Table(this, 'AgentRegisterTable', {
      tableName: `${props.appName}-agent-register-${props.environment}`,
      partitionKey: { name: 'agentId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      pointInTimeRecovery: true,
    });

    this.agentRegisterTable.addGlobalSecondaryIndex({
      indexName: 'state-index',
      partitionKey: { name: 'state', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    this.workflowTable = new dynamodb.Table(this, 'WorkflowTable', {
      tableName: `${props.appName}-workflow-${props.environment}`,
      partitionKey: { name: 'workflowId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.workerStateTable = new dynamodb.Table(this, 'WorkerStateTable', {
      tableName: `${props.appName}-worker-state-${props.environment}`,
      partitionKey: { name: 'requestId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // --- Governance Tables ---

    this.authorityUnitsTable = new dynamodb.Table(this, 'AuthorityUnitsTable', {
      tableName: `${props.appName}-authority-units-${props.environment}`,
      partitionKey: { name: 'unitId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
    });

    this.compositionContractsTable = new dynamodb.Table(this, 'CompositionContractsTable', {
      tableName: `${props.appName}-composition-contracts-${props.environment}`,
      partitionKey: { name: 'contractId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
    });

    this.caseLawTable = new dynamodb.Table(this, 'CaseLawTable', {
      tableName: `${props.appName}-case-law-${props.environment}`,
      partitionKey: { name: 'caseId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
    });

    this.constitutionalLayersTable = new dynamodb.Table(this, 'ConstitutionalLayersTable', {
      tableName: `${props.appName}-constitutional-layers-${props.environment}`,
      partitionKey: { name: 'layerId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
    });

    this.governanceLedgerTable = new dynamodb.Table(this, 'GovernanceLedgerTable', {
      tableName: `${props.appName}-governance-ledger-${props.environment}`,
      partitionKey: { name: 'findingId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      timeToLiveAttribute: 'ttl',
    });

    this.governanceLedgerTable.addGlobalSecondaryIndex({
      indexName: 'workflow-index',
      partitionKey: { name: 'workflowId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'timestamp', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // --- Fabric Memory Tables ---

    this.workflowOutcomesTable = new dynamodb.Table(this, 'WorkflowOutcomesTable', {
      tableName: `${props.appName}-workflow-outcomes-${props.environment}`,
      partitionKey: { name: 'workflowId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      timeToLiveAttribute: 'ttl',
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
    });

    this.workflowOutcomesTable.addGlobalSecondaryIndex({
      indexName: 'status-completedAt-index',
      partitionKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'completedAt', type: dynamodb.AttributeType.NUMBER },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    this.agentMetricsTable = new dynamodb.Table(this, 'AgentMetricsTable', {
      tableName: `${props.appName}-agent-metrics-${props.environment}`,
      partitionKey: { name: 'agentId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
    });

  }
}
