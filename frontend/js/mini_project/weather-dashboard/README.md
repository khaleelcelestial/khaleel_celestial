# 🌤️ Atmosphera — Weather Dashboard

**JavaScript Day-2 Mini Project**  
A browser-based Weather Dashboard demonstrating all Day-2 concepts.

---

## 🚀 How to Run

> **Important:** Because this project uses ES6 Modules (`type="module"`), you **must** serve it via a local HTTP server — opening `index.html` directly with `file://` will not work.

### Option 1 — VS Code Live Server (recommended)
1. Install the "Live Server" extension in VS Code
2. Right-click `index.html` → **Open with Live Server**

### Option 2 — Python
```bash
cd weather-dashboard
python3 -m http.server 8080
# Open http://localhost:8080
```

### Option 3 — Node.js
```bash
cd weather-dashboard
npx serve .
# Open the displayed URL
```

---

## 📦 Project Structure

```
weather-dashboard/
├── index.html              # Entry HTML
├── src/
│   ├── style.css           # All styles (CSS variables, theming, animations)
│   ├── app.js              # Main entry — wires all modules
│   └── modules/
│       ├── api.js          # Weather & Geocoding API (Open-Meteo, free, no key needed)
│       ├── storage.js      # localStorage + Cookie utilities
│       ├── ui.js           # All DOM manipulation (createElement, DocumentFragment)
│       ├── toast.js        # Singleton toast notification system
│       └── utils.js        # Barrel file: debounce, throttle, validators, formatters
└── README.md
```

---

## ✅ Day-2 Concepts Demonstrated

| Concept | Where |
|---|---|
| **DOM Selection & Manipulation** | `ui.js` — all elements built with `createElement`, `DocumentFragment` |
| **Event Handling & Delegation** | `app.js` — single listener on `#favorites-list` |
| **Event Bubbling** | Search button handles bubbling from nested SVG child |
| **Form Validation** | City name validated with regex + `setCustomValidity()` |
| **alert / confirm** | `confirm()` used before removing favorites |
| **Fetch API** | `api.js` — `async/await`, `AbortController`, error handling |
| **Loading & Error States** | `ui.js` — spinner + error card with retry button |
| **localStorage** | `storage.js` — favorites list + theme preference |
| **Cookies** | `CookieManager` in `storage.js` — last searched city (7-day expiry) |
| **JSON** | `JSON.parse`/`stringify` for favorites serialization |
| **Debounce** | `utils.js` — hand-written, applied to search input (400ms) |
| **ES6 Modules** | All code split into named/default exports, barrel file |
| **Observer Pattern** | `app.js` — `state.subscribe()` triggers UI re-render on change |
| **Accessibility** | ARIA labels, roles, keyboard navigation on favorites |
| **Performance** | `DocumentFragment` for favorites list rendering |

---

## 🌐 API Used

**Open-Meteo** (https://open-meteo.com)  
- ✅ Free, no API key required
- ✅ Geocoding + Weather in one pipeline
- ✅ Works directly from the browser

---

## 🎨 Features

- 🔍 Debounced live search (400ms)
- 🌤️ Real weather data for any city in the world
- ⭐ Favorite cities — persisted across page reloads
- 🌙 Dark/Light theme toggle — persisted in localStorage
- 🍪 Last searched city remembered via cookie (7 days)
- 🔔 Toast notifications (success, error, warning, info)
- ♿ Fully keyboard navigable
- 📱 Responsive layout

---

## 📝 Notes

- No frameworks used — pure Vanilla JS + CSS
- No `innerHTML` used for user-supplied data (XSS safe)
- All modules use ES6 `import`/`export`
