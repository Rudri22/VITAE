const TOKEN_KEY = "vitaeAuthToken";
const USER_KEY = "vitaeAuthUser";
const ROLE_KEY = "vitaeAuthRole";

const ADMIN_MODULES = [
  ["hospital-management", "🏥", "Hospital Management", "Manage hospital records, details, and operational status."],
  ["user-management", "👥", "User Management", "Create users, assign roles, reset access, and disable accounts."],
  ["supply-monitoring", "📦", "Supply Monitoring", "Open inventory tools, low stock alerts, surplus, and search."],
  ["emergency-center", "🚨", "Emergency Center", "Coordinate active emergencies and crisis support."],
  ["analytics-reports", "📈", "Analytics & Reports", "View statistics, trends, and export reports."],
  ["transfers", "🔄", "Transfers", "Review incoming, outgoing, approval, and tracking workflows."],
  ["alerts", "🔔", "Alerts", "Inspect critical alerts, expiry, cold chain, and offline status."],
  ["system-settings", "⚙️", "System Settings", "Configure categories, permissions, notifications, and platform settings."],
  ["audit-logs", "📜", "Audit Logs", "Review activity, login history, changes, and admin actions."],
  ["live-monitoring-map", "🗺️", "Live Monitoring Map", "Monitor hospital status and open hospital monitoring."],
];

const DEMO_ACCOUNTS = {
  admin: {
    password: "admin123",
    token: "admin-token",
    user: { username: "admin", role: "admin", displayName: "Admin", isAuthenticated: true },
  },
  "admin@vitae.local": {
    password: "admin123",
    token: "admin-token",
    user: { username: "admin", role: "admin", displayName: "Admin", isAuthenticated: true },
  },
  support: {
    password: "support123",
    token: "support-token",
    user: { username: "support", role: "support", displayName: "Support", isAuthenticated: true },
  },
  "support@vitae.local": {
    password: "support123",
    token: "support-token",
    user: { username: "support", role: "support", displayName: "Support", isAuthenticated: true },
  },
  hospitala: {
    password: "hospitalA123",
    token: "hospital-a-token",
    user: { username: "hospitalA", role: "hospital", hospitalId: "hospital-a", hospitalName: "Hospital A", displayName: "Hospital A", isAuthenticated: true },
  },
  "hospital-a": {
    password: "hospitalA123",
    token: "hospital-a-token",
    user: { username: "hospitalA", role: "hospital", hospitalId: "hospital-a", hospitalName: "Hospital A", displayName: "Hospital A", isAuthenticated: true },
  },
  "hospital-a@vitae.local": {
    password: "hospitalA123",
    token: "hospital-a-token",
    user: { username: "hospitalA", role: "hospital", hospitalId: "hospital-a", hospitalName: "Hospital A", displayName: "Hospital A", isAuthenticated: true },
  },
  hospitalb: {
    password: "hospitalB123",
    token: "hospital-b-token",
    user: { username: "hospitalB", role: "hospital", hospitalId: "hospital-b", hospitalName: "Hospital B", displayName: "Hospital B", isAuthenticated: true },
  },
  "hospital-b": {
    password: "hospitalB123",
    token: "hospital-b-token",
    user: { username: "hospitalB", role: "hospital", hospitalId: "hospital-b", hospitalName: "Hospital B", displayName: "Hospital B", isAuthenticated: true },
  },
  "hospital-b@vitae.local": {
    password: "hospitalB123",
    token: "hospital-b-token",
    user: { username: "hospitalB", role: "hospital", hospitalId: "hospital-b", hospitalName: "Hospital B", displayName: "Hospital B", isAuthenticated: true },
  },
};

let currentUser = null;
let adminDashboard = null;
let supportDashboard = null;
let hospitalDashboard = null;
let supportPage = "dashboard";
let activeSupportCase = null;
let activeHospitalShipment = null;
let liveShipmentState = { status: "idle", shipments: [], alerts: [] };
let liveShipmentTimer = null;
let activeHospitalModule = "dashboard";

document.addEventListener("DOMContentLoaded", () => {
  try {
    setupClock();
    setInterval(setupClock, 30000);
    bindGlobalActions();
    boot();
  } catch (error) {
    console.error("Startup failed:", error);
    currentUser = null;
    clearToken();
    showLogin("Something went wrong starting the app. Please sign in again.");
  }
});

function bindGlobalActions() {
  document.getElementById("loginForm")?.addEventListener("submit", handleLoginSubmit);
  document.querySelectorAll("[data-support-page]").forEach((button) => {
    button.addEventListener("click", () => openSupportPage(button.dataset.supportPage));
  });
  document.getElementById("supportContent")?.addEventListener("click", handleSupportContentClick);
  document.getElementById("hospitalContent")?.addEventListener("click", handleHospitalContentClick);
  document.querySelectorAll("[data-logout]").forEach((button) => button.addEventListener("click", logout));
  document.getElementById("goOwnDashboardButton")?.addEventListener("click", () => redirectToRole(currentUser));
  document.getElementById("adminHomeButton")?.addEventListener("click", showAdminHome);
  document.getElementById("adminBackButton")?.addEventListener("click", showAdminHome);
  document.querySelectorAll("[data-hospital-module]").forEach((button) => {
    button.addEventListener("click", () => renderHospitalModule(button.dataset.hospitalModule));
  });
}

async function boot() {
  currentUser = getStoredUser();
  const path = window.location.pathname;

  console.log("Current pathname:", path);
  console.log("Authenticated user:", currentUser);

  if (!isAuthenticatedUser(currentUser)) {
    clearToken();
    showLogin();
    if (window.location.protocol !== "file:" && path !== "/login") {
      window.history.replaceState({}, "", "/login");
    }
    return;
  }

  currentUser.role = normalizeRole(currentUser.role);
  currentUser.isAuthenticated = true;
  const targetPath = getRolePath(currentUser.role);

  console.log("Authenticated user:", currentUser);
  console.log("Redirecting to:", targetPath);

  if (path === "/" || path === "/login") {
    redirectToRole(currentUser, true);
    return;
  }

  if (!isAllowedRoute(path, currentUser)) {
    redirectToRole(currentUser, true);
    return;
  }

  try {
    await openRoleDashboard(currentUser);
  } catch (error) {
    console.error("Dashboard failed to load:", error);
    showDashboardLoadError(error);
  }
}

function openDemoAccount(accountKey) {
  const account = DEMO_ACCOUNTS[accountKey];
  if (!account) return;

  const user = {
    ...account.user,
    role: normalizeRole(account.user.role),
  };
  const targetPath = getRolePath(user.role);

  console.log("Demo account:", user);
  console.log("Redirecting to:", targetPath);

  saveAuthSession(account.token, user);
  currentUser = user;

  if (window.location.protocol === "file:") {
    if (user.role === "admin") loadAdmin();
    if (user.role === "support") loadSupport();
    if (user.role === "hospital") loadHospital();
    return;
  }

  if (window.location.pathname === targetPath) {
    if (user.role === "admin") loadAdmin();
    if (user.role === "support") loadSupport();
    if (user.role === "hospital") loadHospital();
    return;
  }

  window.location.href = targetPath;
}

function handleLoginSubmit(event) {
  event.preventDefault();

  const form = event.currentTarget;
  const username = String(form.elements.username.value || "").trim().toLowerCase();
  const password = String(form.elements.password.value || "");
  const error = document.getElementById("loginError");
  const button = document.getElementById("loginButton");
  const account = DEMO_ACCOUNTS[username];

  error.hidden = true;

  if (!account || account.password !== password) {
    console.log("Login success:", false);
    error.textContent = "Invalid username or password.";
    error.hidden = false;
    return;
  }

  const user = {
    ...account.user,
    role: normalizeRole(account.user.role),
    isAuthenticated: true,
  };
  const targetPath = getRolePath(user.role);

  console.log("Login success:", user);
  console.log("Redirecting to:", targetPath);

  button.textContent = "Opening...";
  button.disabled = true;
  saveAuthSession(account.token, user);
  currentUser = user;

  if (window.location.protocol === "file:") {
    openRoleDashboard(user);
    button.textContent = "Sign In";
    button.disabled = false;
    return;
  }

  window.location.replace(targetPath);
}

