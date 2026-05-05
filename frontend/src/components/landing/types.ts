import { ReactNode } from "react";

export interface Action {
  title: string;
  description: string;
  handler: () => void;
  color: string;
  isWorkInProgress?: boolean;
  trigger?: ReactNode;
}
