/* ── ApiService Class ───────────────────────────────────────────────── */

class ApiService {

  constructor(baseURL) {

    // 1. Create a custom Axios instance with base config
    this.client = axios.create({
      baseURL,                                    // all requests prefix with this
      timeout: 10000,                             // 10 seconds — then auto-cancel
      headers: { "Content-Type": "application/json" }
    });

    this.authToken = null;    // stores the JWT token
    this.retryCount = 2;      // how many times to retry on 5xx errors

    // Attach interceptors
    this.addRequestInterceptor();
    this.addResponseInterceptor();
  }


  /* ── Set auth token (call this after login) ───────────────────────── */
  setAuthToken(token) {
    this.authToken = token;
    log("info", `Auth token set: Bearer ${token}`);
  }


  /* ── Request Interceptor ──────────────────────────────────────────── */
  // Runs on EVERY request before it is sent
  addRequestInterceptor() {
    this.client.interceptors.request.use(
      (config) => {
        // Add Authorization header if we have a token
        if (this.authToken) {
          config.headers["Authorization"] = `Bearer ${this.authToken}`;
        }

        // Log what we are about to send
        console.log(`→ ${config.method.toUpperCase()} ${config.baseURL}${config.url}`);

        return config;    // MUST return config — Axios waits for this
      },
      (error) => {
        // If something goes wrong building the request
        return Promise.reject(error);
      }
    );
  }


  /* ── Response Interceptor ─────────────────────────────────────────── */
  // Runs on EVERY response that comes back
  addResponseInterceptor() {
    this.client.interceptors.response.use(

      // SUCCESS handler (status 2xx)
      (response) => {
        console.log(`← ${response.status} ${response.config.url}`);
        return response;    // pass the response through unchanged
      },

      // ERROR handler (status 4xx, 5xx, network error, timeout)
      async (error) => {
        const status  = error.response?.status;
        const config  = error.config;

        // ── Retry logic for 5xx server errors ─────────────────────
        // config._retryCount tracks how many retries this request has done
        if (status >= 500) {
          config._retryCount = config._retryCount || 0;

          if (config._retryCount < this.retryCount) {
            config._retryCount++;
            console.warn(`Retrying request... attempt ${config._retryCount}`);
            logRetry(config._retryCount);

            // Wait 1 second before retrying
            await new Promise(resolve => setTimeout(resolve, 1000));

            // Re-send the exact same request
            return this.client(config);
          }
        }

        // ── Handle specific status codes ───────────────────────────
        if (status === 401) {
          console.warn("Unauthorized — redirect to login");
        } else if (status === 404) {
          console.warn("Resource not found");
        } else if (status === 500) {
          console.warn("Server error — try again later");
        } else if (!error.response) {
          console.warn("Network error or timeout");
        }

        // ── Build an enriched error object to throw ────────────────
        const enrichedError = {
          message: error.message,
          status:  status || "Network Error",
          originalError: error
        };

        return Promise.reject(enrichedError);   // throw it to the caller
      }
    );
  }


  /* ── CRUD Methods ─────────────────────────────────────────────────── */
  // Each method calls this.client (the Axios instance)
  // params becomes ?key=value in the URL for GET requests
  // data becomes the JSON body for POST/PUT

  async get(url, params = {}) {
    try {
      const response = await this.client.get(url, { params });
      return { data: response.data, status: response.status };
    } catch (err) {
      this.logError("GET", url, err);
      throw err;
    }
  }

  async post(url, data) {
    try {
      const response = await this.client.post(url, data);
      return { data: response.data, status: response.status };
    } catch (err) {
      this.logError("POST", url, err);
      throw err;
    }
  }

  async put(url, data) {
    try {
      const response = await this.client.put(url, data);
      return { data: response.data, status: response.status };
    } catch (err) {
      this.logError("PUT", url, err);
      throw err;
    }
  }

  async delete(url) {
    try {
      const response = await this.client.delete(url);
      return { data: response.data, status: response.status };
    } catch (err) {
      this.logError("DELETE", url, err);
      throw err;
    }
  }


  /* ── Log error to page ────────────────────────────────────────────── */
  logError(method, url, err) {
    logCard(method, url, false, err.status || "ERR", err.message, true);
  }
}


/* ── Create instance and set token ─────────────────────────────────── */
const api = new ApiService("https://jsonplaceholder.typicode.com");
api.setAuthToken("my-jwt-token-123");


/* ── Button handlers ────────────────────────────────────────────────── */

async function handleGet() {
  try {
    const result = await api.get("/posts", { _limit: 5 });
    logCard("GET", "/posts?_limit=5", true, result.status, result.data);
  } catch (err) {}
}

async function handlePost() {
  try {
    const result = await api.post("/posts", {
      title: "Hello World",
      body:  "This is the body.",
      userId: 1
    });
    logCard("POST", "/posts", true, result.status, result.data);
  } catch (err) {}
}

async function handlePut() {
  try {
    const result = await api.put("/posts/1", { title: "Updated Title" });
    logCard("PUT", "/posts/1", true, result.status, result.data);
  } catch (err) {}
}

async function handleDelete() {
  try {
    const result = await api.delete("/posts/1");
    logCard("DELETE", "/posts/1", true, result.status, result.data);
  } catch (err) {}
}

// JSONPlaceholder doesn't actually return 401/404 for these
// but we simulate them by hitting non-existent routes
async function handle401() {
  try {
    await api.get("/auth/protected");
  } catch (err) {
    logCard("GET", "/auth/protected", false, err.status, err.message, true);
  }
}

async function handle404() {
  try {
    await api.get("/posts/99999");
  } catch (err) {
    logCard("GET", "/posts/99999", false, err.status, err.message, true);
  }
}


/* ── DOM logging helpers ────────────────────────────────────────────── */

function logCard(method, url, ok, status, data, isError = false) {
  const logDiv = document.getElementById("log");

  const card = document.createElement("div");
  card.className = "log-card";

  // Header row
  const header = document.createElement("div");
  header.className = "log-header";

  const methodBadge = document.createElement("span");
  methodBadge.className = `method ${method}`;
  methodBadge.textContent = method;

  const urlSpan = document.createElement("span");
  urlSpan.className = "url";
  urlSpan.textContent = url;

  const statusSpan = document.createElement("span");
  statusSpan.className = ok ? "status-ok" : "status-err";
  statusSpan.textContent = status;

  header.appendChild(methodBadge);
  header.appendChild(urlSpan);
  header.appendChild(statusSpan);

  // Body
  const body = document.createElement("div");
  body.className = `log-body${isError ? " error" : ""}`;
  body.textContent = JSON.stringify(data, null, 2);

  card.appendChild(header);
  card.appendChild(body);
  logDiv.insertBefore(card, logDiv.firstChild);
}

function logRetry(attempt) {
  const logDiv = document.getElementById("log");
  const badge = document.createElement("div");
  badge.className = "log-card";
  badge.style.padding = "8px 14px";
  badge.innerHTML = `<span class="retry-badge">Retry attempt ${attempt} — waiting 1s...</span>`;
  logDiv.insertBefore(badge, logDiv.firstChild);
}

function log(type, message) {
  console.log(`[${type}] ${message}`);
}

function clearLog() {
  document.getElementById("log").innerHTML = "";
}