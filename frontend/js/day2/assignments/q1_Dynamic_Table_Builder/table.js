const users = [
  { name: "Alice", age: 25, role: "Developer" },
  { name: "Bob",   age: 30, role: "Designer"  },
  { name: "Carol", age: 28, role: "Manager"   },
];

const columns = [
  { key: "name", label: "Full Name" },
  { key: "age",  label: "Age",      format: (val) => `${val} years` },
  { key: "role", label: "Position" },
];

function buildTable(containerId, data, columns) {
  // Step 1: Select container
  const container = document.getElementById(containerId);
  // Step 2: Create table structure
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const tbody = document.createElement("tbody");
  // Step 3: Create header row
  const headerRow = document.createElement("tr");
  columns.forEach(col => {
    const th = document.createElement("th");
    th.textContent = col.label;
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  // Step 4: Create table rows
  data.forEach((item, index) => {
    const tr = document.createElement("tr");
    tr.dataset.index = index;
    if (index % 2 === 0) {
      tr.classList.add("even");
    } else {
      tr.classList.add("odd");
    }
    columns.forEach(col => {
      const td = document.createElement("td");
      let value = item[col.key];
      if (col.format) {
        value = col.format(value);
      }
      td.textContent = value;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  // Step 5: Combine everything
  table.appendChild(thead);
  table.appendChild(tbody);
  // Step 6: Add to DOM
  container.appendChild(table);
}

buildTable("app", users, columns);