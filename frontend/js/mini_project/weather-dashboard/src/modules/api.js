/**
 * api.js — Weather API Service
 * Uses Open-Meteo (free, no API key) + Geocoding API
 */

const GEO_BASE = "https://geocoding-api.open-meteo.com/v1";
const WEATHER_BASE = "https://api.open-meteo.com/v1";

// AbortController for cancelling in-flight requests
let currentController = null;

/**
 * Geocode a city name → { name, country, lat, lon }
 * @param {string} city
 * @param {AbortSignal} [signal]
 * @returns {Promise<{name:string, country:string, latitude:number, longitude:number}>}
 */
export async function geocodeCity(city, signal) {
  const url = `${GEO_BASE}/search?name=${encodeURIComponent(city)}&count=1&language=en&format=json`;

  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Geocoding failed (${response.status})`);

  const data = await response.json();
  if (!data.results || data.results.length === 0) {
    throw new Error(`City "${city}" not found`);
  }

  const { name, country, latitude, longitude } = data.results[0];
  return { name, country, latitude, longitude };
}

/**
 * Fetch current weather for lat/lon
 * @param {number} lat
 * @param {number} lon
 * @param {AbortSignal} [signal]
 * @returns {Promise<WeatherData>}
 */
export async function fetchWeather(lat, lon, signal) {
  const params = new URLSearchParams({
    latitude: lat,
    longitude: lon,
    current: [
      "temperature_2m",
      "relative_humidity_2m",
      "wind_speed_10m",
      "weather_code",
      "apparent_temperature",
    ].join(","),
    wind_speed_unit: "kmh",
    temperature_unit: "celsius",
    timezone: "auto",
  });

  const url = `${WEATHER_BASE}/forecast?${params}`;
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Weather fetch failed (${response.status})`);

  const data = await response.json();
  return data.current;
}

/**
 * Main search: geocode + weather, cancels previous request
 * @param {string} city
 * @returns {Promise<{city:string, country:string, lat:number, lon:number, temp:number, feelsLike:number, humidity:number, windSpeed:number, weatherCode:number}>}
 */
export async function searchCity(city) {
  // Cancel any in-flight request
  if (currentController) currentController.abort();
  currentController = new AbortController();
  const { signal } = currentController;

  try {
    const geo = await geocodeCity(city, signal);
    const weather = await fetchWeather(geo.latitude, geo.longitude, signal);

    return {
      city: geo.name,
      country: geo.country,
      lat: geo.latitude,
      lon: geo.longitude,
      temp: Math.round(weather.temperature_2m),
      feelsLike: Math.round(weather.apparent_temperature),
      humidity: weather.relative_humidity_2m,
      windSpeed: Math.round(weather.wind_speed_10m),
      weatherCode: weather.weather_code,
    };
  } catch (err) {
    if (err.name === "AbortError") throw new Error("Request cancelled");
    throw err;
  }
}

/**
 * Map WMO weather code → emoji icon + label
 * https://open-meteo.com/en/docs/wmo-weather-interpretation-codes
 */
export function getWeatherInfo(code) {
  if (code === 0) return { icon: "☀️", label: "Clear sky" };
  if (code <= 2) return { icon: "🌤️", label: "Partly cloudy" };
  if (code === 3) return { icon: "☁️", label: "Overcast" };
  if (code <= 49) return { icon: "🌫️", label: "Foggy" };
  if (code <= 59) return { icon: "🌦️", label: "Drizzle" };
  if (code <= 69) return { icon: "🌧️", label: "Rain" };
  if (code <= 79) return { icon: "🌨️", label: "Snow" };
  if (code <= 82) return { icon: "⛈️", label: "Rain showers" };
  if (code <= 86) return { icon: "🌨️", label: "Snow showers" };
  if (code <= 99) return { icon: "⛈️", label: "Thunderstorm" };
  return { icon: "🌡️", label: "Unknown" };
}