async function openRoleDashboard(user, replaceUrl = false) {
  const targetPath = getRolePath(user?.role);
  if (replaceUrl && window.location.protocol !== "file:" && window.location.pathname !== targetPath) {
    window.history.replaceState({}, "", targetPath);
  }

  if (user.role === "admin") {
    await loadAdmin();
    return;
  }

  if (user.role === "support") {
    await loadSupport();
    return;
  }

  if (user.role === "hospital") {
    await loadHospital();
    return;
  }

  showDenied();
}

async function loadLiveShipmentState() {
  if (!["support", "hospital"].includes(currentUser?.role)) return;
  try {
    const payload = await window.shipmentApi.fetchLiveShipments();
    liveShipmentState = {
      status: "ready",
      shipments: payload.shipments || [],
      alerts: payload.alerts || [],
      source: payload.source,
      lastFetchedAt: new Date().toISOString(),
    };
  } catch (error) {
    console.error("Live shipments failed:", error);
    liveShipmentState = {
      status: "error",
      shipments: [],
      alerts: [],
      error: error.message || "Unable to load live shipments.",
      lastFetchedAt: new Date().toISOString(),
    };
  }
}

function startLiveShipmentPolling() {
  stopLiveShipmentPolling();
  if (!["support", "hospital"].includes(currentUser?.role)) return;
  liveShipmentTimer = window.setInterval(refreshLiveShipmentSection, 5000);
}

function stopLiveShipmentPolling() {
  if (liveShipmentTimer) {
    window.clearInterval(liveShipmentTimer);
    liveShipmentTimer = null;
  }
}

async function refreshLiveShipmentSection() {
  if (!["support", "hospital"].includes(currentUser?.role)) {
    stopLiveShipmentPolling();
    return;
  }
  await loadLiveShipmentState();
  if (currentUser.role === "support" && supportPage === "shipments" && supportDashboard) {
    renderSupportHome();
  }
  if (currentUser.role === "hospital" && ["dashboard", "shipments"].includes(activeHospitalModule) && hospitalDashboard) {
    renderHospitalModule(activeHospitalModule);
  }
}

function openLiveShipmentMap(index) {
  const shipment = (liveShipmentState.shipments || [])[Number(index)];
  if (!shipment) return;
  closeLiveShipmentMap();

  const modal = document.createElement("div");
  modal.className = "map-modal";
  modal.innerHTML = liveShipmentMapModalMarkup(shipment);
  modal.addEventListener("click", (event) => {
    if (event.target.matches("[data-close-live-map]") || event.target === modal) {
      closeLiveShipmentMap();
    }
  });
  document.body.appendChild(modal);
}

function closeLiveShipmentMap() {
  document.querySelector(".map-modal")?.remove();
}

