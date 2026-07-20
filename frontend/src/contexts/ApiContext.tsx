import React, { createContext, useContext, ReactNode, useState, useCallback, useMemo } from "react";
import { resolveInitialApiBaseUrl } from "@/lib/apiBaseUrl";

interface ApiContextType {
  baseUrl: string;
  wsBaseUrl: string;
  fetchWithHeaders: (url: string, options?: RequestInit) => Promise<Response>;
}

const ApiContext = createContext<ApiContextType | undefined>(undefined);

const httpToWs = (url: string): string => url.replace(/^http(s?):/, "ws$1:");

export const ApiProvider: React.FC<{ children: ReactNode }> = ({
  children,
}) => {
  const [baseUrl] = useState<string>(resolveInitialApiBaseUrl);
  const wsBaseUrl = httpToWs(baseUrl);

  const fetchWithHeaders = useCallback(async (url: string, options: RequestInit = {}): Promise<Response> => {
    return fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
    });
  }, []);

  const value = useMemo(
    () => ({ baseUrl, wsBaseUrl, fetchWithHeaders }),
    [baseUrl, wsBaseUrl, fetchWithHeaders]
  );

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
};

export const useApi = (): ApiContextType => {
  const context = useContext(ApiContext);
  if (context === undefined) {
    throw new Error("useApi must be used within an ApiProvider");
  }
  return context;
};
