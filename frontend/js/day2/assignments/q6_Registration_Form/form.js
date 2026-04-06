// ── Grab all inputs and error spans ─────────────────────
const form            = document.getElementById("form");
const nameInput       = document.getElementById("name");
const emailInput      = document.getElementById("email");
const phoneInput      = document.getElementById("phone");
const passwordInput   = document.getElementById("password");
const confirmInput    = document.getElementById("confirm");
const ageInput        = document.getElementById("age");
const output          = document.getElementById("output");

const strengthFill    = document.getElementById("strength-fill");
const strengthLabel   = document.getElementById("strength-label");


// ── Helper: show or clear an error ──────────────────────
function showError(input, errorId, message) {
  const errorSpan = document.getElementById(errorId);

  if (message) {
    // There is an error
    errorSpan.textContent = message;
    input.classList.add("invalid");
    input.classList.remove("valid");
  } else {
    // No error — field is valid
    errorSpan.textContent = "";
    input.classList.add("valid");
    input.classList.remove("invalid");
  }
}


// ── Validation functions (each returns true/false) ──────

function validateName() {
  const value = nameInput.value.trim();
  // Must be at least 2 chars, letters and spaces only
  if (value.length < 2) {
    showError(nameInput, "name-error", "Name must be at least 2 characters");
    return false;
  }
  if (!/^[a-zA-Z ]+$/.test(value)) {
    showError(nameInput, "name-error", "Letters and spaces only");
    return false;
  }
  showError(nameInput, "name-error", "");
  return true;
}

function validateEmail() {
  const value = emailInput.value.trim();
  // Simple but solid email regex
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  if (!emailRegex.test(value)) {
    showError(emailInput, "email-error", "Enter a valid email address");
    return false;
  }
  showError(emailInput, "email-error", "");
  return true;
}

function validatePhone() {
  const value = phoneInput.value.trim();
  // Exactly 10 digits, nothing else
  if (!/^\d{10}$/.test(value)) {
    showError(phoneInput, "phone-error", "Phone must be exactly 10 digits");
    return false;
  }
  showError(phoneInput, "phone-error", "");
  return true;
}

function validatePassword() {
  const value = passwordInput.value;

  if (value.length < 8) {
    showError(passwordInput, "password-error", "Password must be at least 8 characters");
    return false;
  }
  if (!/[A-Z]/.test(value)) {
    showError(passwordInput, "password-error", "Must contain an uppercase letter");
    return false;
  }
  if (!/[a-z]/.test(value)) {
    showError(passwordInput, "password-error", "Must contain a lowercase letter");
    return false;
  }
  if (!/[0-9]/.test(value)) {
    showError(passwordInput, "password-error", "Must contain a number");
    return false;
  }
  if (!/[^A-Za-z0-9]/.test(value)) {
    showError(passwordInput, "password-error", "Must contain a special character");
    return false;
  }
  showError(passwordInput, "password-error", "");
  return true;
}

function validateConfirm() {
  if (confirmInput.value !== passwordInput.value) {
    showError(confirmInput, "confirm-error", "Passwords do not match");
    return false;
  }
  showError(confirmInput, "confirm-error", "");
  return true;
}

function validateAge() {
  const value = Number(ageInput.value);
  if (!ageInput.value || value < 18 || value > 120) {
    showError(ageInput, "age-error", "Age must be between 18 and 120");
    return false;
  }
  showError(ageInput, "age-error", "");
  return true;
}


// ── Password strength indicator ──────────────────────────
function checkStrength(value) {
  let score = 0;

  if (value.length >= 8)          score++;   // long enough
  if (/[A-Z]/.test(value))        score++;   // has uppercase
  if (/[0-9]/.test(value))        score++;   // has number
  if (/[^A-Za-z0-9]/.test(value)) score++;   // has special char

  // score 0-1 = Weak, 2-3 = Medium, 4 = Strong
  if (value.length === 0) {
    strengthFill.style.width      = "0%";
    strengthFill.style.background = "";
    strengthLabel.textContent     = "";
  } else if (score <= 1) {
    strengthFill.style.width      = "33%";
    strengthFill.style.background = "#ef4444";   // red
    strengthLabel.style.color     = "#ef4444";
    strengthLabel.textContent     = "Weak";
  } else if (score <= 3) {
    strengthFill.style.width      = "66%";
    strengthFill.style.background = "#f59e0b";   // amber
    strengthLabel.style.color     = "#f59e0b";
    strengthLabel.textContent     = "Medium";
  } else {
    strengthFill.style.width      = "100%";
    strengthFill.style.background = "#22c55e";   // green
    strengthLabel.style.color     = "#22c55e";
    strengthLabel.textContent     = "Strong";
  }
}


// ── Real-time listeners (blur = when field loses focus) ──
nameInput.addEventListener("blur", validateName);
emailInput.addEventListener("blur", validateEmail);
phoneInput.addEventListener("blur", validatePhone);
confirmInput.addEventListener("blur", validateConfirm);
ageInput.addEventListener("blur", validateAge);

// Password: validate + update strength bar on every keystroke
passwordInput.addEventListener("input", () => {
  checkStrength(passwordInput.value);
  validatePassword();
});


// ── Submit ───────────────────────────────────────────────
form.addEventListener("submit", (e) => {
  e.preventDefault();   // stop the page from reloading

  // Run all validations at once
  const allValid =
    validateName()    &
    validateEmail()   &
    validatePhone()   &
    validatePassword() &
    validateConfirm() &
    validateAge();

  // Single & (not &&) so ALL functions run even if one fails
  // This ensures all errors are shown at the same time

  if (allValid) {
    // Collect form data using the FormData API
    const data = new FormData(form);

    // Convert to a plain object (exclude confirmPassword from output)
    const result = {
      name:     data.get("name"),
      email:    data.get("email"),
      phone:    data.get("phone"),
      password: data.get("password"),
      age:      data.get("age"),
    };

    // Display as formatted JSON
    output.classList.remove("hidden");
    output.textContent = JSON.stringify(result, null, 2);
  }
});