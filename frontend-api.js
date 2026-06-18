const API_BASE_URL = localStorage.getItem("skillswap_api_base") || "http://127.0.0.1:5000";

function apiDownloadUrl(path) {
  return `${API_BASE_URL}${path}`;
}

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "Request failed.");
  }
  return data;
}
