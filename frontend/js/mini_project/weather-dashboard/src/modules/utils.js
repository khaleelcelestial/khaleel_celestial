/**
 * utils/index.js — Barrel file re-exporting all utilities
 * Debounce, Throttle, Validators, Formatters
 */

// ─── Debounce ──────────────────────────────────────────────────────────────

/**
 * Debounce: delays fn execution until after `delay` ms of inactivity
 * @param {Function} fn
 * @param {number} delay
 * @returns {Function}
 */
export function debounce(fn, delay) {
  let timer = null;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => {
      fn.apply(this, args);
      timer = null;
    }, delay);
  };
}

// ─── Throttle ─────────────────────────────────────────────────────────────

/**
 * Throttle: ensures fn is called at most once per `interval` ms
 * @param {Function} fn
 * @param {number} interval
 * @returns {Function}
 */
export function throttle(fn, interval) {
  let lastTime = 0;
  return function (...args) {
    const now = Date.now();
    if (now - lastTime >= interval) {
      lastTime = now;
      fn.apply(this, args);
    }
  };
}

// ─── Validators ───────────────────────────────────────────────────────────

/**
 * Validate a city name: non-empty, letters/spaces/hyphens only, min 2 chars
 * @param {string} value
 * @returns {{ valid: boolean, message: string }}
 */
export function validateCityName(value) {
  const trimmed = value.trim();
  if (!trimmed) return { valid: false, message: "City name is required." };
  if (trimmed.length < 2) return { valid: false, message: "Minimum 2 characters." };
  if (!/^[a-zA-Z\s\-'.]+$/.test(trimmed))
    return { valid: false, message: "Only letters, spaces, and hyphens allowed." };
  return { valid: true, message: "" };
}

export function validateEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

export function validatePhone(phone) {
  return /^\d{10}$/.test(phone);
}

export function validatePassword(password) {
  return (
    password.length >= 8 &&
    /[A-Z]/.test(password) &&
    /[a-z]/.test(password) &&
    /[0-9]/.test(password) &&
    /[^A-Za-z0-9]/.test(password)
  );
}

// ─── Formatters ───────────────────────────────────────────────────────────

/**
 * Format a Date to a readable string
 * @param {Date|string|number} date
 * @returns {string}
 */
export function formatDate(date) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(date));
}

/**
 * Format number as currency
 * @param {number} amount
 * @param {string} currency
 * @returns {string}
 */
export function formatCurrency(amount, currency = "USD") {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(amount);
}

/**
 * Capitalize first letter of each word
 * @param {string} str
 * @returns {string}
 */
export function capitalize(str) {
  return str
    .toLowerCase()
    .replace(/(?:^|\s)\S/g, (c) => c.toUpperCase());
}

/**
 * Format temperature with degree symbol
 * @param {number} temp
 * @returns {string}
 */
export function formatTemp(temp) {
  return `${temp}°C`;
}
