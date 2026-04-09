import { useState, useMemo } from "react";

function countPrimes(n) {
  if (n < 2) return 0;
  const sieve = new Uint8Array(n + 1).fill(1);
  sieve[0] = sieve[1] = 0;
  for (let i = 2; i * i <= n; i++) {
    if (sieve[i]) {
      for (let j = i * i; j <= n; j += i) sieve[j] = 0;
    }
  }
  return sieve.reduce((acc, v) => acc + v, 0);
}

function PrimeCalculator() {
  const [n, setN] = useState(100);
  const [theme, setTheme] = useState("light");

  const primeCount = useMemo(() => {
    console.log("⚙️ Computing primes for N =", n);
    return countPrimes(Number(n));
  }, [n]);

  const isDark = theme === "dark";

  return (
    <div
      style={{
        background: isDark ? "#1a1a2e" : "#f5f5f5",
        color: isDark ? "#eee" : "#1a1a2e",
        minHeight: "100vh",
        padding: "32px",
        fontFamily: "sans-serif",
        transition: "all 0.3s",
      }}
    >
      <h2>Prime Number Calculator (useMemo)</h2>

      <button
        onClick={() => setTheme((t) => (t === "light" ? "dark" : "light"))}
        style={{
          marginBottom: "20px",
          padding: "8px 18px",
          background: isDark ? "#eee" : "#1a1a2e",
          color: isDark ? "#1a1a2e" : "#eee",
          border: "none",
          borderRadius: "6px",
          cursor: "pointer",
        }}
      >
        Toggle Theme (won't recompute primes)
      </button>

      <div>
        <label style={{ display: "block", marginBottom: "8px" }}>
          Enter N:
          <input
            type="number"
            value={n}
            min={1}
            max={500000}
            onChange={(e) => setN(e.target.value)}
            style={{
              marginLeft: "12px",
              padding: "8px 12px",
              borderRadius: "6px",
              border: "1px solid #ccc",
              width: "160px",
            }}
          />
        </label>
      </div>

      <p style={{ fontSize: "1.3rem", marginTop: "16px" }}>
        Primes from 1 to {n}: <strong>{primeCount}</strong>
      </p>
      <p style={{ fontSize: "0.8rem", color: isDark ? "#aaa" : "#888" }}>
        (Check console — computation only runs when N changes)
      </p>
    </div>
  );
}

export default PrimeCalculator;
