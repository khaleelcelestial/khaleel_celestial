import { memo } from "react";

const IncrementButton = memo(function IncrementButton({ onIncrement }) {
  console.log("🔁 IncrementButton rendered");
  return (
    <button
      onClick={onIncrement}
      style={{
        padding: "10px 28px",
        background: "#6c63ff",
        color: "#fff",
        border: "none",
        borderRadius: "8px",
        cursor: "pointer",
        fontSize: "1rem",
        fontWeight: "600",
      }}
    >
      Increment
    </button>
  );
});

export default IncrementButton;
