import React, { useState } from "react";
import { Film } from "lucide-react";
import Upload from "./pages/Upload";
import Review from "./pages/Review";
import Done from "./pages/Done";
import Login from "./pages/Login";

export default function App() {
  const [jobId, setJobId] = useState(null);
  const [page, setPage] = useState("login");
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

  function handleHomeClick() {
    if (page !== "login") handleReset();
  }

  function handleLogin() {
    setPage("upload");
  }

  return (
    <div className={`app-shell ${page === "login" ? "auth-shell" : "editor-shell"}`}>
      {page === "login" ? (
        <div className="top-bar">
          <Film size={24} style={{ color: "#818cf8" }} />
          <h1>TakeTwo</h1>
        </div>
      ) : (
        <header className="editor-topnav">
          <div className="looply-brand" onClick={handleHomeClick} style={{ cursor: "pointer" }}>
            <span className="brand-mark"><Film size={19} /></span>
            <strong>TakeTwo</strong>
          </div>
        </header>
      )}
      <div className={page === "login" ? "main-content" : "editor-content"}>
        {page === "login" && <Login onLogin={handleLogin} />}
        {page === "upload" && <Upload onUploaded={handleUploaded} />}
        {page === "review" && <Review jobId={jobId} onDone={handleDone} onReset={handleReset} />}
        {page === "done" && (
          <Done jobId={jobId} outputPath={outputPath} onReset={handleReset} />
        )}
      </div>
    </div>
  );
}
