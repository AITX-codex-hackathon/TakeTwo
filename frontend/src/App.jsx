import React, { useState } from "react";
import { Film } from "lucide-react";
import Upload from "./pages/Upload";
import Review from "./pages/Review";
import Done from "./pages/Done";

export default function App() {
  const [jobId, setJobId] = useState(null);
  const [page, setPage] = useState("upload");
  const [outputPath, setOutputPath] = useState(null);

  function handleUploaded(id) {
    setJobId(id);
    setPage("review");
  }

  function handleDone(path) {
    setOutputPath(path);
    setPage("done");
  }

  function handleReset() {
    setJobId(null);
    setOutputPath(null);
    setPage("upload");
  }

  return (
    <div className="app-shell">
      <div className="top-bar">
        <Film size={24} style={{ color: "#818cf8" }} />
        <h1>ClipCure</h1>
      </div>
      <div className="main-content">
        {page === "upload" && <Upload onUploaded={handleUploaded} />}
        {page === "review" && <Review jobId={jobId} onDone={handleDone} />}
        {page === "done" && (
          <Done jobId={jobId} outputPath={outputPath} onReset={handleReset} />
        )}
      </div>
    </div>
  );
}
