const BASE_URL = "https://jsonplaceholder.typicode.com/posts";


/* ── 1. fetchWithTimeout ────────────────────────────────
   Wraps fetch() with an AbortController.
   If the request takes longer than `timeout` ms, it cancels. */

async function fetchWithTimeout(url, options = {}, timeout = 5000) {
  const controller = new AbortController();

  // After `timeout` ms, controller.abort() fires
  // This cancels the fetch and throws an AbortError
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal   // link the abort controller to this fetch
    });
    clearTimeout(timer);          // request succeeded — cancel the timeout
    return response;
  } catch (err) {
    clearTimeout(timer);
    if (err.name === "AbortError") {
      throw new Error("Request timed out");
    }
    throw err;                    // re-throw any other network error
  }
}


/* ── 2. request() — core helper ─────────────────────────
   Every CRUD function calls this.
   Handles logging, headers, response.ok check, and JSON parsing. */

async function request(method, url, body = null) {

  // Build fetch options
  const options = {
    method,
    headers: { "Content-Type": "application/json" }
  };

  // Only attach body for POST and PUT
  if (body) {
    options.body = JSON.stringify(body);
  }

  try {
    const response = await fetchWithTimeout(url, options);

    // Log method, url, status to the page
    const status = `${response.status} ${response.statusText}`;
    logHeader(method, url, response.ok, status);

    // response.ok is true for status 200–299, false for 400, 404, 500, etc.
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    // DELETE returns an empty body — handle that safely
    const text = await response.text();
    const data = text ? JSON.parse(text) : {};

    logBody(data);
    return { success: true, data };

  } catch (err) {
    logBody({ error: err.message });
    return { success: false, error: err.message };
  }
}


/* ── 3. CRUD Functions ──────────────────────────────────
   Each one just calls request() with the right method/url/body */

// GET /posts  or  GET /posts?_limit=5
async function getAllPosts(limit) {
  const url = limit ? `${BASE_URL}?_limit=${limit}` : BASE_URL;
  return request("GET", url);
}

// GET /posts/1
async function getPost(id) {
  return request("GET", `${BASE_URL}/${id}`);
}

// POST /posts  (sends data as JSON body)
async function createPost(data) {
  return request("POST", BASE_URL, data);
}

// PUT /posts/1  (replaces the post with new data)
async function updatePost(id, data) {
  return request("PUT", `${BASE_URL}/${id}`, data);
}

// DELETE /posts/1
async function deletePost(id) {
  return request("DELETE", `${BASE_URL}/${id}`);
}


/* ── 4. Button handlers ─────────────────────────────────
   Called by onclick in the HTML */

async function handleGetAll()  { await getAllPosts(5); }
async function handleGetOne()  { await getPost(1); }
async function handleDelete()  { await deletePost(1); }

async function handleCreate() {
  await createPost({
    title:  "My New Post",
    body:   "This is the post body.",
    userId: 1
  });
}

async function handleUpdate() {
  await updatePost(1, {
    title:  "Updated Title",
    body:   "Updated body text.",
    userId: 1
  });
}


/* ── 5. Logging helpers ─────────────────────────────────
   Write each request and response visibly on the page */

let currentCard = null;   // keep reference so logBody can add to it

function logHeader(method, url, ok, status) {
  const card = document.createElement("div");
  card.className = "log-card";

  const header = document.createElement("div");
  header.className = "log-header";

  // Coloured method badge
  const methodBadge = document.createElement("span");
  methodBadge.className = `method ${method}`;
  methodBadge.textContent = method;

  // URL text
  const urlSpan = document.createElement("span");
  urlSpan.className = "url";
  urlSpan.textContent = url;

  // Status text
  const statusSpan = document.createElement("span");
  statusSpan.className = ok ? "status-ok" : "status-err";
  statusSpan.textContent = status;

  header.appendChild(methodBadge);
  header.appendChild(urlSpan);
  header.appendChild(statusSpan);
  card.appendChild(header);

  // Prepend so newest appears at the top
  const log = document.getElementById("log");
  log.insertBefore(card, log.firstChild);

  currentCard = card;   // save so logBody can append to same card
}

function logBody(data) {
  if (!currentCard) return;

  const body = document.createElement("div");
  body.className = "log-body";
  body.textContent = JSON.stringify(data, null, 2);
  currentCard.appendChild(body);
}

function clearLog() {
  document.getElementById("log").innerHTML = "";
}