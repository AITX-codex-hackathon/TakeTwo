import React, { useRef, useState } from "react";
import { Upload as UploadIcon, Loader } from "lucide-react";
import { uploadVideo } from "../api";

export default function Upload({ onUploaded }) {
  const inputRef = useRef();
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);

  async function handleFile(file) {
    if (!file) return;
    setUploading(true);
    try {
      const data = await uploadVideo(file);
      onUploaded(data.job_id);
    } catch (e) {
      alert("Upload failed: " + e.message);
      setUploading(false);
    }
  }

  function onDrop(e) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    handleFile(file);
  }

  if (uploading) {
    return (
      <div className="upload-zone">
        <Loader size={48} style={{ color: "#818cf8", animation: "spin 1s linear infinite" }} />
        <h2 style={{ marginTop: 16 }}>Uploading video...</h2>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  return (
    <div
      className={`upload-zone ${dragging ? "dragging" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      onClick={() => inputRef.current?.click()}
    >
      <UploadIcon size={48} style={{ color: "#4a4a6a", marginBottom: 12 }} />
      <h2>Drop your video here</h2>
      <p>or click to browse. MP4, MOV, AVI supported.</p>
      <input
        ref={inputRef}
        type="file"
        accept="video/*"
        onChange={(e) => handleFile(e.target.files[0])}
      />
    </div>
  );
}