function liveShipmentMapModalMarkup(shipment) {
  const current = liveGpsParam(shipment.latitude, shipment.longitude);
  const destination = liveGpsParam(shipment.destinationLatitude, shipment.destinationLongitude);
  const hasCurrent = Boolean(current);
  const hasDestination = Boolean(destination);
  const mapUrl = hasCurrent && hasDestination
    ? `https://maps.google.com/maps?saddr=${encodeURIComponent(current)}&daddr=${encodeURIComponent(destination)}&z=12&output=embed`
    : hasCurrent
      ? `https://maps.google.com/maps?q=${encodeURIComponent(current)}&z=13&output=embed`
      : "";
  const externalUrl = hasCurrent && hasDestination
    ? `https://www.google.com/maps/dir/?api=1&origin=${encodeURIComponent(current)}&destination=${encodeURIComponent(destination)}`
    : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(current || shipment.currentLocation || "")}`;

  return `
    <section class="map-modal-card" role="dialog" aria-modal="true" aria-label="Shipment route map">
      <header>
        <div>
          <span>Shipment Route</span>
          <h2>${escapeHtml(shipment.shipmentId || "Shipment")}</h2>
          <p>${escapeHtml(shipment.currentLocation || "Current location")} → ${escapeHtml(shipment.hospitalName || "Destination")}</p>
        </div>
        <button data-close-live-map type="button" aria-label="Close map">X</button>
      </header>
      ${mapUrl ? `<iframe title="Route map for ${escapeHtml(shipment.shipmentId || "shipment")}" loading="lazy" src="${escapeHtml(mapUrl)}"></iframe>` : emptyMarkup("Live coordinates unavailable.")}
      <footer>
        <div><span>Current</span><strong>${escapeHtml(current || "Unavailable")}</strong></div>
        <div><span>Destination</span><strong>${escapeHtml(destination || shipment.hospitalName || "Unavailable")}</strong></div>
        <a href="${escapeHtml(externalUrl)}" target="_blank" rel="noopener">Open in Google Maps</a>
      </footer>
    </section>
  `;
}

function liveGpsParam(lat, lng) {
  if (typeof lat !== "number" || typeof lng !== "number") return "";
  return `${lat},${lng}`;
}

async function loadAdmin() {
  stopLiveShipmentPolling();
  showOnly("adminView");
  updateLoggedInLabels();
  adminDashboard = await fetchJson("/api/admin/dashboard");
  renderAdminHome();
}

async function loadSupport() {
  showOnly("supportView");
  updateLoggedInLabels();
  supportDashboard = await fetchJson("/api/support/dashboard");
  liveShipmentState = { status: "loading", shipments: [], alerts: [] };
  renderSupportHome();
  await loadLiveShipmentState();
  renderSupportHome();
  startLiveShipmentPolling();
}

async function loadHospital() {
  showOnly("hospitalView");
  hospitalDashboard = await fetchJson("/api/hospital/dashboard");
  liveShipmentState = { status: "loading", shipments: [], alerts: [] };
  renderHospitalHome();
  renderHospitalModule("inventory");
  await loadLiveShipmentState();
  renderHospitalModule("inventory");
  startLiveShipmentPolling();
}

function renderAdminHome() {
  const overview = adminDashboard.overview || {};
  document.getElementById("hospitalsOnline").textContent = overview.activeHospitalsToday || 0;
  document.getElementById("activeEmergencies").textContent = overview.emergencyIncidents || 0;
  document.getElementById("pendingRequests").textContent = overview.activeRequests || 0;
  document.getElementById("activeTransfers").textContent = overview.activeTransfers || 0;

  document.getElementById("adminModules").innerHTML = ADMIN_MODULES.map(([id, icon, title, description]) => `
    <article class="module-card" data-module="${id}">
      <div class="module-icon">${icon}</div>
      <div><h2>${escapeHtml(title)}</h2><p>${escapeHtml(description)}</p></div>
      <button type="button">${id.includes("management") || id === "transfers" || id === "system-settings" ? "Manage" : "Open"}</button>
    </article>
  `).join("");
  document.querySelectorAll("[data-module]").forEach((card) => {
    card.addEventListener("click", () => openAdminModule(card.dataset.module));
  });
}

function openAdminModule(moduleId) {
  const module = ADMIN_MODULES.find(([id]) => id === moduleId);
  if (!module) return;
  document.getElementById("adminHomeView").hidden = true;
  document.getElementById("adminModuleView").hidden = false;
  document.getElementById("adminModuleTitle").textContent = module[2];
  document.getElementById("adminModuleDescription").textContent = module[3];
  document.getElementById("adminModuleContent").innerHTML = renderAdminModule(moduleId);
  if (moduleId === "live-monitoring-map") renderMap("adminModuleContent", adminDashboard.hospitalMap || []);
}

function showAdminHome() {
  document.getElementById("adminModuleView").hidden = true;
  document.getElementById("adminHomeView").hidden = false;
}

function renderAdminModule(moduleId) {
  const data = adminDashboard.moduleData || {};
  if (moduleId === "live-monitoring-map") return `<div id="adminMap" class="map-canvas large-map"></div>`;
  if (moduleId === "hospital-management") return opsList(data.hospitals || [], (h) => [h.name, `${h.region || "Region"} · ${h.activeRequests || 0} active request(s)`, h.status]);
  if (moduleId === "user-management") return commandTiles(["Create users", "Assign roles", "Reset passwords", "Disable accounts"]) + opsList(data.users || [], (u) => [u.name, `${u.username} · ${u.role}`, "Manage"]);
  if (moduleId === "supply-monitoring") return commandTiles(["Search inventory", "Low stock alerts", "Surplus supplies", "View statistics"]) + opsList(data.inventory || [], (i) => [i.category, `${i.items} item(s), ${i.lowStock} low stock, ${i.shared} shared`, "Open"]);
  if (moduleId === "emergency-center") return commandTiles(["Active emergencies", "Emergency requests", "Crisis coordination", "Dispatch support"]) + opsList((data.requests || []).filter((r) => r.urgency === "critical"), (r) => [r.itemName, `${r.organization?.name || "Hospital"} · ${r.publicNote || "No public note"}`, r.urgency]);
  if (moduleId === "analytics-reports") return commandTiles(["Charts", "Statistics", "Trends", "Export PDF/Excel"]);
  if (moduleId === "transfers") return commandTiles(["Incoming transfers", "Outgoing transfers", "Approvals", "Tracking"]) + opsList(data.transfers || [], (t) => [t.transferId, `${t.route} · ${formatEta(t.etaMinutes)}`, t.status]);
  if (moduleId === "alerts") return commandTiles(["Critical alerts", "Expiring medicine", "Cold chain failures", "Hospital offline"]) + opsList(data.alerts || [], (a) => [a.shipmentId || a.type, `${a.message || "Alert"} · ${a.destination || "Network"}`, a.severity || "alert"]);
  if (moduleId === "system-settings") return commandTiles(["Categories", "Permissions", "Notifications", "Platform settings"]);
  if (moduleId === "audit-logs") return commandTiles(["User activity", "Login history", "System changes", "Admin actions"]) + opsList(data.auditLogs || [], (a) => [a.label, a.detail, a.timestamp || ""]);
  return emptyMarkup("Module not available.");
}

function renderSupportHome() {
  updateSupportNav();
  const content = document.getElementById("supportContent");
  if (supportPage === "cases") content.innerHTML = supportCasesPage();
  else if (supportPage === "case-details") content.innerHTML = supportCaseDetailsPage(activeSupportCase);
  else if (supportPage === "transfers") content.innerHTML = supportTransfersPage();
  else if (supportPage === "shipments") content.innerHTML = supportShipmentsPage();
  else if (supportPage === "shipment-details") content.innerHTML = supportShipmentDetailsPage(activeSupportCase);
  else if (supportPage === "shared") content.innerHTML = supportSharedRequestsPage();
  else if (supportPage === "alerts") content.innerHTML = supportAlertsPage();
  else if (supportPage === "reports") content.innerHTML = supportReportsPage();
  else content.innerHTML = supportDashboardPage();
}

function openSupportPage(page) {
  supportPage = page || "dashboard";
  activeSupportCase = null;
  if (supportDashboard) renderSupportHome();
}

function handleSupportContentClick(event) {
  const liveMapButton = event.target.closest("[data-live-map-index]");
  if (liveMapButton) {
    openLiveShipmentMap(liveMapButton.dataset.liveMapIndex);
    return;
  }

  const pageButton = event.target.closest("[data-support-page]");
  if (pageButton) {
    openSupportPage(pageButton.dataset.supportPage);
    return;
  }

  const caseButton = event.target.closest("[data-open-case]");
  if (caseButton) {
    activeSupportCase = caseButton.dataset.openCase;
    supportPage = "case-details";
    renderSupportHome();
    return;
  }

  const shipmentButton = event.target.closest("[data-open-shipment]");
  if (shipmentButton) {
    activeSupportCase = shipmentButton.dataset.openShipment;
    supportPage = "shipment-details";
    renderSupportHome();
  }
}

function handleHospitalContentClick(event) {
  const liveMapButton = event.target.closest("[data-live-map-index]");
  if (liveMapButton) {
    openLiveShipmentMap(liveMapButton.dataset.liveMapIndex);
    return;
  }

  const navLink = event.target.closest("[data-hospital-module]");
  if (navLink) {
    renderHospitalModule(navLink.dataset.hospitalModule);
    return;
  }

  const shipmentButton = event.target.closest("[data-open-shipment]");
  if (shipmentButton) {
    activeHospitalShipment = shipmentButton.dataset.openShipment;
    renderHospitalModule("shipment-details");
  }
}

function updateSupportNav() {
  document.querySelectorAll("[data-support-page]").forEach((button) => {
    button.classList.toggle("active", button.dataset.supportPage === supportPage);
  });
}

function supportDashboardPage() {
  const overview = supportDashboard.overview || {};
  const cases = getSupportCases().slice(0, 6);
  const alerts = (supportDashboard.alerts || []).slice(0, 3);
  return `
    <section class="ops-priorities">
      ${priorityCard("Critical Cases", overview.urgentRequests || 0, "cases")}
      ${priorityCard("Pending Cases", getSupportCases().length, "cases")}
      ${priorityCard("Transfers Waiting", supportDashboard.coordinationQueue?.length || 0, "transfers")}
      ${priorityCard("Active Alerts", overview.activeAlerts || 0, "alerts")}
    </section>
    <section class="ops-panel">
      <header><h2>Active Cases</h2></header>
      ${supportCaseTable(cases)}
    </section>
    <section class="ops-panel">
      <header><h2>Recent Alerts</h2><button data-support-page="alerts" type="button">View All Alerts</button></header>
      ${supportRecentAlerts(alerts)}
    </section>
  `;
}

function priorityCard(title, value, page) {
  return `<button class="ops-priority-card" data-support-page="${page}" type="button"><span>${escapeHtml(title)}</span><strong>${value}</strong></button>`;
}

function supportCaseTable(cases) {
  if (!cases.length) return emptyMarkup("No cases need attention.");
  return `
    <div class="ops-table">
      ${cases.map((item, index) => `
        <article class="ops-case-row">
          <span class="priority-dot ${priorityClass(item.urgency)}"></span>
          <div><strong>${escapeHtml(item.hospital)}</strong><span>${escapeHtml(item.itemName)}</span></div>
          <span class="status-badge">${escapeHtml(item.status)}</span>
          <span>${escapeHtml(item.updatedAt)}</span>
          <button data-open-case="${index}" type="button">Open Case</button>
        </article>
      `).join("")}
    </div>
  `;
}

function supportRecentAlerts(alerts) {
  if (!alerts.length) return emptyMarkup("No recent alerts.");
  return `<div class="ops-alert-compact">${alerts.map((alert) => `
    <article>
      <span class="priority-dot ${priorityClass(alert.severity)}"></span>
      <div><strong>${escapeHtml(alert.type || "Alert")}</strong><span>${escapeHtml(alert.destination || "Hospital network")}</span></div>
      <button data-support-page="alerts" type="button">View</button>
    </article>
  `).join("")}</div>`;
}

function supportCasesPage() {
  return `<section class="ops-panel"><header><h2>Cases</h2></header>${supportCaseTable(getSupportCases())}</section>`;
}

function supportCaseDetailsPage(caseIndex) {
  const item = getSupportCases()[Number(caseIndex) || 0] || getSupportCases()[0];
  if (!item) return `<section class="ops-panel">${emptyMarkup("No case selected.")}</section>`;
  return `
    <section class="case-detail">
      <header>
        <button data-support-page="cases" type="button">Back</button>
        <div><h2>${escapeHtml(item.itemName)}</h2><p>${escapeHtml(item.hospital)} · ${escapeHtml(item.status)}</p></div>
      </header>
      <div class="case-detail-grid">
        <article><span>Hospital</span><strong>${escapeHtml(item.hospital)}</strong></article>
        <article><span>Requested item</span><strong>${escapeHtml(item.itemName)}</strong></article>
        <article><span>Priority</span><strong>${escapeHtml(priorityLabel(item.urgency))}</strong></article>
        <article><span>Suggested match</span><strong>${escapeHtml(item.supplier || "No match assigned")}</strong></article>
      </div>
      <section class="ops-panel"><h3>Timeline</h3><p>Pending → Matched → Approved → In Transit → Delivered → Closed</p></section>
      <section class="ops-panel"><h3>Transfer History</h3><p>${escapeHtml(item.transferHistory || "No completed transfer yet.")}</p></section>
      <section class="ops-panel"><h3>Internal Notes</h3><p>Support-only coordination notes. Private hospital inventory details are hidden.</p></section>
      <div class="case-actions">
        <button type="button">Assign Match</button>
        <button type="button">Approve Transfer</button>
        <button type="button">Escalate</button>
        <button type="button">Send Message</button>
        <button type="button">Close Case</button>
      </div>
    </section>
  `;
}

function supportTransfersPage() {
  const transfers = supportDashboard.coordinationQueue || [];
  return `
    <section class="ops-panel">
      <header><h2>Transfers</h2></header>
      <div class="ops-tabs"><button>Incoming</button><button>Outgoing</button><button>Completed</button><button>Cancelled</button></div>
      ${supportCaseTable(transfers.map((item) => supportCaseFromQueue(item)))}
    </section>
  `;
}

function supportShipmentsPage() {
  return `
    <section class="ops-panel">
      ${window.liveShipmentDashboard.render(liveShipmentState, { title: "Real-Time Shipment Monitoring" })}
    </section>
  `;
}

function supportShipmentDetailsPage(index) {
  const shipment = (supportDashboard.liveShipments || [])[Number(index) || 0];
  if (!shipment) return `<section class="ops-panel">${emptyMarkup("No shipment selected.")}</section>`;
  return `
    <section class="shipment-detail">
      <header>
        <button data-support-page="shipments" type="button">Back</button>
        <div><h2>${escapeHtml(shipment.shipmentId)}</h2><p>${escapeHtml(shipment.destinationHospitalName)} · ${shipmentStatusLabel(shipment.status)}</p></div>
      </header>
      ${shipmentDetailMarkup(shipment, true)}
    </section>
  `;
}

function supportSharedRequestsPage() {
  const records = [
    ...(supportDashboard.sharedNeeds || []).map((item) => ({ type: "Need", hospital: item.organization?.name, itemName: item.itemName, privacy: item.privacy, status: item.flowStatus || "Pending" })),
    ...(supportDashboard.sharedOffers || []).map((item) => ({ type: "Surplus", hospital: item.organization?.name, itemName: item.itemName, privacy: item.privacy, status: item.quantityRange || "Available" })),
  ];
  return `
    <section class="ops-panel">
      <header><h2>Shared Requests</h2></header>
      <div class="ops-filters"><input placeholder="Search"><select><option>Hospital</option></select><select><option>Category</option></select><select><option>Priority</option></select></div>
      <div class="shared-records">${records.map((item) => `
        <article>
          <div><strong>${escapeHtml(item.hospital || "Hospital")}</strong><span>${escapeHtml(item.type)} · ${escapeHtml(item.itemName)}</span><small>${escapeHtml(item.privacy || "Shared")} · ${escapeHtml(item.status)}</small></div>
          <button type="button">Open</button>
        </article>
      `).join("") || emptyMarkup("No shared records.")}</div>
    </section>
  `;
}

function supportAlertsPage() {
  const alerts = supportDashboard.alerts || [];
  return `<section class="ops-panel"><header><h2>Alerts</h2></header>${supportRecentAlerts(alerts)}</section>`;
}

function supportReportsPage() {
  return `
    <section class="ops-panel">
      <header><h2>Reports</h2></header>
      <div class="report-list">
        <article><strong>Open Cases</strong><span>Simple list of active coordination cases.</span></article>
        <article><strong>Transfer History</strong><span>Completed and cancelled support transfers.</span></article>
        <article><strong>Alert Summary</strong><span>Operational alert history.</span></article>
      </div>
    </section>
  `;
}

function getSupportCases() {
  const queueCases = (supportDashboard.coordinationQueue || []).map((item) => supportCaseFromQueue(item));
  const requestCases = (supportDashboard.sharedNeeds || []).map((item) => ({
    hospital: item.organization?.name || "Hospital",
    itemName: item.itemName,
    urgency: item.urgency,
    status: item.flowStatus || "Waiting for Match",
    updatedAt: relativeUpdated(item.updatedAt),
    supplier: "Waiting for match",
  }));
  return [...queueCases, ...requestCases];
}

function supportCaseFromQueue(item) {
  return {
    hospital: item.needHospital?.name || "Requesting hospital",
    itemName: item.itemName,
    urgency: item.urgency,
    status: item.flowStatus || "Match Found",
    updatedAt: "5 minutes ago",
    supplier: item.offerHospital?.name || "No supplying hospital yet",
    transferHistory: `${item.offerHospital?.name || "Supplier"} matched with ${item.needHospital?.name || "requesting hospital"}`,
  };
}

function priorityClass(value) {
  const priority = String(value || "").toLowerCase();
  if (priority === "critical" || priority === "high") return "critical";
  if (priority === "medium") return "warning";
  return "normal";
}

function relativeUpdated(value) {
  if (!value) return "Recently";
  return "5 minutes ago";
}

function shipmentSummaryStrip(shipments) {
  const counts = {
    critical: shipments.filter((s) => ["critical", "high"].includes(String(s.riskLevel).toLowerCase()) || s.status === "at_risk").length,
    delayed: shipments.filter((s) => s.status === "delayed").length,
    transit: shipments.filter((s) => s.status === "in_transit").length,
    arrived: shipments.filter((s) => s.status === "arrived").length,
  };
  return `
    <div class="shipment-summary-strip">
      <article><span>Critical / At Risk</span><strong>${counts.critical}</strong></article>
      <article><span>Delayed</span><strong>${counts.delayed}</strong></article>
      <article><span>In Transit</span><strong>${counts.transit}</strong></article>
      <article><span>Arrived</span><strong>${counts.arrived}</strong></article>
    </div>
  `;
}

function shipmentTable(shipments, scope = "hospital") {
  if (!shipments.length) return emptyMarkup("No active shipments.");
  return `
    <div class="shipment-table">
      <table>
        <thead><tr>${["Shipment", scope === "support" ? "Hospital" : "Route", "Location", "Status", "Temperature", "Battery", "Updated", "Risk", "Actions"].map((h) => `<th>${h}</th>`).join("")}</tr></thead>
        <tbody>
          ${shipments.map((shipment, index) => `
            <tr>
              <td><strong>${escapeHtml(shipment.shipmentId)}</strong></td>
              <td>${escapeHtml(scope === "support" ? shipment.destinationHospitalName : `${shipment.origin} → ${shipment.destinationHospitalName}`)}</td>
              <td>${escapeHtml(shipment.currentLocation)}</td>
              <td>${shipmentStatusBadge(shipment.status)}</td>
              <td>${temperatureIndicator(shipment)}</td>
              <td>${batteryIndicator(shipment)}</td>
              <td>${escapeHtml(relativeUpdated(shipment.lastUpdated))}</td>
              <td>${riskBadge(shipment.riskLevel)}</td>
              <td><div class="shipment-actions"><button data-open-shipment="${index}" type="button">Details</button><button data-open-shipment="${index}" type="button">Map</button></div></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function shipmentDetailMarkup(shipment, showSupportAction = false) {
  return `
    <div class="shipment-detail-grid">
      <article><span>Status</span>${shipmentStatusBadge(shipment.status)}</article>
      <article><span>Route Progress</span><strong>${Number(shipment.routeProgress || 0)}%</strong></article>
      <article><span>Temperature</span>${temperatureIndicator(shipment)}</article>
      <article><span>Cooling Battery</span>${batteryIndicator(shipment)}</article>
    </div>
    ${shipmentRouteMap(shipment)}
    <section class="ops-panel"><h3>Supply Contents</h3><p>${escapeHtml((shipment.supplies || []).join(", ") || "Contents not provided")}</p></section>
    <section class="shipment-history">
      <article><h3>Temperature History</h3>${miniHistory(shipment.temperatureHistory, "°C")}</article>
      <article><h3>Battery History</h3>${miniHistory(shipment.batteryHistory, "%")}</article>
    </section>
    <section class="ops-panel"><h3>Related Alerts</h3>${(shipment.alerts || []).length ? (shipment.alerts || []).map((alert) => `<p>${escapeHtml(alert.severity || "alert")} · ${escapeHtml(alert.message || alert.type || "Alert")}</p>`).join("") : "<p>No alerts for this shipment.</p>"}</section>
    ${showSupportAction ? `<section class="ops-panel"><h3>Suggested Support Action</h3><p>${escapeHtml(suggestedShipmentAction(shipment))}</p></section>` : ""}
  `;
}

function shipmentStatusBadge(status) {
  return `<span class="shipment-badge ${shipmentTone(status)}">${escapeHtml(shipmentStatusLabel(status))}</span>`;
}

function shipmentStatusLabel(status) {
  const labels = { in_transit: "In Transit", delayed: "Delayed", at_risk: "At Risk", arrived: "Arrived" };
  return labels[String(status || "").toLowerCase()] || humanize(status || "In Transit");
}

function shipmentTone(status) {
  const value = String(status || "").toLowerCase();
  if (value === "at_risk") return "critical";
  if (value === "delayed") return "warning";
  if (value === "arrived") return "normal";
  return "good";
}

function temperatureIndicator(shipment) {
  const value = typeof shipment.temperature === "number" ? `${shipment.temperature.toFixed(1)}°C` : "N/A";
  const range = shipment.safeTemperatureMin != null && shipment.safeTemperatureMax != null ? `${shipment.safeTemperatureMin}-${shipment.safeTemperatureMax}°C` : "No range";
  return `<span class="metric-pill ${shipment.temperatureStatus === "critical" ? "critical" : "normal"}">${escapeHtml(value)} <small>${escapeHtml(range)}</small></span>`;
}

function batteryIndicator(shipment) {
  const level = typeof shipment.batteryLevel === "number" ? shipment.batteryLevel : 0;
  const tone = level <= 20 ? "critical" : level <= 45 ? "warning" : "normal";
  return `<span class="metric-pill ${tone}">${level}% <small>${escapeHtml(shipment.coolingUnitStatus || "normal")}</small></span>`;
}

function riskBadge(risk) {
  const tone = ["critical", "high"].includes(String(risk).toLowerCase()) ? "critical" : String(risk).toLowerCase() === "medium" ? "warning" : "normal";
  return `<span class="shipment-badge ${tone}">${escapeHtml(risk || "low")}</span>`;
}

function shipmentRouteMap(shipment) {
  const progress = clamp(Number(shipment.routeProgress || 0), 0, 100);
  const googleEmbed = googleMapsEmbedUrl(shipment);
  const routeUrl = shipment.googleMapsUrl || googleMapsDirectionsUrl(shipment.currentGps, shipment.destinationGps);

  return `
    <section class="ops-panel shipment-map-panel">
      <header>
        <div>
          <h3>Shipment Location</h3>
          <p>${escapeHtml(shipment.currentLocation || "Current location unavailable")} → ${escapeHtml(shipment.destinationHospitalName || "Destination")}</p>
        </div>
        <a class="map-link" href="${escapeHtml(routeUrl)}" target="_blank" rel="noopener">Open in Google Maps</a>
      </header>
      ${googleEmbed ? `
        <iframe class="google-route-map" title="Google route for ${escapeHtml(shipment.shipmentId)}" loading="lazy" referrerpolicy="no-referrer-when-downgrade" src="${escapeHtml(googleEmbed)}"></iframe>
      ` : shipmentRouteCanvas(shipment)}
      <div class="route-progress-row">
        <span>Route progress</span>
        <strong>${progress}%</strong>
      </div>
      <div class="route-bar"><span style="width:${progress}%"></span></div>
    </section>
  `;
}

function shipmentRouteCanvas(shipment) {
  const points = normalizeRoutePoints(shipment);
  const origin = routePoint(points[0]?.gps, 0, points.length);
  const truck = routePoint(points[1]?.gps, 1, points.length);
  const destination = routePoint(points[2]?.gps, 2, points.length);

  return `
    <div class="shipment-route-canvas" aria-label="Shipment route map">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <path class="planned-route" d="M ${origin.x} ${origin.y} C 38 18, 58 82, ${destination.x} ${destination.y}" />
        <path class="completed-route" d="M ${origin.x} ${origin.y} C 38 18, 58 82, ${truck.x} ${truck.y}" />
      </svg>
      ${routeMarker(origin, "Origin", shipment.origin || "Origin", "origin")}
      ${routeMarker(truck, "Truck", shipment.currentLocation || "Current truck location", "truck")}
      ${routeMarker(destination, "Destination", shipment.destinationHospitalName || "Destination", "destination")}
    </div>
  `;
}

function normalizeRoutePoints(shipment) {
  const points = shipment.routePoints || [];
  return [
    points[0] || { label: shipment.origin || "Origin", gps: shipment.originGps },
    points[1] || { label: shipment.currentLocation || "Truck", gps: shipment.currentGps },
    points[2] || { label: shipment.destinationHospitalName || "Destination", gps: shipment.destinationGps },
  ];
}

function routePoint(gps, index, total) {
  if (gps && typeof gps.lat === "number" && typeof gps.lng === "number") {
    return { x: clamp(((gps.lng - 35.45) / 0.16) * 100, 8, 92), y: clamp(100 - ((gps.lat - 33.82) / 0.11) * 100, 10, 88) };
  }
  return { x: 14 + (72 / Math.max(total - 1, 1)) * index, y: index === 1 ? 45 : 58 };
}

function routeMarker(position, label, detail, tone) {
  return `
    <div class="route-marker ${tone}" style="left:${position.x}%; top:${position.y}%">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(detail)}</strong>
    </div>
  `;
}

function googleMapsEmbedUrl(shipment) {
  if (!shipment.currentGps || !shipment.destinationGps) return "";
  const origin = gpsParam(shipment.currentGps);
  const destination = gpsParam(shipment.destinationGps);
  const key = window.GOOGLE_MAPS_API_KEY;
  if (!key) {
    return `https://maps.google.com/maps?saddr=${encodeURIComponent(origin)}&daddr=${encodeURIComponent(destination)}&output=embed`;
  }
  return `https://www.google.com/maps/embed/v1/directions?key=${encodeURIComponent(key)}&origin=${encodeURIComponent(origin)}&destination=${encodeURIComponent(destination)}&mode=driving`;
}

