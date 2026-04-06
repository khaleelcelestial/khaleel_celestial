// ── Grab elements ───────────────────────────────────────
const grandparent = document.getElementById("grandparent");
const parent      = document.getElementById("parent");
const child       = document.getElementById("child");
const stopProp    = document.getElementById("stop-prop");
const stopImm     = document.getElementById("stop-imm");
const logDiv      = document.getElementById("log");
const clearBtn    = document.getElementById("clear-btn");

// ── Helper: write a line to the log box ─────────────────
function log(message, type) {
  // Remove the hint text on first log
  const hint = document.querySelector(".log-hint");
  if (hint) hint.remove();

  const p = document.createElement("p");
  p.className  = `log-entry ${type}`;   // "capture", "bubble", or "stopped"
  p.textContent = message;
  logDiv.appendChild(p);
}

// ── CAPTURING phase listeners (third argument = true) ───
// Fire top → down:  grandparent → parent → child

grandparent.addEventListener("click", () => {
  log("CAPTURE: grandparent  (phase 1)", "capture");
}, true);

parent.addEventListener("click", () => {
  log("CAPTURE: parent  (phase 1)", "capture");
}, true);

child.addEventListener("click", () => {
  log("CAPTURE: child  (phase 2 — target)", "capture");
}, true);

// ── BUBBLING phase listeners (no third argument) ────────
// Fire bottom → up:  child → parent → grandparent

child.addEventListener("click", () => {
  log("BUBBLE: child  (phase 2 — target)", "bubble");
});

parent.addEventListener("click", (e) => {
  log("BUBBLE: parent  (phase 3)", "bubble");

  // stopPropagation — stops the event going up any further
  // grandparent bubble listener will NOT fire
  if (stopProp.checked) {
    e.stopPropagation();
    log("⛔ stopPropagation() called — bubble stops here", "stopped");
  }

  // stopImmediatePropagation — same as above PLUS
  // stops any other listeners on THIS element from running
  if (stopImm.checked) {
    e.stopImmediatePropagation();
    log("⛔ stopImmediatePropagation() called — bubble stops + no more listeners on parent", "stopped");
  }
});

// Second listener on parent — to show stopImmediatePropagation effect
parent.addEventListener("click", () => {
  log("BUBBLE: parent (2nd listener) — only runs if stopImmediate is OFF", "bubble");
});

grandparent.addEventListener("click", () => {
  log("BUBBLE: grandparent  (phase 3)", "bubble");
});

// ── Clear log ───────────────────────────────────────────
clearBtn.addEventListener("click", () => {
  logDiv.innerHTML = "";
});