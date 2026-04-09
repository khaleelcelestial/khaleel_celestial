function Button({ label, color, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        backgroundColor: color,
        color: "#fff",
        border: "none",
        padding: "10px 24px",
        borderRadius: "8px",
        cursor: "pointer",
        fontSize: "0.95rem",
        fontWeight: "600",
        transition: "opacity 0.2s",
      }}
      onMouseOver={(e) => (e.target.style.opacity = "0.85")}
      onMouseOut={(e) => (e.target.style.opacity = "1")}
    >
      {label}
    </button>
  );
}

export default Button;
