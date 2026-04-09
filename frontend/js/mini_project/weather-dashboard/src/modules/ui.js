/**
 * ui.js — All DOM manipulation, rendering, state display
 * No innerHTML used for user data (per assignment constraints)
 */

import { getWeatherInfo } from "./api.js";
import { isFavorite } from "./storage.js";

const currentSection = document.getElementById("current-section");

// ─── Loading State ─────────────────────────────────────────────────────────

export function showLoading() {
  currentSection.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "spinner-wrap";

  const spinner = document.createElement("div");
  spinner.className = "spinner";
  spinner.setAttribute("role", "status");
  spinner.setAttribute("aria-label", "Loading weather data");

  const text = document.createElement("p");
  text.className = "spinner-text";
  text.textContent = "Fetching weather…";

  wrap.appendChild(spinner);
  wrap.appendChild(text);
  currentSection.appendChild(wrap);
}

// ─── Error State ───────────────────────────────────────────────────────────

export function showError(message, onRetry) {
  currentSection.innerHTML = "";
  const card = document.createElement("div");
  card.className = "error-card";

  const icon = document.createElement("div");
  icon.className = "error-icon";
  icon.textContent = "😕";

  const msg = document.createElement("p");
  msg.className = "error-message";
  msg.textContent = message;

  const retryBtn = document.createElement("button");
  retryBtn.className = "retry-btn";
  retryBtn.textContent = "Try Again";
  retryBtn.setAttribute("aria-label", "Retry search");
  if (onRetry) retryBtn.addEventListener("click", onRetry);

  card.appendChild(icon);
  card.appendChild(msg);
  card.appendChild(retryBtn);
  currentSection.appendChild(card);
}

// ─── Weather Card ──────────────────────────────────────────────────────────

/**
 * Render the current weather card
 * @param {object} data — weather result from api.searchCity
 * @param {Function} onToggleFavorite
 */
export function renderWeatherCard(data, onToggleFavorite) {
  currentSection.innerHTML = "";

  const { city, country, temp, feelsLike, humidity, windSpeed, weatherCode } = data;
  const { icon: weatherIcon, label: weatherLabel } = getWeatherInfo(weatherCode);
  const favorited = isFavorite(city);

  const card = document.createElement("div");
  card.className = "weather-card";
  card.setAttribute("role", "region");
  card.setAttribute("aria-label", `Weather for ${city}`);

  // Top Row
  const cardTop = document.createElement("div");
  cardTop.className = "card-top";

  const cityInfo = document.createElement("div");

  const cityNameEl = document.createElement("h2");
  cityNameEl.className = "city-name";
  cityNameEl.textContent = city;

  const countryEl = document.createElement("p");
  countryEl.className = "country-code";
  countryEl.textContent = country;

  cityInfo.appendChild(cityNameEl);
  cityInfo.appendChild(countryEl);

  const starBtn = document.createElement("button");
  starBtn.className = `fav-star-btn${favorited ? " active" : ""}`;
  starBtn.textContent = favorited ? "★" : "☆";
  starBtn.setAttribute("aria-label", favorited ? "Remove from favorites" : "Add to favorites");
  starBtn.setAttribute("aria-pressed", String(favorited));
  starBtn.addEventListener("click", () => onToggleFavorite(data, starBtn));

  cardTop.appendChild(cityInfo);
  cardTop.appendChild(starBtn);

  // Main weather display
  const weatherMain = document.createElement("div");
  weatherMain.className = "weather-main";

  const iconEl = document.createElement("div");
  iconEl.className = "weather-icon";
  iconEl.setAttribute("aria-hidden", "true");
  iconEl.textContent = weatherIcon;

  const tempWrap = document.createElement("div");

  const tempEl = document.createElement("div");
  tempEl.className = "temp-display";
  tempEl.setAttribute("aria-label", `${temp} degrees Celsius`);
  tempEl.textContent = `${temp}`;

  const unitEl = document.createElement("span");
  unitEl.className = "temp-unit";
  unitEl.setAttribute("aria-hidden", "true");
  unitEl.textContent = "°C";
  tempEl.appendChild(unitEl);

  const conditionEl = document.createElement("p");
  conditionEl.className = "condition-text";
  conditionEl.textContent = weatherLabel;

  tempWrap.appendChild(tempEl);
  tempWrap.appendChild(conditionEl);

  weatherMain.appendChild(iconEl);
  weatherMain.appendChild(tempWrap);

  // Meta grid
  const meta = document.createElement("div");
  meta.className = "weather-meta";

  const metaItems = [
    { label: "Feels Like", value: `${feelsLike}°C`, aria: `Feels like ${feelsLike} degrees` },
    { label: "Humidity", value: `${humidity}%`, aria: `Humidity ${humidity} percent` },
    { label: "Wind", value: `${windSpeed} km/h`, aria: `Wind speed ${windSpeed} kilometres per hour` },
  ];

  for (const item of metaItems) {
    const metaItem = document.createElement("div");
    metaItem.className = "meta-item";
    metaItem.setAttribute("aria-label", item.aria);

    const label = document.createElement("div");
    label.className = "meta-label";
    label.setAttribute("aria-hidden", "true");
    label.textContent = item.label;

    const value = document.createElement("div");
    value.className = "meta-value";
    value.textContent = item.value;

    metaItem.appendChild(label);
    metaItem.appendChild(value);
    meta.appendChild(metaItem);
  }

  card.appendChild(cardTop);
  card.appendChild(weatherMain);
  card.appendChild(meta);
  currentSection.appendChild(card);
}