function googleMapsDirectionsUrl(originGps, destinationGps) {
  if (!originGps || !destinationGps) return "https://www.google.com/maps";
  return `https://www.google.com/maps/dir/?api=1&origin=${encodeURIComponent(gpsParam(originGps))}&destination=${encodeURIComponent(gpsParam(destinationGps))}`;
}

function gpsParam(gps) {
  return `${gps.lat},${gps.lng}`;
}

function miniHistory(history, unit) {
  if (!history || !history.length) return "<p>No history yet.</p>";
  return `<div class="mini-history">${history.map((point) => `<span>${escapeHtml(point.value ?? "N/A")}${unit}</span>`).join("")}</div>`;
}

function suggestedShipmentAction(shipment) {
  if (shipment.status === "at_risk" || shipment.temperatureStatus === "critical") return "Check backup cooling and contact carrier.";
  if (shipment.status === "delayed") return "Notify hospital and confirm revised ETA.";
  if (shipment.batteryStatus === "warning" || shipment.batteryStatus === "critical") return "Contact carrier to verify cooling-unit battery.";
  return "Continue monitoring.";
}

function renderHospitalHome() {
  const d = hospitalDashboard;
  const overview = d.overview || {};
  document.getElementById("hospitalName").textContent = d.organization?.name || "Hospital Workspace";
  document.getElementById("myInventoryCount").textContent = overview.ownInventoryItems || 0;
  document.getElementById("lowStockCount").textContent = countLowStock(d.inventory || []);
  document.getElementById("activeRequestCount").textContent = overview.ownRequests || 0;
  document.getElementById("incomingTransferCount").textContent = (d.transfers || []).filter((t) => t.direction === "incoming").length;
  document.getElementById("outgoingTransferCount").textContent = (d.transfers || []).filter((t) => t.direction === "outgoing").length;
  document.getElementById("notificationCount").textContent = (d.notifications || []).filter((n) => n.unread).length;
  document.getElementById("recommendations").innerHTML = (d.recommendations || []).map((item) => `<article>${escapeHtml(item)}</article>`).join("") || emptyMarkup("No recommendations right now.");
}

