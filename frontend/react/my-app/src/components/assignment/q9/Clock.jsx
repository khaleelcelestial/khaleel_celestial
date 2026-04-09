import { useState, useEffect, useRef } from "react";

function Clock() {
  const [time, setTime] = useState(new Date());
  const [paused, setPaused] = useState(false);
  const intervalRef = useRef(null);

  useEffect(() => {
    if (!paused) {
      intervalRef.current = setInterval(() => {
        setTime(new Date());
      }, 1000);
    }
    return () => clearInterval(intervalRef.current);
  }, [paused]);

  const pad = (n) => String(n).padStart(2, "0");
  const formatted = `${pad(time.getHours())}:${pad(time.getMinutes())}:${pad(time.getSeconds())}`;

  return (
    <div style={{ textAlign: "center", fontFamily: "monospace" }}>
      <h1 style={{ fontSize: "3rem", letterSpacing: "0.1em", color: "#1a1a2e" }}>
        {formatted}
      </h1>
      <button
        onClick={() => setPaused((p) => !p)}
        style={{
          padding: "10px 28px",
          background: paused ? "#2ecc71" : "#e74c3c",
          color: "#fff",
          border: "none",
          borderRadius: "8px",
          cursor: "pointer",
          fontSize: "1rem",
          fontWeight: "600",
        }}
      >
        {paused ? "Resume" : "Pause"}
      </button>
    </div>
  );
}

export default Clock;
