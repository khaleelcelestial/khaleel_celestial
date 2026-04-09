import { useState, useMemo } from "react";

// Generate 1000 products
const CATEGORIES = ["Electronics", "Clothing", "Books", "Food", "Sports"];
const ALL_PRODUCTS = Array.from({ length: 1000 }, (_, i) => ({
  id: i + 1,
  name: `Product ${i + 1}`,
  price: Math.floor(Math.random() * 9900) + 100,
  category: CATEGORIES[i % CATEGORIES.length],
}));

function ExpensiveFilter() {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("All");
  const [counter, setCounter] = useState(0);

  const filtered = useMemo(() => {
    console.log("🔍 Filtering products...");
    return ALL_PRODUCTS.filter((p) => {
      const matchSearch = p.name.toLowerCase().includes(search.toLowerCase());
      const matchCat = category === "All" || p.category === category;
      return matchSearch && matchCat;
    });
  }, [search, category]);

  return (
    <div style={{ fontFamily: "sans-serif", padding: "16px" }}>
      <h2>Product Filter (useMemo)</h2>

      <div style={{ display: "flex", gap: "12px", marginBottom: "12px", flexWrap: "wrap" }}>
        <input
          placeholder="Search products..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ padding: "8px 12px", borderRadius: "6px", border: "1px solid #ccc", flex: 1 }}
        />
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          style={{ padding: "8px 12px", borderRadius: "6px", border: "1px solid #ccc" }}
        >
          <option value="All">All Categories</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      </div>

      <div style={{ marginBottom: "12px" }}>
        <button
          onClick={() => setCounter((c) => c + 1)}
          style={{
            padding: "8px 18px",
            background: "#e74c3c",
            color: "#fff",
            border: "none",
            borderRadius: "6px",
            cursor: "pointer",
          }}
        >
          Unrelated Counter: {counter} (won't re-filter)
        </button>
      </div>

      <p style={{ color: "#555" }}>
        Showing <strong>{filtered.length}</strong> of {ALL_PRODUCTS.length} products
      </p>

      <ul style={{ listStyle: "none", padding: 0, maxHeight: "320px", overflowY: "auto" }}>
        {filtered.slice(0, 50).map((p) => (
          <li
            key={p.id}
            style={{
              padding: "8px 12px",
              borderBottom: "1px solid #eee",
              display: "flex",
              justifyContent: "space-between",
            }}
          >
            <span>{p.name} <small style={{ color: "#888" }}>({p.category})</small></span>
            <strong>₹{p.price}</strong>
          </li>
        ))}
        {filtered.length > 50 && (
          <li style={{ padding: "8px 12px", color: "#888", fontStyle: "italic" }}>
            ... and {filtered.length - 50} more
          </li>
        )}
      </ul>
    </div>
  );
}

export default ExpensiveFilter;