function renderHospitalModule(moduleId) {
  document.querySelectorAll("[data-hospital-module]").forEach((button) => button.classList.toggle("active", button.dataset.hospitalModule === moduleId));
  const titles = {
    inventory: "Inventory",
    requests: "My Requests",
    marketplace: "Shared Marketplace",
    transfers: "Transfers",
    notifications: "Notifications",
    reports: "Reports",
    staff: "Hospital Team",
    profile: "Hospital Profile",
    privacy: "Privacy & Sharing Settings",
    preferences: "Preferences",
  };
  document.getElementById("hospitalModuleTitle").textContent = titles[moduleId] || "Module";
  document.getElementById("hospitalModuleContent").innerHTML = hospitalModuleMarkup(moduleId);
}

function hospitalModuleMarkup(moduleId) {
  const d = hospitalDashboard;
  if (moduleId === "inventory") {
    return commandTiles(["Search items", "Filter by category", "Filter by expiry date", "Add new item", "Update quantity", "Remove item", "Mark damaged", "Mark expired"]) +
      cardList(d.inventory || [], (item) => [
        item.itemName,
        `${item.category} · Qty ${item.quantity} ${item.unit || ""} · Expires ${item.expiryDate || "N/A"} · ${privacyLabel(item.shareLevel)}`,
        item.status || "Available",
      ]);
  }
  if (moduleId === "requests") {
    return commandTiles(["Create request", "Share need", "Track approval flow", "Cancel request"]) +
      cardList(d.requests || [], (r) => [r.itemName, `Qty ${r.quantityNeeded} ${r.unit || ""} · ${r.urgency} priority · ${privacyLabel(r.shareLevel)} · ${r.publicNote || "No notes"}`, r.flowStatus || statusLabel(r.status)]);
  }
  if (moduleId === "marketplace") {
    const byHospital = groupMarketplace(d.sharedOffers || [], d.sharedNeeds || []);
    return cardList(byHospital, (h) => [h.name, `Sharing: ${h.offers.join(", ") || "None"} · Needs: ${h.needs.join(", ") || "None"}`, "Request Transfer"]);
  }
  if (moduleId === "transfers") {
    return commandTiles(["Pending", "Matched", "Approved", "In Transit", "Delivered", "Cancelled"]) +
      cardList(d.transfers || [], (t) => [t.transferId, `${t.hospitalInvolved} · ${t.items.join(", ")} · Qty ${t.quantity} · ${t.eta}`, t.flowStatus || statusLabel(t.status)]);
  }
  if (moduleId === "notifications") return cardList(d.notifications || [], (n) => [n.title, n.detail, n.unread ? "Unread" : "Read"], "notification");
  if (moduleId === "reports") return commandTiles(["Inventory Report", "Request History", "Transfer History"]) + cardList((d.reports || []).map((r) => ({ title: r })), (r) => [r.title, "Simple report for this hospital only", "Open"]);
  if (moduleId === "staff") return cardList(d.staff || [], (s) => [s.name, s.role, "Team member"]);
  if (moduleId === "profile") {
    const p = d.profile || {};
    return cardList([
      ["Hospital name", p.name],
      ["Address", p.address],
      ["Contact information", p.contact],
      ["Emergency contact", p.emergencyContact],
      ["Operating hours", p.operatingHours],
      ["Emergency level", p.emergencyLevel],
    ], (row) => [row[0], row[1] || "Not set", "Profile"]);
  }
  if (moduleId === "privacy") {
    const s = d.sharingSettings || {};
    return cardList(Object.entries(s).map(([key, value]) => ({ key, value })), (setting) => [
      humanize(setting.key),
      typeof setting.value === "boolean" ? (setting.value ? "Enabled" : "Disabled") : setting.value,
      "Privacy",
    ]);
  }
  if (moduleId === "preferences") return commandTiles(["Notification preferences", "Language", "Theme", "Account settings", "Password", "Two-factor authentication"]);
  return emptyMarkup("Module not available.");
}

