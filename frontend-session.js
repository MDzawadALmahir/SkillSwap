function getCurrentUser() {
  try {
    const u = JSON.parse(localStorage.getItem("skillswap_current_user") || "null");
    if (u && u.id != null) u.id = Number(u.id);
    return u;
  } catch {
    return null;
  }
}

function setCurrentUser(user) {
  if (user) {
    localStorage.setItem("skillswap_current_user", JSON.stringify(user));
  } else {
    localStorage.removeItem("skillswap_current_user");
  }
}

function logout() {
  localStorage.removeItem("skillswap_current_user");
  window.location.href = "login.html";
}

function logoutAdmin() {
  localStorage.removeItem("skillswap_current_user");
  window.location.href = "admin_login.html";
}

/** Redirect if not logged in as a normal member (not admin). */
function requireMember() {
  const u = getCurrentUser();
  if (!u) {
    window.location.href = "login.html";
    return null;
  }
  if (u.role === "admin") {
    window.location.href = "admin_overview.html";
    return null;
  }
  return u;
}

/** Redirect if not logged in as admin. */
function requireAdmin() {
  const u = getCurrentUser();
  if (!u) {
    window.location.href = "admin_login.html";
    return null;
  }
  if (u.role !== "admin") {
    window.location.href = "login.html";
    return null;
  }
  return u;
}

function renderMemberNavChip(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const u = getCurrentUser();
  if (!u) return;
  const rel = Math.round(Number(u.reliability_score) || 0);
  el.innerHTML = `
    <span style="font-weight:600;">${escapeHtml(u.name)}</span>
    <span class="pill" style="margin-left:8px;">${u.credits} Credits</span>
    <span class="pill" style="margin-left:6px;background:#1a195d;color:#fff;">${rel}% Reliable</span>
  `;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}
