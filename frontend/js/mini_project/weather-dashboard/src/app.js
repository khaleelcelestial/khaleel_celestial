/**
 * app.js — Main Entry Point
 * Wires all modules together using Observer pattern for state management
 * Demonstrates: debounce, event delegation, form validation, localStorage,
 * cookies, ES6 modules, error handling, loading states, bubbling
 */

import { searchCity } from "./modules/api.js";
import {
  loadFavorites,
  addFavorite,
  removeFavorite,
  isFavorite,
  loadTheme,
  saveTheme,
  saveLastSearchCookie,
  loadLastSearchCookie,
} from "./modules/storage.js";
import { renderWeatherCard, renderFavorites, showLoading, showError, updateStarButton } from "./modules/ui.js";
import { toast } from "./modules/toast.js";
import { debounce, validateCityName } from "./modules/utils.js";

// ─── Observer / State ──────────────────────────────────────────────────────

/**
 * Simple Observer pattern for app state.
 * When state changes, all registered listeners are notified.
 */
const state = {
  favorites: loadFavorites(),
  currentWeather: null,
  theme: loadTheme(),
  _listeners: [],

  subscribe(fn) {
    this._listeners.push(fn);
  },

  notify() {
    for (const fn of this._listeners) fn(this);
  },

  setFavorites(favs) {
    this.favorites = favs;
    this.notify();
  },

  setCurrentWeather(data) {
    this.currentWeather = data;
    this.notify();
  },

  setTheme(theme) {
    this.theme = theme;
    this.notify();
  },
};

// ─── DOM References ────────────────────────────────────────────────────────

const searchInput = document.getElementById("search-input");
const searchBtn = document.getElementById("search-btn");
const searchError = document.getElementById("search-error");
const themeToggle = document.getElementById("theme-toggle");
const themeIcon = themeToggle.querySelector(".theme-icon");

// ─── Theme ────────────────────────────────────────────────────────────────

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  themeIcon.textContent = theme === "dark" ? "🌙" : "☀️";
}

themeToggle.addEventListener("click", () => {
  const next = state.theme === "dark" ? "light" : "dark";
  state.setTheme(next);
  saveTheme(next);
  applyTheme(next);
  toast.show(`Switched to ${next} mode`, "info", 2000);
});

// ─── Form Validation ──────────────────────────────────────────────────────

function showFieldError(message) {
  searchInput.classList.add("invalid");
  searchError.textContent = message;
  searchInput.setCustomValidity(message);
}

function clearFieldError() {
  searchInput.classList.remove("invalid");
  searchError.textContent = "";
  searchInput.setCustomValidity("");
}

searchInput.addEventListener("input", () => {
  // Clear error as user types (validate on blur / submit)
  if (searchInput.classList.contains("invalid")) {
    clearFieldError();
  }
});

searchInput.addEventListener("blur", () => {
  const val = searchInput.value;
  if (val) {
    const { valid, message } = validateCityName(val);
    if (!valid) showFieldError(message);
    else clearFieldError();
  }
});

// ─── Search Functionality ──────────────────────────────────────────────────

let lastSearchedCity = null;

async function doSearch(cityName) {
  const { valid, message } = validateCityName(cityName);
  if (!valid) {
    showFieldError(message);
    return;
  }
  clearFieldError();

  lastSearchedCity = cityName.trim();
  showLoading();

  const start = performance.now();

  try {
    const data = await searchCity(cityName.trim());
    const elapsed = ((performance.now() - start) / 1000).toFixed(2);

    state.setCurrentWeather(data);
    renderWeatherCard(data, handleToggleFavorite);
    saveLastSearchCookie(data.city);

    console.log(`[Weather] Fetched "${data.city}" in ${elapsed}s`);
    toast.show(`Showing weather for ${data.city}`, "success", 2500);
  } catch (err) {
    if (err.message === "Request cancelled") return; // silently ignore
    console.error("[Weather] Error:", err.message);

    const userMsg =
      err.message.includes("not found")
        ? `City "${cityName.trim()}" not found. Check spelling?`
        : "Network error. Check your connection.";

    showError(userMsg, () => doSearch(cityName));
    toast.show(userMsg, "error", 5000);
  }
}

// Debounced search (triggers 400ms after user stops typing)
const debouncedSearch = debounce((value) => {
  if (value.trim().length >= 2) doSearch(value);
}, 400);

searchInput.addEventListener("input", (e) => {
  debouncedSearch(e.target.value);
});

// Enter key → immediate search
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    doSearch(searchInput.value);
  }
});

// Search button click — demonstrates event bubbling through nested SVG
searchBtn.addEventListener("click", (e) => {
  // event.target may be the SVG or its child elements (bubbling up)
  // We handle click on the button regardless of which child triggered it
  doSearch(searchInput.value);
});

// ─── Favorites ────────────────────────────────────────────────────────────

function handleToggleFavorite(data, starBtn) {
  const fav = isFavorite(data.city);
  if (fav) {
    const confirmed = confirm(`Remove "${data.city}" from favorites?`);
    if (!confirmed) return;
    const updated = removeFavorite(data.city);
    state.setFavorites(updated);
    updateStarButton(starBtn, false);
    toast.show(`${data.city} removed from favorites`, "warning", 2500);
  } else {
    const updated = addFavorite(data);
    state.setFavorites(updated);
    updateStarButton(starBtn, true);
    toast.show(`${data.city} added to favorites! ⭐`, "success", 2500);
  }
}

/**
 * Event Delegation on favorites list (single listener)
 * All clicks on the list are handled here
 */
document.getElementById("favorites-list").addEventListener("click", (e) => {
  // Delegation: find closest fav-item or fav-delete-btn
  const deleteBtn = e.target.closest(".fav-delete-btn");
  const favItem = e.target.closest(".fav-item");

  if (deleteBtn) {
    // handled inside renderFavorites' own listener (stopPropagation)
    return;
  }

  if (favItem) {
    const cityName = favItem.querySelector(".fav-item-name")?.textContent;
    if (cityName) {
      searchInput.value = cityName;
      doSearch(cityName);
    }
  }
});

function handleDeleteFavorite(cityName) {
  const confirmed = confirm(`Remove "${cityName}" from favorites?`);
  if (!confirmed) return;

  const updated = removeFavorite(cityName);
  state.setFavorites(updated);

  // If currently viewing that city, update star
  if (state.currentWeather?.city?.toLowerCase() === cityName.toLowerCase()) {
    const starBtn = document.querySelector(".fav-star-btn");
    if (starBtn) updateStarButton(starBtn, false);
  }

  toast.show(`${cityName} removed from favorites`, "warning", 2500);
}

// ─── Observer: Re-render favorites on state change ─────────────────────────

state.subscribe((s) => {
  renderFavorites(
    s.favorites,
    (cityName) => {
      searchInput.value = cityName;
      doSearch(cityName);
    },
    handleDeleteFavorite
  );
});

// ─── Init ─────────────────────────────────────────────────────────────────

function init() {
  // Apply saved theme
  applyTheme(state.theme);

  // Render favorites from localStorage
  renderFavorites(
    state.favorites,
    (cityName) => {
      searchInput.value = cityName;
      doSearch(cityName);
    },
    handleDeleteFavorite
  );

  // Pre-fill search from last-search cookie
  const lastCity = loadLastSearchCookie();
  if (lastCity) {
    searchInput.value = lastCity;
    toast.show(`Welcome back! Showing last search: ${lastCity}`, "info", 3000);
    doSearch(lastCity);
  }
}

init();
