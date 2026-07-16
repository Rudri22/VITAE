(function () {
  const TOKEN_KEY = "vitaeAuthToken";
  const USER_KEY = "vitaeAuthUser";
  const ROLE_KEY = "vitaeAuthRole";

  function normalizeRole(role) {
    return String(role || "").trim().toLowerCase() === "hospital"
      ? "organization_user"
      : String(role || "").trim().toLowerCase();
  }

  function routeForRole(role) {
    return {
      admin: "/admin",
      organization_user: "/organization",
      driver: "/driver",
      support: "/support",
    }[normalizeRole(role)] || "/403";
  }

  function saveSession(token, user) {
    const normalized = { ...user, role: normalizeRole(user.normalizedRole || user.role), isAuthenticated: true };
    sessionStorage.setItem(TOKEN_KEY, token);
    sessionStorage.setItem(USER_KEY, JSON.stringify(normalized));
    sessionStorage.setItem(ROLE_KEY, normalized.role);
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(ROLE_KEY);
    document.cookie = `vitae_token=${encodeURIComponent(token)}; Path=/; SameSite=Lax`;
    return normalized;
  }

  function session() {
    const token = sessionStorage.getItem(TOKEN_KEY);
    if (!token) return null;
    try {
      const user = JSON.parse(sessionStorage.getItem(USER_KEY) || "null");
      if (!user?.username || !user?.role) return null;
      return { token, user: { ...user, role: normalizeRole(user.role), isAuthenticated: true } };
    } catch (_error) {
      clearSession();
      return null;
    }
  }

  function clearSession() {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(USER_KEY);
    sessionStorage.removeItem(ROLE_KEY);
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(ROLE_KEY);
    document.cookie = "vitae_token=; Max-Age=0; Path=/; SameSite=Lax";
  }

  async function api(url, options = {}) {
    const current = session();
    const headers = { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(current ? { Authorization: `Bearer ${current.token}` } : {}), ...(options.headers || {}) };
    const base = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";
    let response;
    try {
      response = await fetch(`${base}${url}`, { ...options, headers, credentials: "same-origin" });
    } catch (_error) {
      throw new Error("VITAE could not reach the server. Check your connection and try again.");
    }
    let payload = {};
    try {
      payload = await response.json();
    } catch (_error) {
      if (!response.ok) throw new Error(`VITAE received an invalid server response (${response.status}).`);
    }
    if (!response.ok) {
      if (response.status === 401 && current) {
        clearSession();
        window.dispatchEvent(new CustomEvent("vitae:session-expired"));
      }
      const error = new Error(payload.error || "Request failed");
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  async function login(username, password) {
    const payload = await api("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
    return saveSession(payload.token, payload.user);
  }

  async function verify() {
    const current = session();
    if (!current) return null;
    try {
      const payload = await api("/api/me");
      return saveSession(current.token, payload.user);
    } catch (_error) {
      clearSession();
      return null;
    }
  }

  window.VitaeAuth = { api, clearSession, login, normalizeRole, routeForRole, session, verify };
})();
