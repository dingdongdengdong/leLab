import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import ReplayHeader from "@/components/replay/ReplayHeader";
import DatasetCombobox from "@/components/replay/DatasetCombobox";
import { useApi } from "@/contexts/ApiContext";
import { DatasetItem, listDatasets } from "@/lib/replayApi";

const SPACE_BASE_URL = "https://lerobot-visualize-dataset.hf.space";

const ReplayDataset: React.FC = () => {
  const { baseUrl, fetchWithHeaders } = useApi();
  const navigate = useNavigate();

  const [datasets, setDatasets] = useState<DatasetItem[]>([]);
  const [datasetsLoading, setDatasetsLoading] = useState(true);

  useEffect(() => {
    setDatasetsLoading(true);
    listDatasets(baseUrl, fetchWithHeaders)
      .then(setDatasets)
      .catch(() => setDatasets([]))
      .finally(() => setDatasetsLoading(false));
  }, [baseUrl, fetchWithHeaders]);

  const handleDatasetChange = (repoId: string | null) => {
    if (!repoId) return;
    window.open(`${SPACE_BASE_URL}/${repoId}`, "_blank", "noopener,noreferrer");
    navigate("/");
  };

  return (
    <div className="h-screen bg-black text-white flex flex-col p-4 gap-6">
      <ReplayHeader />

      <div className="flex-1 flex flex-col items-center justify-center gap-4 px-4">
        <p className="text-gray-400 text-sm text-center max-w-md">
          Pick a dataset to open it in the LeRobot dataset viewer in a new tab.
        </p>
        <div className="w-full max-w-xl">
          <DatasetCombobox
            datasets={datasets}
            loading={datasetsLoading}
            value={null}
            onChange={handleDatasetChange}
          />
        </div>
      </div>
    </div>
  );
};

export default ReplayDataset;