function commandTiles(actions) {
  return `<div class="command-grid">${actions.map((action) => `<article class="action-tile"><strong>${escapeHtml(action)}</strong><button type="button">Open</button></article>`).join("")}</div>`;
}

function opsList(items, mapper) {
  if (!items.length) return emptyMarkup("No records available.");
  return `<div class="module-list">${items.map((item) => {
    const [title, detail, status] = mapper(item);
    return `<article class="ops-row"><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></div><span class="status-badge">${escapeHtml(status)}</span></article>`;
  }).join("")}</div>`;
}

function cardList(items, mapper, className = "") {
  if (!items.length) return emptyMarkup("No records available.");
  return `<div class="hospital-card-list ${className}">${items.map((item) => {
    const [title, detail, status] = mapper(item);
    return `<article class="hospital-record"><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></div><button type="button">${escapeHtml(status || "Open")}</button></article>`;
  }).join("")}</div>`;
}

function renderMap(containerId, hospitals) {
  const container = document.getElementById(containerId).querySelector("#adminMap") || document.getElementById(containerId);
  container.innerHTML = hospitals.map((hospital, index) => {
    const position = mapPosition(hospital.gps, index, hospitals.length);
    const status = hospital.criticalRequests ? "critical" : normalizeStatus(hospital.status);
    return `<button type="button" class="hospital-marker status-${status}" style="left:${position.x}%;top:${position.y}%"><span>${escapeHtml(hospital.name)}</span><small>${escapeHtml(hospital.region || "Region")} · ${hospital.activeRequests || 0} request(s)</small></button>`;
  }).join("");
}

function groupMarketplace(offers, needs) {
  const grouped = {};
  [...offers, ...needs].forEach((record) => {
    const name = record.organization?.name || "Hospital";
    grouped[name] ||= { name, offers: [], needs: [] };
    if (record.inventoryId) grouped[name].offers.push(record.itemName);
    if (record.requestId) grouped[name].needs.push(record.itemName);
  });
  return Object.values(grouped);
}

function redirectToRole(user, replace = false) {
  const targetPath = getRolePath(user?.role);
  if (window.location.protocol === "file:") {
    openRoleDashboard(user);
    return;
  }
  if (replace) {
    window.location.replace(targetPath);
    return;
  }
  window.location.href = targetPath;
}

function showOnly(id) {
  ["loginView", "deniedView", "adminView", "supportView", "hospitalView"].forEach((viewId) => {
    document.getElementById(viewId).hidden = viewId !== id;
  });
}

function showLogin(message) {
  stopLiveShipmentPolling();
  closeLiveShipmentMap();
  showOnly("loginView");
  const error = document.getElementById("loginError");
  const button = document.getElementById("loginButton");
  if (button) {
    button.textContent = "Sign In";
    button.disabled = false;
  }
  if (!error) return;
  const params = new URLSearchParams(window.location.search);
  const invalidLogin = params.get("error") === "invalid";
  if (invalidLogin) {
    error.textContent = "Invalid username or password.";
    error.hidden = false;
    return;
  }
  if (message && !message.includes("Authentication required")) {
    error.textContent = message;
    error.hidden = false;
  } else {
    error.hidden = true;
  }
}

function showDenied(message = "You do not have permission to open this dashboard.") {
  if (isAuthenticatedUser(currentUser)) {
    redirectToRole(currentUser, true);
    return;
  }
  showLogin(message);
}

function showDashboardLoadError(error) {
  const message = error?.message || "Dashboard failed to load.";

  if (currentUser?.role === "admin") {
    showOnly("adminView");
    document.getElementById("adminModules").innerHTML = emptyMarkup(`Admin page opened, but dashboard data could not load: ${message}`);
    return;
  }

  if (currentUser?.role === "support") {
    showOnly("supportView");
    document.getElementById("supportContent").innerHTML = emptyMarkup(`Support page opened, but dashboard data could not load: ${message}`);
    return;
  }

  if (currentUser?.role === "hospital") {
    showOnly("hospitalView");
    document.getElementById("hospitalName").textContent = currentUser.hospitalName || "Hospital Workspace";
    document.getElementById("hospitalContent").innerHTML = emptyMarkup(`Hospital page opened, but dashboard data could not load: ${message}`);
    return;
  }

  showDenied(message);
}

