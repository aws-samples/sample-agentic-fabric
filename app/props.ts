import { StackProps } from "aws-cdk-lib";

export interface FabricProps extends StackProps {
  appName: string;
  environment: string;
}