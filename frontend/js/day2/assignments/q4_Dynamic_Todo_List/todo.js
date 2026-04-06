// ── State ──────────────────────────────────────────
let idCounter = 0;  // gives each todo a unique data-id

// ── Elements ───────────────────────────────────────
const input   = document.getElementById("todo-input");
const addBtn  = document.getElementById("add-btn");
const list    = document.getElementById("todo-list");

// ── Add todo ───────────────────────────────────────
function addTodo() {
  const text = input.value.trim();

  // Prevent empty todos
  if (text === "") return;

  idCounter++;

  // Build the <li>
  const li = document.createElement("li");
  li.dataset.id = idCounter;  // data-id="1", "2", ...

  // Text span (click to toggle, double-click to edit)
  const span = document.createElement("span");
  span.className = "todo-text";
  span.textContent = text;

  // Delete button
  const btn = document.createElement("button");
  btn.className = "delete-btn";
  btn.textContent = "X";

  li.appendChild(span);
  li.appendChild(btn);
  list.appendChild(li);

  // Clear input
  input.value = "";
  input.focus();
}

// Add on button click
addBtn.addEventListener("click", addTodo);

// Add on Enter key
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") addTodo();
});

// ── Single event listener on <ul> (Event Delegation) ──
list.addEventListener("click", (e) => {

  // Delete: clicked the X button
  if (e.target.classList.contains("delete-btn")) {
    const li = e.target.closest("li");
    li.remove();
    return;
  }

  // Toggle completed: clicked the text span
  if (e.target.classList.contains("todo-text")) {
    const li = e.target.closest("li");
    li.classList.toggle("completed");
  }
});

// Double-click to edit (also on the <ul>)
list.addEventListener("dblclick", (e) => {

  // Only trigger on the text span
  if (!e.target.classList.contains("todo-text")) return;

  const span = e.target;
  const li   = span.closest("li");

  // Create an input field with current text
  const editInput = document.createElement("input");
  editInput.className   = "edit-input";
  editInput.value       = span.textContent;

  // Hide the span, show the input
  span.style.display = "none";
  li.insertBefore(editInput, li.firstChild);
  editInput.focus();

  // Save function — runs on Enter or blur
  function saveEdit() {
    const newText = editInput.value.trim();

    // If empty, restore old text; otherwise save new text
    span.textContent   = newText !== "" ? newText : span.textContent;
    span.style.display = "";  // show span again
    editInput.remove();       // remove input field
  }

  editInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveEdit();
  });

  editInput.addEventListener("blur", saveEdit);
});