function logout() {
  stopLiveShipmentPolling();
  closeLiveShipmentMap();
  clearToken();
  currentUser = null;
  adminDashboard = null;
  supportDashboard = null;
  hospitalDashboard = null;
  if (window.location.protocol !== "file:") {
    window.location.replace("/login");
    return;
  }
  showLogin();
}

function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

function getStoredUser() {
  try {
    if (!getToken()) return null;
    const rawUser = localStorage.getItem(USER_KEY);
    if (!rawUser) return null;
    const user = JSON.parse(rawUser);
    if (!isAuthenticatedUser(user)) return null;
    return {
      ...user,
      role: normalizeRole(user.role),
      isAuthenticated: true,
    };
  } catch (error) {
    clearToken();
    return null;
  }
}

function saveAuthSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify({ ...user, role: normalizeRole(user.role), isAuthenticated: true }));
  localStorage.setItem(ROLE_KEY, normalizeRole(user.role));
  document.cookie = `vitae_token=${token}; Path=/; SameSite=Lax`;
}

function getDefaultUserForPath(path) {
  if (path.startsWith("/support")) return { ...DEMO_ACCOUNTS.support.user };
  if (path.startsWith("/hospital")) return { ...DEMO_ACCOUNTS["hospital-a"].user };
  return { ...DEMO_ACCOUNTS.admin.user };
}

function getTokenForUser(user) {
  if (user.role === "support") return DEMO_ACCOUNTS.support.token;
  if (user.hospitalId === "hospital-b") return DEMO_ACCOUNTS["hospital-b"].token;
  if (user.role === "hospital") return DEMO_ACCOUNTS["hospital-a"].token;
  return DEMO_ACCOUNTS.admin.token;
}

function restoreTokenFromRedirect() {
  const params = new URLSearchParams(window.location.search);
  const redirectToken = params.get("session");
  const validTokens = ["admin-token", "support-token", "hospital-a-token", "hospital-b-token"];

  if (!redirectToken || !validTokens.includes(redirectToken)) return;

  localStorage.setItem(TOKEN_KEY, redirectToken);
  document.cookie = `vitae_token=${redirectToken}; Path=/; SameSite=Lax`;
  params.delete("session");

  const nextSearch = params.toString();
  const nextUrl = `${window.location.pathname}${nextSearch ? `?${nextSearch}` : ""}`;
  window.history.replaceState({}, "", nextUrl);
}

function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(ROLE_KEY);
  document.cookie = "vitae_token=; Max-Age=0; Path=/; SameSite=Lax";
}

