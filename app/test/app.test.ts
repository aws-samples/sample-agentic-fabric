import * as cdk from 'aws-cdk-lib/core';
import 'source-map-support/register';
import { AwsSolutionsChecks } from 'cdk-nag'
import { Aspects } from 'aws-cdk-lib';
import { FabricStack } from '../lib/fabricStack';
import { ArbiterStack } from '../lib/arbiterStack';
import { WorkerStack } from '../lib/workerStack';
import { FabricatorStack } from '../lib/fabricatorStack';

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION || 'ap-southeast-2',
};

const stackProps = {
  env,
  appName: 'test-fabric',
  environment: 'test',
};


const app = new cdk.App();
// Add the cdk-nag AwsSolutions Pack with extra verbose logging enabled.
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }))
const fabricStack = new FabricStack(app, 'TestFabricStack', stackProps);

const workerStack = new WorkerStack(app, 'TestWorkerStack', {
  ...stackProps,
  agentEventBus: fabricStack.agentEventBus,
  agentRegisterTable: fabricStack.agentRegisterTable,
  governanceLedgerTable: fabricStack.governanceLedgerTable,
  agentMetricsTable: fabricStack.agentMetricsTable,
});

const fabricatorStack = new FabricatorStack(app, 'TestFabricatorStack', {
  ...stackProps,
  agentEventBus: fabricStack.agentEventBus,
  agentRegisterTable: fabricStack.agentRegisterTable,
  workerAgentQueue: workerStack.workerAgentQueue,
  workerCodeBucket: workerStack.workerCodeBucket,
  authorityUnitsTable: fabricStack.authorityUnitsTable,
  constitutionalLayersTable: fabricStack.constitutionalLayersTable,
});

const arbiterStack = new ArbiterStack(app, 'TestArbiterStack', {
  ...stackProps,
  agentEventBus: fabricStack.agentEventBus,
  agentRegisterTable: fabricStack.agentRegisterTable,
  workflowTable: fabricStack.workflowTable,
  workerStateTable: fabricStack.workerStateTable,
  fabricatorQueue: fabricatorStack.fabricatorQueue,
  // Governance tables
  authorityUnitsTable: fabricStack.authorityUnitsTable,
  compositionContractsTable: fabricStack.compositionContractsTable,
  caseLawTable: fabricStack.caseLawTable,
  constitutionalLayersTable: fabricStack.constitutionalLayersTable,
  governanceLedgerTable: fabricStack.governanceLedgerTable,
  // Fabric Memory tables
  workflowOutcomesTable: fabricStack.workflowOutcomesTable,
  agentMetricsTable: fabricStack.agentMetricsTable,
});


