#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { FabricStack } from '../lib/fabricStack';
import { ArbiterStack } from '../lib/arbiterStack';
import { WorkerStack } from '../lib/workerStack';
import { FabricatorStack } from '../lib/fabricatorStack';


const app = new cdk.App();

const appName = "agentic-fabric";

const environment = process.env.ENVIRONMENT || 'dev';
if (!environment) {
  throw new Error('ENVIRONMENT variable must be set (dev, staging, or prod)');
}

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION || 'ap-southeast-2',
};

const stackProps = {
  env,
  appName,
  environment: environment,
};

const fabricStack = new FabricStack(app, 'FabricStack', {
  ...stackProps
})

const workerStack = new WorkerStack(app, 'WorkerStack', {
  ...stackProps,
  agentEventBus: fabricStack.agentEventBus,
  agentRegisterTable: fabricStack.agentRegisterTable,
  governanceLedgerTable: fabricStack.governanceLedgerTable,
  agentMetricsTable: fabricStack.agentMetricsTable,
});

const fabricatorStack = new FabricatorStack(app, 'FabricatorStack', {
  ...stackProps,
  agentEventBus: fabricStack.agentEventBus,
  agentRegisterTable: fabricStack.agentRegisterTable,
  workerAgentQueue: workerStack.workerAgentQueue,
  workerCodeBucket: workerStack.workerCodeBucket,
  authorityUnitsTable: fabricStack.authorityUnitsTable,
  constitutionalLayersTable: fabricStack.constitutionalLayersTable,
});

const arbiterStack = new ArbiterStack(app, 'ArbiterStack', {
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
