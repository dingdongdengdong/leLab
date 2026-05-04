export interface DatasetItem {
  repo_id: string;
  last_modified: string | null;
  private: boolean;
}

type Fetcher = (url: string, options?: RequestInit) => Promise<Response>;

export async function listDatasets(
  baseUrl: string,
  fetcher: Fetcher,
): Promise<DatasetItem[]> {
  const r = await fetcher(`${baseUrl}/datasets`);
  if (!r.ok) throw new Error(`GET /datasets failed: ${r.status}`);
  return r.json();
}
