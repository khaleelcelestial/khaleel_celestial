/**
 * storage.js — localStorage, sessionStorage & Cookie utilities
 */

// ─── localStorage: Favorites ───────────────────────────────────────────────

const FAVORITES_KEY = "atmosphera_favorites";

/**
 * Load favorites from localStorage
 * @returns {Array<{city:string, country:string, lat:number, lon:number}>}
 */
export function loadFavorites() {
  try {
    const raw = localStorage.getItem(FAVORITES_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

/**
 * Save favorites to localStorage
 * @param {Array} favorites
 */
export function saveFavorites(favorites) {
  try {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(favorites));
  } catch (e) {
    if (e.name === "QuotaExceededError") {
      throw new Error("Storage full! Please remove some favorites.");
    }
    throw e;
  }
}

/**
 * Add a city to favorites (no duplicates)
 * @param {{city, country, lat, lon}} cityData
 * @returns {Array}
 */
export function addFavorite(cityData) {
  const favs = loadFavorites();
  const exists = favs.some(
    (f) => f.city.toLowerCase() === cityData.city.toLowerCase()
  );
  if (!exists) {
    favs.push({ city: cityData.city, country: cityData.country, lat: cityData.lat, lon: cityData.lon });
    saveFavorites(favs);
  }
  return loadFavorites();
}

/**
 * Remove a city from favorites
 * @param {string} cityName
 * @returns {Array}
 */
export function removeFavorite(cityName) {
  const favs = loadFavorites().filter(
    (f) => f.city.toLowerCase() !== cityName.toLowerCase()
  );
  saveFavorites(favs);
  return favs;
}

/**
 * Check if a city is favorited
 * @param {string} cityName
 * @returns {boolean}
 */
export function isFavorite(cityName) {
  return loadFavorites().some(
    (f) => f.city.toLowerCase() === cityName.toLowerCase()
  );
}

// ─── localStorage: Theme ───────────────────────────────────────────────────

const THEME_KEY = "atmosphera_theme";

export function loadTheme() {
  return localStorage.getItem(THEME_KEY) || "dark";
}

export function saveTheme(theme) {
  localStorage.setItem(THEME_KEY, theme);
}

// ─── Cookie Utilities ──────────────────────────────────────────────────────

export const CookieManager = {
  /**
   * Set a cookie
   * @param {string} name
   * @param {string} value
   * @param {{ expires?: number, path?: string, secure?: boolean, sameSite?: string }} options
   */
  set(name, value, options = {}) {
    let cookie = `${encodeURIComponent(name)}=${encodeURIComponent(value)}`;
    if (options.expires) {
      const date = new Date();
      date.setDate(date.getDate() + options.expires);
      cookie += `; expires=${date.toUTCString()}`;
    }
    cookie += `; path=${options.path || "/"}`;
    if (options.secure) cookie += "; Secure";
    if (options.sameSite) cookie += `; SameSite=${options.sameSite}`;
    document.cookie = cookie;
  },

  /**
   * Get a cookie value by name
   * @param {string} name
   * @returns {string|null}
   */
  get(name) {
    const key = encodeURIComponent(name) + "=";
    const parts = document.cookie.split("; ");
    for (const part of parts) {
      if (part.startsWith(key)) {
        return decodeURIComponent(part.slice(key.length));
      }
    }
    return null;
  },

  /**
   * Get all cookies as an object
   * @returns {Record<string, string>}
   */
  getAll() {
    const result = {};
    for (const part of document.cookie.split("; ")) {
      if (!part) continue;
      const idx = part.indexOf("=");
      if (idx === -1) continue;
      const key = decodeURIComponent(part.slice(0, idx));
      const val = decodeURIComponent(part.slice(idx + 1));
      result[key] = val;
    }
    return result;
  },

  /**
   * Delete a cookie
   * @param {string} name
   */
  delete(name) {
    document.cookie = `${encodeURIComponent(name)}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  },

  /**
   * Check if a cookie exists
   * @param {string} name
   * @returns {boolean}
   */
  has(name) {
    return this.get(name) !== null;
  },
};

// ─── Last-Search Cookie (expires 7 days) ───────────────────────────────────

const LAST_SEARCH_COOKIE = "atmosphera_last_city";

export function saveLastSearchCookie(city) {
  CookieManager.set(LAST_SEARCH_COOKIE, city, { expires: 7, sameSite: "Lax" });
}

export function loadLastSearchCookie() {
  return CookieManager.get(LAST_SEARCH_COOKIE);
}
