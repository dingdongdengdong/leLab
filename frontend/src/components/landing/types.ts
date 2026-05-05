import { ReactNode } from "react";

interface ActionBase {
  title: string;
  description: string;
  isWorkInProgress?: boolean;
}

interface NavAction extends ActionBase {
  handler: () => void;
  color: string;
  trigger?: never;
}

interface TriggerAction extends ActionBase {
  trigger: ReactNode;
  handler?: never;
  color?: never;
}

export type Action = NavAction | TriggerAction;