// ─── Favorites List ────────────────────────────────────────────────────────

/**
 * Render the favorites sidebar list
 * @param {Array} favorites
 * @param {Function} onSelect — called with cityName when user clicks
 * @param {Function} onDelete — called with cityName when user deletes
 */
export function renderFavorites(favorites, onSelect, onDelete) {
  const list = document.getElementById("favorites-list");
  const countEl = document.getElementById("fav-count");

  // Clear with DocumentFragment for efficient re-render
  const fragment = document.createDocumentFragment();

  if (favorites.length === 0) {
    const emptyEl = document.createElement("li");
    emptyEl.id = "fav-empty";
    emptyEl.className = "fav-empty";
    emptyEl.textContent = "No favorites yet. Star a city to save it!";
    fragment.appendChild(emptyEl);
  } else {
    for (const fav of favorites) {
      const li = document.createElement("li");
      li.className = "fav-item";
      li.setAttribute("role", "button");
      li.setAttribute("tabindex", "0");
      li.setAttribute("aria-label", `${fav.city}, ${fav.country}. Click to view weather.`);

      const info = document.createElement("div");
      info.className = "fav-item-info";

      const iconEl = document.createElement("span");
      iconEl.className = "fav-item-icon";
      iconEl.setAttribute("aria-hidden", "true");
      iconEl.textContent = "📍";

      const textWrap = document.createElement("div");

      const nameEl = document.createElement("div");
      nameEl.className = "fav-item-name";
      nameEl.textContent = fav.city;

      const countryEl = document.createElement("div");
      countryEl.className = "fav-item-country";
      countryEl.textContent = fav.country;

      textWrap.appendChild(nameEl);
      textWrap.appendChild(countryEl);
      info.appendChild(iconEl);
      info.appendChild(textWrap);

      const delBtn = document.createElement("button");
      delBtn.className = "fav-delete-btn";
      delBtn.textContent = "✕";
      delBtn.setAttribute("aria-label", `Remove ${fav.city} from favorites`);
      delBtn.addEventListener("click", (e) => {
        e.stopPropagation(); // prevent bubbling to li click
        onDelete(fav.city);
      });

      // Click on row to search city
      li.addEventListener("click", () => onSelect(fav.city));
      li.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(fav.city);
        }
      });

      li.appendChild(info);
      li.appendChild(delBtn);
      fragment.appendChild(li);
    }
  }

  // Efficient single DOM update
  list.innerHTML = "";
  list.appendChild(fragment);

  // Update count badge
  countEl.textContent = `${favorites.length} ${favorites.length === 1 ? "city" : "cities"}`;
}

// ─── Update favorite star button ───────────────────────────────────────────

export function updateStarButton(starBtn, isFav) {
  starBtn.textContent = isFav ? "★" : "☆";
  starBtn.className = `fav-star-btn${isFav ? " active" : ""}`;
  starBtn.setAttribute("aria-label", isFav ? "Remove from favorites" : "Add to favorites");
  starBtn.setAttribute("aria-pressed", String(isFav));
}
