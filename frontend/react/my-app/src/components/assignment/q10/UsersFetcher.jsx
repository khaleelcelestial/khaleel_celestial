import { useState, useEffect } from "react";

function UsersFetcher() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [trigger, setTrigger] = useState(0);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch("https://jsonplaceholder.typicode.com/users")
      .then((res) => {
        if (!res.ok) throw new Error("Failed to fetch");
        return res.json();
      })
      .then((data) => {
        setUsers(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [trigger]);

  return (
    <div style={{ maxWidth: 480, margin: "0 auto", fontFamily: "sans-serif" }}>
      <h2>Users</h2>
      <button
        onClick={() => setTrigger((t) => t + 1)}
        style={{
          padding: "8px 20px",
          background: "#6c63ff",
          color: "#fff",
          border: "none",
          borderRadius: "6px",
          cursor: "pointer",
          marginBottom: "16px",
        }}
      >
        Refresh
      </button>

      {loading && <p>Loading...</p>}
      {error && <p style={{ color: "red" }}>Error: {error}</p>}
      {!loading && !error && (
        <ul style={{ listStyle: "none", padding: 0 }}>
          {users.map((u) => (
            <li
              key={u.id}
              style={{
                padding: "10px 14px",
                borderBottom: "1px solid #eee",
                fontSize: "0.95rem",
              }}
            >
              <strong>{u.name}</strong> — {u.email}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default UsersFetcher;
