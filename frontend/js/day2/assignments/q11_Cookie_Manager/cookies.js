/* ══════════════════════════════════════════════
   PART A — CookieManager
   ══════════════════════════════════════════════ */

const CookieManager = {

  /* ── set(name, value, options) ─────────────────
     Builds and writes a cookie string             */
  set(name, value, options = {}) {

    // encodeURIComponent handles special chars like =, spaces, &
    let cookie = `${encodeURIComponent(name)}=${encodeURIComponent(value)}`;

    // expires: number of days from now
    if (options.expires) {
      const date = new Date();
      date.setDate(date.getDate() + options.expires);   // add N days
      cookie += `; expires=${date.toUTCString()}`;
    }

    // path: which URLs can see this cookie (default is current path)
    if (options.path) {
      cookie += `; path=${options.path}`;
    }

    // domain: which domain can see this cookie
    if (options.domain) {
      cookie += `; domain=${options.domain}`;
    }

    // secure: cookie only sent over HTTPS
    if (options.secure) {
      cookie += "; secure";
    }

    // sameSite: controls cross-site sending (Strict / Lax / None)
    if (options.sameSite) {
      cookie += `; sameSite=${options.sameSite}`;
    }

    // Writing to document.cookie does NOT overwrite all cookies
    // It only adds or updates the ONE cookie you are writing
    document.cookie = cookie;

    print(`Set: ${cookie}`);
  },


  /* ── get(name) ─────────────────────────────────
     Finds one cookie by name from document.cookie */
  get(name) {
    const encodedName = encodeURIComponent(name);

    // document.cookie looks like: "theme=dark; visits=5; user=Alice"
    // Split by "; " to get each cookie as "key=value"
    const cookies = document.cookie.split("; ");

    for (const cookie of cookies) {
      // Split on the FIRST "=" only — values may contain "="
      const eqIndex = cookie.indexOf("=");
      const key     = cookie.substring(0, eqIndex).trim();
      const val     = cookie.substring(eqIndex + 1);

      if (key === encodedName) {
        return decodeURIComponent(val);   // decode back to readable string
      }
    }

    return null;   // cookie not found
  },


  /* ── getAll() ──────────────────────────────────
     Returns ALL cookies as a plain object         */
  getAll() {
    const result = {};

    if (!document.cookie) return result;   // no cookies at all

    const cookies = document.cookie.split("; ");

    for (const cookie of cookies) {
      const eqIndex = cookie.indexOf("=");
      const key     = decodeURIComponent(cookie.substring(0, eqIndex).trim());
      const val     = decodeURIComponent(cookie.substring(eqIndex + 1));
      result[key]   = val;
    }

    return result;
  },


  /* ── delete(name) ──────────────────────────────
     Deletes a cookie by setting its expiry to the past */
  delete(name) {
    // Setting expires to a past date makes the browser discard it
    document.cookie = `${encodeURIComponent(name)}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/`;
    print(`Deleted: ${name}`);
  },


  /* ── has(name) ─────────────────────────────────
     Returns true if the cookie exists             */
  has(name) {
    return this.get(name) !== null;
  }

};


/* ══════════════════════════════════════════════
   PART B — Session Tracker
   ══════════════════════════════════════════════ */

function initSession() {
  const now     = new Date().toISOString();
  const options = { expires: 30, path: "/" };

  // ── Visit count ──────────────────────────────
  // Read existing count, add 1, save it back
  const visits = parseInt(CookieManager.get("visits") || "0") + 1;
  CookieManager.set("visits", visits, options);

  // ── First visit ──────────────────────────────
  // Only set if it doesn't exist yet
  if (!CookieManager.has("firstVisit")) {
    CookieManager.set("firstVisit", now, options);
  }

  // ── Last visit ───────────────────────────────
  // Always update to current time
  CookieManager.set("lastVisit", now, options);

  // ── Theme ────────────────────────────────────
  // Default to "light" if never set
  if (!CookieManager.has("theme")) {
    CookieManager.set("theme", "light", options);
  }

  // Update the UI
  updateUI();
}


/* ── updateUI() ────────────────────────────────
   Reads cookies and displays them in the cards  */
function updateUI() {
  const visits     = CookieManager.get("visits");
  const firstVisit = CookieManager.get("firstVisit");
  const lastVisit  = CookieManager.get("lastVisit");
  const theme      = CookieManager.get("theme");

  document.getElementById("visit-count").textContent  = `#${visits}`;
  document.getElementById("first-visit").textContent  = formatDate(firstVisit);
  document.getElementById("last-visit").textContent   = formatDate(lastVisit);
  document.getElementById("theme-display").textContent = theme;

  // Apply theme class to body
  applyTheme(theme);
}


/* ── formatDate() ──────────────────────────────
   Converts ISO string to readable "Jan 15, 2025" */
function formatDate(isoString) {
  if (!isoString) return "—";
  return new Date(isoString).toLocaleDateString("en-US", {
    month: "short",
    day:   "numeric",
    year:  "numeric"
  });
}


/* ── applyTheme() ──────────────────────────────
   Adds/removes .dark class on body              */
function applyTheme(theme) {
  if (theme === "dark") {
    document.body.classList.add("dark");
  } else {
    document.body.classList.remove("dark");
  }
}


/* ── setTheme() ────────────────────────────────
   Called by theme buttons                       */
function setTheme(theme) {
  CookieManager.set("theme", theme, { expires: 30, path: "/" });
  document.getElementById("theme-display").textContent = theme;
  applyTheme(theme);
  print(`Theme changed to: ${theme}`);
}


/* ══════════════════════════════════════════════
   Cookie Tester — button handlers
   ══════════════════════════════════════════════ */

function handleSet() {
  const name  = document.getElementById("cookie-name").value.trim();
  const value = document.getElementById("cookie-value").value.trim();
  if (!name) { print("Enter a cookie name"); return; }
  CookieManager.set(name, value, { expires: 1, path: "/" });
}

function handleGet() {
  const name   = document.getElementById("get-name").value.trim();
  const result = CookieManager.get(name);
  print(result !== null ? `${name} = "${result}"` : `"${name}" not found`);
}

function handleDelete() {
  const name = document.getElementById("get-name").value.trim();
  if (!name) { print("Enter a cookie name to delete"); return; }
  CookieManager.delete(name);
}

function handleGetAll() {
  const all = CookieManager.getAll();
  print(JSON.stringify(all, null, 2));
}

function handleClear() {
  document.getElementById("output").textContent = "";
}


/* ── print() ───────────────────────────────────
   Appends a line to the output box             */
function print(message) {
  const out = document.getElementById("output");
  out.textContent += message + "\n";
}


/* ── Run session tracker on page load ──────── */
initSession();