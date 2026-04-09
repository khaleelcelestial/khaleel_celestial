import { memo } from "react";

const TodoItem = memo(function TodoItem({ todo, onDelete }) {
  console.log(`🔁 TodoItem rendered: ${todo.text}`);
  return (
    <li
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "10px 14px",
        borderBottom: "1px solid #eee",
        fontSize: "0.95rem",
      }}
    >
      <span>{todo.text}</span>
      <button
        onClick={() => onDelete(todo.id)}
        style={{
          background: "#e74c3c",
          color: "#fff",
          border: "none",
          borderRadius: "6px",
          padding: "4px 12px",
          cursor: "pointer",
        }}
      >
        Delete
      </button>
    </li>
  );
});

export default TodoItem;
