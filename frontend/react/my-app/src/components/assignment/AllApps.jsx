// ============================================================
//  AllApps.jsx — All 15 Assignment App() functions
//
//  HOW TO USE — two options:
//
//  OPTION A (recommended): Import directly from this file in App.jsx
//    import { AppQ1 } from "./components/assisgment/AllApps";
//    export default function App() { return <AppQ1 />; }
//    → Just change AppQ1 → AppQ2 ... AppQ15 to switch questions.
//
//  OPTION B: Copy-paste one AppQ* function body into App.jsx
//    then also copy the relevant component files from q*/
// ============================================================

import { useState, useEffect, useMemo, useCallback, memo } from "react";
import ProfileCard from "./q1/ProfileCard";
import Button from "./q2/Button";
import ProductCard from "./q3/ProductCard";
import Clock from "./q9/Clock";
import UsersFetcher from "./q10/UsersFetcher";
import ExpensiveFilter from "./q12/ExpensiveFilter";
import PrimeCalculator from "./q13/PrimeCalculator";
import TodoItem from "./q14/TodoItem";
import IncrementButton from "./q15/IncrementButton";

// ─────────────────────────────────────────────────────────────
// Q1 — Personal Profile Card
// ─────────────────────────────────────────────────────────────
export function AppQ1() {
  const profiles = [
    {
      name: "Arjun Sharma",
      title: "Frontend Developer",
      bio: "Loves building UI with React and experimenting with animations.",
      avatarUrl: "https://i.pravatar.cc/150?img=11",
    },
    {
      name: "Priya Iyer",
      title: "UI/UX Designer",
      bio: "Passionate about accessible, beautiful, and intuitive design.",
      avatarUrl: "https://i.pravatar.cc/150?img=47",
    },
    {
      name: "Karan Mehta",
      title: "Backend Engineer",
      bio: "Node.js enthusiast, database whisperer, and coffee addict.",
      avatarUrl: "https://i.pravatar.cc/150?img=53",
    },
  ];

  return (
    <div style={{ display: "flex", gap: "24px", padding: "40px", flexWrap: "wrap", justifyContent: "center", background: "#f0f2f5", minHeight: "100vh" }}>
      <h1 style={{ width: "100%", textAlign: "center", color: "#1a1a2e" }}>Q1 — Profile Cards</h1>
      {profiles.map((p) => (
        <ProfileCard key={p.name} {...p} />
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Q2 — Reusable Button Component
// ─────────────────────────────────────────────────────────────
export function AppQ2() {
  return (
    <div style={{ display: "flex", gap: "16px", padding: "60px", justifyContent: "center", alignItems: "center", minHeight: "100vh", background: "#f0f2f5" }}>
      <h1 style={{ position: "absolute", top: 30, left: 0, right: 0, textAlign: "center", color: "#1a1a2e" }}>Q2 — Reusable Buttons</h1>
      <Button label="Save" color="#2ecc71" onClick={() => alert("Saved!")} />
      <Button label="Cancel" color="#f39c12" onClick={() => alert("Cancelled!")} />
      <Button label="Delete" color="#e74c3c" onClick={() => alert("Deleted!")} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Q3 — Product Card Grid
// ─────────────────────────────────────────────────────────────
export function AppQ3() {
  const products = [
    { id: 1, image: "https://picsum.photos/seed/p1/300/200", name: "Wireless Headphones", price: 2499, description: "Premium sound with 30hr battery." },
    { id: 2, image: "https://picsum.photos/seed/p2/300/200", name: "Mechanical Keyboard", price: 3999, description: "Tactile switches for fast typing." },
    { id: 3, image: "https://picsum.photos/seed/p3/300/200", name: "USB-C Hub", price: 1299, description: "7-in-1 hub for all your ports." },
    { id: 4, image: "https://picsum.photos/seed/p4/300/200", name: "Laptop Stand", price: 899, description: "Ergonomic aluminium stand." },
    { id: 5, image: "https://picsum.photos/seed/p5/300/200", name: "Webcam 4K", price: 5499, description: "Crystal-clear video calls." },
    { id: 6, image: "https://picsum.photos/seed/p6/300/200", name: "Mouse Pad XL", price: 599, description: "Smooth surface, anti-slip base." },
  ];

  const handleAddToCart = (name) => console.log(`Added to cart: ${name}`);

  return (
    <div style={{ padding: "40px", background: "#f0f2f5", minHeight: "100vh" }}>
      <h1 style={{ textAlign: "center", color: "#1a1a2e", marginBottom: "32px" }}>Q3 — Product Grid</h1>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "20px", justifyContent: "center" }}>
        {products.map((p) => (
          <ProductCard key={p.id} {...p} onAddToCart={handleAddToCart} />
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Q4 — Counter App with Limits
// ─────────────────────────────────────────────────────────────
export function AppQ4() {
  const [count, setCount] = useState(0);
  const MIN = 0, MAX = 10;

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "100vh", gap: "20px", fontFamily: "sans-serif" }}>
      <h1>Q4 — Counter with Limits</h1>
      <div style={{ fontSize: "4rem", fontWeight: "700", color: "#6c63ff" }}>{count}</div>

      {count === MAX && <p style={{ color: "#e74c3c", fontWeight: "600" }}>Maximum limit reached!</p>}
      {count === MIN && <p style={{ color: "#e74c3c", fontWeight: "600" }}>Minimum limit reached!</p>}

      <div style={{ display: "flex", gap: "12px" }}>
        <button disabled={count <= MIN} onClick={() => setCount((c) => c - 1)} style={btnStyle("#e74c3c", count <= MIN)}>−</button>
        <button onClick={() => setCount(0)} style={btnStyle("#f39c12", false)}>Reset</button>
        <button disabled={count >= MAX} onClick={() => setCount((c) => c + 1)} style={btnStyle("#2ecc71", count >= MAX)}>+</button>
      </div>
    </div>
  );
}

function btnStyle(color, disabled) {
  return {
    padding: "12px 24px",
    fontSize: "1.2rem",
    background: disabled ? "#ccc" : color,
    color: "#fff",
    border: "none",
    borderRadius: "8px",
    cursor: disabled ? "not-allowed" : "pointer",
    fontWeight: "700",
  };
}

// ─────────────────────────────────────────────────────────────
// Q5 — Toggle Theme
// ─────────────────────────────────────────────────────────────
export function AppQ5() {
  const [isDark, setIsDark] = useState(false);

  return (
    <div style={{
      minHeight: "100vh",
      background: isDark ? "#1a1a2e" : "#f5f5f5",
      color: isDark ? "#eee" : "#1a1a2e",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      gap: "24px",
      transition: "all 0.4s",
      fontFamily: "sans-serif",
    }}>
      <h1>Q5 — Toggle Theme</h1>
      <p>Current mode: <strong>{isDark ? "Dark Mode 🌙" : "Light Mode ☀️"}</strong></p>
      <button
        onClick={() => setIsDark((d) => !d)}
        style={{
          padding: "12px 32px",
          background: isDark ? "#eee" : "#1a1a2e",
          color: isDark ? "#1a1a2e" : "#eee",
          border: "none",
          borderRadius: "8px",
          cursor: "pointer",
          fontSize: "1rem",
          fontWeight: "600",
        }}
      >
        Switch to {isDark ? "Light" : "Dark"} Mode
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Q6 — Login / Logout UI
// ─────────────────────────────────────────────────────────────
export function AppQ6() {
  const [loggedIn, setLoggedIn] = useState(false);

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "100vh", gap: "20px", fontFamily: "sans-serif" }}>
      <h1>Q6 — Login / Logout</h1>
      {loggedIn
        ? <p style={{ fontSize: "1.3rem", color: "#2ecc71", fontWeight: "600" }}>Welcome, User! 👋</p>
        : <p style={{ fontSize: "1.3rem", color: "#e74c3c" }}>Please log in to continue.</p>
      }
      <button
        onClick={() => setLoggedIn((l) => !l)}
        style={{
          padding: "12px 32px",
          background: loggedIn ? "#e74c3c" : "#6c63ff",
          color: "#fff",
          border: "none",
          borderRadius: "8px",
          cursor: "pointer",
          fontSize: "1rem",
          fontWeight: "600",
        }}
      >
        {loggedIn ? "Logout" : "Login"}
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Q7 — Simple To-Do List
// ─────────────────────────────────────────────────────────────
export function AppQ7() {
  const [input, setInput] = useState("");
  const [tasks, setTasks] = useState([]);

  const addTask = () => {
    const trimmed = input.trim();
    if (!trimmed) return;
    setTasks((t) => [...t, { id: Date.now(), text: trimmed }]);
    setInput("");
  };

  return (
    <div style={{ maxWidth: 480, margin: "60px auto", fontFamily: "sans-serif", padding: "0 16px" }}>
      <h1 style={{ color: "#1a1a2e" }}>Q7 — To-Do List</h1>
      <div style={{ display: "flex", gap: "10px", marginBottom: "24px" }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addTask()}
          placeholder="Enter a task..."
          style={{ flex: 1, padding: "10px 14px", borderRadius: "8px", border: "1px solid #ccc", fontSize: "0.95rem" }}
        />
        <button
          onClick={addTask}
          style={{ padding: "10px 20px", background: "#6c63ff", color: "#fff", border: "none", borderRadius: "8px", cursor: "pointer", fontWeight: "600" }}
        >
          Add
        </button>
      </div>

      {tasks.length === 0
        ? <p style={{ color: "#aaa", textAlign: "center" }}>No tasks yet</p>
        : <ul style={{ listStyle: "none", padding: 0 }}>
            {tasks.map((t) => (
              <li key={t.id} style={{ padding: "10px 14px", borderBottom: "1px solid #eee", fontSize: "0.95rem" }}>
                ✅ {t.text}
              </li>
            ))}
          </ul>
      }
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Q8 — Student List Renderer
// ─────────────────────────────────────────────────────────────
export function AppQ8() {
  const students = [
    { rollNo: 1, name: "Aanya Singh", marks: 88 },
    { rollNo: 2, name: "Rahul Verma", marks: 32 },
    { rollNo: 3, name: "Sneha Nair", marks: 76 },
    { rollNo: 4, name: "Dev Patel", marks: 55 },
    { rollNo: 5, name: "Meera Joshi", marks: 91 },
    { rollNo: 6, name: "Kiran Das", marks: 28 },
    { rollNo: 7, name: "Aman Gupta", marks: 63 },
  ];

  const getColor = (marks) => {
    if (marks > 75) return "#d4edda";
    if (marks < 35) return "#f8d7da";
    return "#fff";
  };
  const getTextColor = (marks) => {
    if (marks > 75) return "#155724";
    if (marks < 35) return "#721c24";
    return "#1a1a2e";
  };

  return (
    <div style={{ maxWidth: 560, margin: "60px auto", fontFamily: "sans-serif", padding: "0 16px" }}>
      <h1>Q8 — Student List</h1>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ background: "#6c63ff", color: "#fff" }}>
            <th style={th}>Roll No</th>
            <th style={th}>Name</th>
            <th style={th}>Marks</th>
          </tr>
        </thead>
        <tbody>
          {students.map((s) => (
            <tr key={s.rollNo} style={{ background: getColor(s.marks), color: getTextColor(s.marks) }}>
              <td style={td}>{s.rollNo}</td>
              <td style={td}>{s.name}</td>
              <td style={{ ...td, fontWeight: "700" }}>{s.marks}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p style={{ fontSize: "0.8rem", color: "#888", marginTop: "12px" }}>
        🟢 &gt;75 marks (green) &nbsp; 🔴 &lt;35 marks (red)
      </p>
    </div>
  );
}

const th = { padding: "12px 16px", textAlign: "left", fontWeight: "600" };
const td = { padding: "10px 16px", borderBottom: "1px solid #eee" };

// ─────────────────────────────────────────────────────────────
// Q9 — Live Clock with useEffect
// ─────────────────────────────────────────────────────────────
export function AppQ9() {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "100vh", background: "#1a1a2e" }}>
      <h1 style={{ color: "#eee", marginBottom: "32px" }}>Q9 — Live Clock</h1>
      <Clock />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Q10 — Users Fetcher with useEffect
// ─────────────────────────────────────────────────────────────
export function AppQ10() {
  return (
    <div style={{ minHeight: "100vh", padding: "40px", background: "#f0f2f5", fontFamily: "sans-serif" }}>
      <h1 style={{ textAlign: "center", color: "#1a1a2e" }}>Q10 — Users Fetcher</h1>
      <UsersFetcher />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Q11 — Document Title Tracker
// ─────────────────────────────────────────────────────────────
export function AppQ11() {
  const [count, setCount] = useState(0);

  useEffect(() => {
    document.title = `Count: ${count}`;
    return () => { document.title = "React App"; };
  }, [count]);

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "100vh", gap: "20px", fontFamily: "sans-serif" }}>
      <h1>Q11 — Document Title Tracker</h1>
      <p style={{ color: "#777" }}>Check your browser tab title!</p>
      <div style={{ fontSize: "4rem", fontWeight: "700", color: "#6c63ff" }}>{count}</div>
      <div style={{ display: "flex", gap: "12px" }}>
        <button onClick={() => setCount((c) => c - 1)} style={{ padding: "10px 24px", background: "#e74c3c", color: "#fff", border: "none", borderRadius: "8px", cursor: "pointer", fontWeight: "700", fontSize: "1.2rem" }}>−</button>
        <button onClick={() => setCount((c) => c + 1)} style={{ padding: "10px 24px", background: "#2ecc71", color: "#fff", border: "none", borderRadius: "8px", cursor: "pointer", fontWeight: "700", fontSize: "1.2rem" }}>+</button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Q12 — Expensive Filter with useMemo
// ─────────────────────────────────────────────────────────────
export function AppQ12() {
  return (
    <div style={{ minHeight: "100vh", background: "#f0f2f5", padding: "40px" }}>
      <h1 style={{ textAlign: "center", color: "#1a1a2e" }}>Q12 — useMemo Filter</h1>
      <ExpensiveFilter />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Q13 — Prime Number Calculator with useMemo
// ─────────────────────────────────────────────────────────────
export function AppQ13() {
  return <PrimeCalculator />;
}

// ─────────────────────────────────────────────────────────────
// Q14 — Todo List with useCallback + React.memo
// ─────────────────────────────────────────────────────────────
export function AppQ14() {
  const [todos, setTodos] = useState([
    { id: 1, text: "Learn React Hooks" },
    { id: 2, text: "Practice useCallback" },
    { id: 3, text: "Build a project" },
  ]);
  const [input, setInput] = useState("");
  const [counter, setCounter] = useState(0); // unrelated state

  const deleteTodo = useCallback((id) => {
    setTodos((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <div style={{ maxWidth: 480, margin: "60px auto", fontFamily: "sans-serif", padding: "0 16px" }}>
      <h1>Q14 — useCallback + React.memo</h1>
      <p style={{ fontSize: "0.85rem", color: "#888" }}>Open console to see which items re-render.</p>

      <button
        onClick={() => setCounter((c) => c + 1)}
        style={{ padding: "6px 16px", background: "#f39c12", color: "#fff", border: "none", borderRadius: "6px", cursor: "pointer", marginBottom: "16px" }}
      >
        Unrelated counter: {counter}
      </button>

      <div style={{ display: "flex", gap: "10px", marginBottom: "20px" }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="New task..."
          style={{ flex: 1, padding: "8px 12px", borderRadius: "6px", border: "1px solid #ccc" }}
        />
        <button
          onClick={() => { if (input.trim()) { setTodos((t) => [...t, { id: Date.now(), text: input.trim() }]); setInput(""); } }}
          style={{ padding: "8px 16px", background: "#6c63ff", color: "#fff", border: "none", borderRadius: "6px", cursor: "pointer", fontWeight: "600" }}
        >
          Add
        </button>
      </div>

      <ul style={{ listStyle: "none", padding: 0 }}>
        {todos.map((todo) => (
          <TodoItem key={todo.id} todo={todo} onDelete={deleteTodo} />
        ))}
      </ul>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Q15 — Counter + Child Button (useCallback)
// ─────────────────────────────────────────────────────────────
export function AppQ15() {
  const [count, setCount] = useState(0);
  const [theme, setTheme] = useState("light");

  console.log("🔁 Parent rendered");

  const handleIncrement = useCallback(() => {
    setCount((c) => c + 1);
  }, []);

  const isDark = theme === "dark";

  return (
    <div style={{
      minHeight: "100vh",
      background: isDark ? "#1a1a2e" : "#f5f5f5",
      color: isDark ? "#eee" : "#1a1a2e",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      gap: "20px",
      fontFamily: "sans-serif",
      transition: "all 0.3s",
    }}>
      <h1>Q15 — useCallback + React.memo</h1>
      <p style={{ fontSize: "0.85rem", color: isDark ? "#aaa" : "#888" }}>Open console — toggling theme should NOT re-render IncrementButton.</p>

      <div style={{ fontSize: "3.5rem", fontWeight: "700", color: "#6c63ff" }}>{count}</div>

      <IncrementButton onIncrement={handleIncrement} />

      <button
        onClick={() => setTheme((t) => (t === "light" ? "dark" : "light"))}
        style={{
          padding: "10px 24px",
          background: isDark ? "#eee" : "#1a1a2e",
          color: isDark ? "#1a1a2e" : "#eee",
          border: "none",
          borderRadius: "8px",
          cursor: "pointer",
          fontWeight: "600",
        }}
      >
        Toggle Theme (won't re-render child)
      </button>
    </div>
  );
}