async function fetchJson(url) {
  const token = getToken();
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  const apiBase = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";
  const response = await fetch(`${apiBase}${url}`, { headers, credentials: "same-origin" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
}

function setupClock() {
  const now = new Date();
  ["admin", "support", "hospital"].forEach((scope) => {
    const time = document.getElementById(`${scope}CurrentTime`);
    const date = document.getElementById(`${scope}CurrentDate`);
    if (time) time.textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (date) date.textContent = now.toLocaleDateString([], { month: "long", day: "numeric", year: "numeric" });
  });
}

function redirectToLoginIfNeeded(message) {
  const path = window.location.pathname;
  if (path !== "/login") {
    window.location.replace("/login");
    return;
  }
  showLogin(message);
}

function saveAuthUser(user) {
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  localStorage.setItem(ROLE_KEY, normalizeRole(user.role));
}

function getRolePath(role) {
  const paths = {
    admin: "/admin",
    support: "/support",
    hospital: "/hospital",
  };
  return paths[normalizeRole(role)] || "/login";
}

function normalizeRole(role) {
  return String(role || "").trim().toLowerCase();
}

function isAuthenticatedUser(user) {
  return Boolean(user && user.isAuthenticated && user.username && normalizeRole(user.role));
}

function isAllowedRoute(path, user) {
  const role = normalizeRole(user?.role);
  if (path.startsWith("/admin")) return role === "admin";
  if (path.startsWith("/support")) return role === "support";
  if (path.startsWith("/hospital")) return role === "hospital";
  if (path.startsWith("/403")) return false;
  return path === "/" || path === "/login";
}

function updateLoggedInLabels() {
  const label = currentUser?.displayName || currentUser?.hospitalName || currentUser?.username || "User";
  const adminLabel = document.getElementById("adminLoggedInAs");
  const supportLabel = document.getElementById("supportLoggedInAs");
  const hospitalLabel = document.getElementById("hospitalLoggedInAs");
  if (adminLabel) adminLabel.textContent = `Logged in as ${label}`;
  if (supportLabel) supportLabel.textContent = `Logged in as ${label}`;
  if (hospitalLabel) hospitalLabel.textContent = `Logged in as ${label}`;
}

function countLowStock(items) {
  return items.filter((item) => typeof item.quantity === "number" && typeof item.minThreshold === "number" && item.quantity <= item.minThreshold).length;
}

function mapPosition(gps, index, total) {
  if (gps && typeof gps.lat === "number" && typeof gps.lng === "number") {
    return { x: clamp(((gps.lng - 35.45) / 0.16) * 100, 10, 82), y: clamp(100 - ((gps.lat - 33.82) / 0.11) * 100, 12, 78) };
  }
  return { x: 12 + (80 / Math.max(total, 1)) * index, y: 48 };
}

function normalizeStatus(value) {
  const status = String(value || "offline").toLowerCase();
  if (["online", "critical", "offline", "warning", "high"].includes(status)) return status === "high" ? "critical" : status;
  return "warning";
}

function formatEta(value) {
  return typeof value === "number" ? `${value} min ETA` : "ETA unavailable";
}

function humanize(value) {
  return String(value).replace(/([A-Z])/g, " $1").replace(/^./, (char) => char.toUpperCase());
}

function privacyLabel(value) {
  const labels = {
    private: "Private",
    support: "Shared with Support only",
    network: "Shared with all hospitals",
    emergency: "Emergency shared",
  };
  return labels[String(value || "private").toLowerCase()] || "Private";
}

function statusLabel(value) {
  const labels = {
    pending: "Pending",
    matched: "Matched",
    approved: "Approved",
    in_transit: "In Transit",
    incoming: "In Transit",
    outgoing: "Approved",
    delivered: "Delivered",
    cancelled: "Cancelled",
    open: "Pending",
  };
  return labels[String(value || "pending").toLowerCase()] || humanize(value || "pending");
}

function priorityLabel(value) {
  const priority = String(value || "medium").toLowerCase();
  if (priority === "critical") return "Critical";
  if (priority === "high") return "High";
  if (priority === "low") return "Low";
  return "Medium";
}

function emptyMarkup(message) {
  return `<div class="empty-state">${escapeHtml(message)}</div>`;
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function loadHospital() {
  showOnly("hospitalView");
  updateLoggedInLabels();
  hospitalDashboard = await fetchJson("/api/hospital/dashboard");
  liveShipmentState = { status: "loading", shipments: [], alerts: [] };
  renderHospitalHome();
  renderHospitalModule("dashboard");
  await loadLiveShipmentState();
  renderHospitalModule("dashboard");
  startLiveShipmentPolling();
}

function renderHospitalHome() {
  document.getElementById("hospitalName").textContent = hospitalDashboard.organization?.name || "Hospital Workspace";
}

function renderHospitalModule(moduleId) {
  activeHospitalModule = moduleId || "dashboard";
  document.querySelectorAll("[data-hospital-module]").forEach((button) => {
    button.classList.toggle("active", button.dataset.hospitalModule === moduleId);
  });
  const titles = {
    dashboard: ["Dashboard", "What requires attention today?"],
    inventory: ["Inventory", "Search, filter, and maintain hospital supplies."],
    requests: ["Requests", "Review supply needs and request status."],
    transfers: ["Transfers", "Track incoming, outgoing, completed, and cancelled transfers."],
    shipments: ["Shipment Monitoring", "Monitor active shipments, temperature, battery, and route progress."],
    "shipment-details": ["Shipment Details", "Route, telemetry, alerts, and shipment contents."],
    notifications: ["Notifications", "Filter operational updates."],
    reports: ["Reports", "Simple inventory, request, and transfer reports."],
    staff: ["Hospital Team", "Basic hospital team directory."],
    profile: ["Profile", "Hospital information, contacts, and preferences."],
    privacy: ["Settings", "Privacy settings and sharing preferences."],
  };
  const [title, subtitle] = titles[moduleId] || ["Workspace", "Hospital workspace."];
  document.getElementById("hospitalPageTitle").textContent = title;
  document.getElementById("hospitalPageSubtitle").textContent = subtitle;
  document.getElementById("hospitalContent").innerHTML = hospitalModuleMarkup(moduleId);
}

function hospitalModuleMarkup(moduleId) {
  const d = hospitalDashboard;
  if (moduleId === "dashboard") return hospitalDashboardMarkup(d);
  if (moduleId === "inventory") {
    return `
      ${hmsToolbar(["Search Inventory", "Category", "Status", "Expiry"], ["+ Add Item", "Export"])}
      ${hmsTable(["Item", "Category", "Available", "Reserved", "Expiry", "Visibility", "Status", "Actions"], (d.inventory || []).map((item) => [
        item.itemName,
        item.category,
        `${item.quantity} ${item.unit || ""}`,
        item.status === "surplus" ? "Surplus" : "Standard",
        item.expiryDate || "N/A",
        privacyLabel(item.shareLevel),
        item.status || "Available",
        "...",
      ]))}
    `;
  }
  if (moduleId === "requests") {
    return `
      ${hmsToolbar(["Search Requests", "Priority", "Status"], ["+ New Request"])}
      ${hmsTable(["Item", "Priority", "Status", "Created", "Actions"], (d.requests || []).map((r) => [
        r.itemName,
        priorityLabel(r.urgency),
        r.flowStatus || statusLabel(r.status),
        relativeUpdated(r.updatedAt),
        "...",
      ]))}
    `;
  }
  if (moduleId === "transfers") {
    return `
      <div class="hms-tabs"><button>Incoming</button><button>Outgoing</button><button>Completed</button><button>Cancelled</button></div>
      ${hmsTable(["Transfer", "Direction", "Hospital", "Items", "Quantity", "ETA", "Status", "Actions"], (d.transfers || []).map((t) => [
        t.transferId,
        humanize(t.direction),
        t.hospitalInvolved,
        t.items.join(", "),
        t.quantity,
        t.eta,
        t.flowStatus || statusLabel(t.status),
        "...",
      ]))}
    `;
  }
  if (moduleId === "shipments") {
    return `
      <section class="hms-panel">
        ${window.liveShipmentDashboard.render(liveShipmentState, { title: "Real-Time Shipment Monitoring" })}
      </section>
    `;
  }
  if (moduleId === "shipment-details") {
    const shipment = (d.shipments || [])[Number(activeHospitalShipment) || 0];
    if (!shipment) return emptyMarkup("No shipment selected.");
    return `
      <section class="shipment-detail">
        <header>
          <button data-hospital-module="shipments" type="button">Back</button>
          <div><h2>${escapeHtml(shipment.shipmentId)}</h2><p>${escapeHtml(shipment.currentLocation)}</p></div>
        </header>
        ${shipmentDetailMarkup(shipment, false)}
      </section>
    `;
  }
  if (moduleId === "notifications") {
    return `${hmsToolbar(["Unread", "Critical", "Transfer", "Inventory"], [])}${hmsList(d.notifications || [], (n) => [n.title, n.detail, n.unread ? "Unread" : "Read"])}`;
  }
  if (moduleId === "reports") {
    return `<div class="hms-report-grid">${(d.reports || []).map((report) => `<article><strong>${escapeHtml(report)}</strong><span>Simple hospital report</span></article>`).join("")}</div>`;
  }
  if (moduleId === "staff") {
    return hmsTable(["Name", "Role", "Status", "Actions"], (d.staff || []).map((s) => [s.name, s.role, s.status, "..."]));
  }
  if (moduleId === "profile") {
    const p = d.profile || {};
    return hmsDefinitionGrid([
      ["Hospital information", p.name],
      ["Address", p.address],
      ["Contacts", p.contact],
      ["Emergency contact", p.emergencyContact],
      ["Operating hours", p.operatingHours],
      ["Emergency level", p.emergencyLevel],
    ]);
  }
  if (moduleId === "privacy") {
    const s = d.sharingSettings || {};
    return hmsDefinitionGrid(Object.entries(s).map(([key, value]) => [
      humanize(key),
      typeof value === "boolean" ? (value ? "Enabled" : "Disabled") : value,
    ]));
  }
  return emptyMarkup("Module not available.");
}

function hospitalDashboardMarkup(d) {
  const lowStock = countLowStock(d.inventory || []);
  const pendingRequests = (d.requests || []).filter((r) => ["pending", "open"].includes(String(r.status || "").toLowerCase())).length;
  const incomingTransfers = (d.transfers || []).filter((t) => t.direction === "incoming").length;
  const unread = (d.notifications || []).filter((n) => n.unread).length;
  return `
    <section class="hms-kpis">
      <article><span>Low Stock</span><strong>${lowStock}</strong></article>
      <article><span>Pending Requests</span><strong>${pendingRequests}</strong></article>
      <article><span>Incoming Transfers</span><strong>${incomingTransfers}</strong></article>
      <article><span>Notifications</span><strong>${unread}</strong></article>
    </section>
    <section class="hms-panel">
      <header><h2>Recent Activity</h2><a href="#">View All Activity</a></header>
      <div class="hms-activity">
        ${hospitalActivity(d).map((item) => `<article><span>${item.icon}</span><div><strong>${escapeHtml(item.text)}</strong><small>${escapeHtml(item.time)}</small></div></article>`).join("")}
      </div>
    </section>
    <section class="hms-panel">
      <header><h2>Active Shipments</h2><a href="#" data-hospital-module="shipments">View All Shipments</a></header>
      ${window.liveShipmentDashboard.render(liveShipmentState, { title: "Real-Time Shipment Monitoring" })}
    </section>
  `;
}

function hospitalShipmentCards(shipments) {
  if (!shipments.length) return emptyMarkup("No active shipments.");
  return `<div class="hospital-shipment-list">${shipments.map((shipment, index) => `
    <article>
      <div>
        <strong>${escapeHtml(shipment.shipmentId)}</strong>
        <span>${escapeHtml(shipment.currentLocation)}</span>
      </div>
      ${shipmentStatusBadge(shipment.status)}
      ${temperatureIndicator(shipment)}
      ${batteryIndicator(shipment)}
      <small>${escapeHtml(relativeUpdated(shipment.lastUpdated))}</small>
      <div class="shipment-actions"><button data-open-shipment="${index}" type="button">Details</button><button data-open-shipment="${index}" type="button">Map</button></div>
    </article>
  `).join("")}</div>`;
}

function hmsToolbar(filters, actions) {
  return `<div class="hms-toolbar">${filters.map((label, index) => index === 0 ? `<input placeholder="${escapeHtml(label)}">` : `<select><option>${escapeHtml(label)}</option></select>`).join("")}${actions.map((action) => `<button type="button">${escapeHtml(action)}</button>`).join("")}</div>`;
}

function hmsTable(headers, rows) {
  if (!rows.length) return emptyMarkup("No records available.");
  return `<div class="hms-table"><table><thead><tr>${headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function hmsList(items, mapper) {
  if (!items.length) return emptyMarkup("No notifications.");
  return `<div class="hms-list">${items.map((item) => { const [title, detail, status] = mapper(item); return `<article><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></div><span class="status-badge">${escapeHtml(status)}</span></article>`; }).join("")}</div>`;
}

function hmsDefinitionGrid(rows) {
  return `<div class="hms-def-grid">${rows.map(([label, value]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value || "Not set")}</strong></article>`).join("")}</div>`;
}

function hospitalActivity(d) {
  return [
    { icon: "📦", text: "Inventory updated", time: "8 minutes ago" },
    { icon: "🚚", text: (d.transfers || [])[0] ? `Transfer ${(d.transfers || [])[0].transferId} updated` : "No transfer updates", time: "14 minutes ago" },
    { icon: "📝", text: (d.requests || [])[0] ? `Request approved for ${(d.requests || [])[0].itemName}` : "No request updates", time: "25 minutes ago" },
    { icon: "⚠", text: "Medicine expires tomorrow", time: "Today" },
  ];
}
