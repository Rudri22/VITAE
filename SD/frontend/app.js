const ROLE_CONFIG = {
  admin: { path: "/admin", endpoint: "/api/admin/dashboard", renderer: () => window.VitaeAdmin },
  organization_user: { path: "/organization", endpoint: "/api/organization/dashboard", renderer: () => window.VitaeOrganization },
  driver: { path: "/driver", endpoint: "/api/driver/dashboard", renderer: () => window.VitaeDriver },
  support: { path: "/support", endpoint: "/api/support/dashboard", renderer: () => window.VitaeSupport },
};

const appState = {
  user: null,
  data: null,
  page: null,
  live: { status: "idle", shipments: [], alerts: [] },
  liveTimer: null,
  workspaceTimer: null,
  workspaceRenderPending: false,
  v2Monitoring: { status: "idle", data: null, error: null },
  stopV2MonitoringPoll: null,
};

document.addEventListener("DOMContentLoaded", () => {
  bindGlobalEvents();
  boot().catch(handleBootFailure);
});

window.addEventListener("vitae:session-expired", () => {
  stopLivePolling();
  stopWorkspacePolling();
  stopV2MonitoringPolling();
  appState.user = null;
  appState.data = null;
  window.history.replaceState({}, "", "/login");
  showLogin("Your session expired. Please sign in again.");
});

function bindGlobalEvents() {
  document.getElementById("loginForm").addEventListener("submit", handleLogin);
  document.getElementById("roleView").addEventListener("click", handleRoleClick);
  document.getElementById("roleView").addEventListener("submit", handleRoleSubmit);
  document.getElementById("roleView").addEventListener("input", handleRoleFilter);
  document.getElementById("roleView").addEventListener("change", handleRoleFilter);
  document.getElementById("roleView").addEventListener("pointerdown", handleSignaturePointer);
  document.getElementById("roleView").addEventListener("pointermove", handleSignaturePointer);
  document.getElementById("roleView").addEventListener("pointerup", handleSignaturePointer);
  document.getElementById("roleView").addEventListener("pointercancel", handleSignaturePointer);
  document.getElementById("deniedView").addEventListener("click", (event) => {
    if (event.target.closest("[data-go-workspace]")) navigate(window.VitaeAuth.routeForRole(appState.user?.role), true);
    if (event.target.closest("[data-denied-logout]")) logout();
  });
  document.getElementById("roleView").addEventListener("click", (event) => {
    if (!event.target.closest("[data-retry-workspace]")) return;
    loadWorkspace().catch(handleBootFailure);
  });
}

function handleSignaturePointer(event) {
  const canvas = event.target.closest?.("[data-signature-pad]");
  if (!canvas) return;
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  if (!canvas.dataset.ready) {
    const scale = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(rect.width * scale));
    canvas.height = Math.max(1, Math.round(rect.height * scale));
    const context = canvas.getContext("2d");
    context.scale(scale, scale);
    context.lineWidth = 2.2;
    context.lineCap = "round";
    context.lineJoin = "round";
    context.strokeStyle = "#102f48";
    canvas.dataset.ready = "true";
  }
  const context = canvas.getContext("2d");
  const x = event.clientX - rect.left, y = event.clientY - rect.top;
  if (event.type === "pointerdown") {
    canvas.setPointerCapture(event.pointerId);
    canvas.dataset.drawing = "true";
    context.beginPath();
    context.moveTo(x, y);
    return;
  }
  if (event.type === "pointermove" && canvas.dataset.drawing === "true") {
    context.lineTo(x, y);
    context.stroke();
    canvas.dataset.signed = "true";
    return;
  }
  if (["pointerup", "pointercancel"].includes(event.type) && canvas.dataset.drawing === "true") {
    context.closePath();
    canvas.dataset.drawing = "";
    const input = canvas.closest("form")?.elements.receiverSignature;
    if (input && canvas.dataset.signed === "true") input.value = canvas.toDataURL("image/png");
  }
}

async function boot() {
  const path = window.location.pathname;
  if (!["/", "/login"].includes(path)) {
    showOnly("roleView");
    document.getElementById("roleView").innerHTML = `<main class="foundation-state-page" aria-busy="true"><span class="foundation-state-spinner" aria-hidden="true"></span><h1>Opening VITAE</h1><p>Verifying your session and loading the latest delivery state…</p></main>`;
  }
  if (path === "/login" || path === "/") {
    const current = await window.VitaeAuth.verify();
    if (!current) return showLogin();
    navigate(window.VitaeAuth.routeForRole(current.role), true);
    return;
  }

  const user = await window.VitaeAuth.verify();
  if (!user) {
    navigate("/login", true);
    showLogin("Your session has ended. Please sign in again.");
    return;
  }

  appState.user = user;
  const expectedPath = window.VitaeAuth.routeForRole(user.role);
  if (path === "/hospital" && user.role === "organization_user") {
    navigate(expectedPath, true);
    return;
  }
  if (path === "/403" || path !== expectedPath) {
    showDenied(path, expectedPath);
    return;
  }
  await loadWorkspace();
}

async function handleLogin(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = document.getElementById("loginButton");
  const error = document.getElementById("loginError");
  error.hidden = true;
  button.disabled = true;
  button.textContent = "Signing in…";
  try {
    const user = await window.VitaeAuth.login(form.elements.username.value.trim(), form.elements.password.value);
    appState.user = user;
    navigate(window.VitaeAuth.routeForRole(user.role));
  } catch (loginError) {
    error.textContent = loginError.message || "Invalid username or password.";
    error.hidden = false;
    button.disabled = false;
    button.textContent = "Sign in";
  }
}

async function loadWorkspace() {
  const config = ROLE_CONFIG[appState.user.role];
  if (!config) return showDenied(window.location.pathname, "/login");
  stopLivePolling();
  stopWorkspacePolling();
  stopV2MonitoringPolling();
  appState.page = defaultPage(appState.user.role);
  showOnly("roleView");
  document.getElementById("roleView").innerHTML = `<main class="foundation-state-page" aria-busy="true"><span class="foundation-state-spinner" aria-hidden="true"></span><h1>Loading your VITAE workspace</h1><p>Connecting to live operations dataâ€¦</p></main>`;
  appState.data = await window.VitaeAuth.api(config.endpoint);
  if (["organization_user", "driver", "support"].includes(appState.user.role)) {
    appState.live = { status: "loading", shipments: [], alerts: [] };
    renderWorkspace();
    await refreshLiveShipments();
    startLivePolling();
  }
  if (appState.user.role === "organization_user") {
    appState.v2Monitoring = { status: "loading", data: null, error: null };
    await refreshV2Monitoring(false);
    startV2MonitoringPolling();
  }
  renderWorkspace();
  startWorkspacePolling();
}

function handleBootFailure(error) {
  if (!appState.user) return showLogin(error.message);
  showOnly("roleView");
  document.getElementById("roleView").innerHTML = `<main class="foundation-state-page" role="alert"><span class="foundation-error-code">!</span><h1>Workspace unavailable</h1><p>${window.VitaeUI.escape(error.message || "VITAE could not load this workspace.")}</p><button class="foundation-primary" data-retry-workspace type="button">Try again</button></main>`;
}

function renderWorkspace() {
  const config = ROLE_CONFIG[appState.user.role];
  showOnly("roleView");
  document.getElementById("roleView").innerHTML = config.renderer().render(appState, appState.page);
}

async function handleRoleClick(event) {
  const logoutButton = event.target.closest("[data-logout]");
  if (logoutButton) return logout();

  const pageButton = event.target.closest("[data-role-page]");
  if (pageButton) {
    appState.page = pageButton.dataset.rolePage;
    renderWorkspace();
    return;
  }

  if (appState.user?.role === "admin" && await window.VitaeAdmin.handleClick(event, appState, adminActions())) return;
  if (appState.user?.role === "organization_user" && await window.VitaeOrganization.handleClick(event, appState, organizationActions())) return;
  if (appState.user?.role === "driver" && await window.VitaeDriver.handleClick(event, appState, driverActions())) return;
  if (appState.user?.role === "support" && await window.VitaeSupport.handleClick(event, appState, supportActions())) return;

  const mapButton = event.target.closest("[data-live-map-index]");
  if (mapButton) return openMap(appState.live.shipments[Number(mapButton.dataset.liveMapIndex)]);

  if (event.target.closest("[data-driver-map]")) openMap(appState.data.activeDelivery);
  if (event.target.closest("[data-close-map]") || event.target.classList.contains("foundation-map-modal")) closeMap();
}

async function handleRoleSubmit(event) {
  if (appState.user?.role === "admin" && await window.VitaeAdmin.handleSubmit(event, appState, adminActions())) return;
  if (appState.user?.role === "organization_user" && await window.VitaeOrganization.handleSubmit(event, appState, organizationActions())) return;
  if (appState.user?.role === "driver" && await window.VitaeDriver.handleSubmit(event, appState, driverActions())) return;
  if (appState.user?.role === "support" && await window.VitaeSupport.handleSubmit(event, appState, supportActions())) return;
  if (event.target.id !== "createShipmentForm") return;
  event.preventDefault();
  const form = event.target;
  const status = document.getElementById("shipmentFormStatus");
  const submit = form.querySelector("button[type=submit]");
  submit.disabled = true;
  status.textContent = "Creating shipment…";
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.safeTemperatureMin = Number(payload.safeTemperatureMin);
  payload.safeTemperatureMax = Number(payload.safeTemperatureMax);
  try {
    await window.VitaeAuth.api("/api/organization/shipments", { method: "POST", body: JSON.stringify(payload) });
    appState.data = await window.VitaeAuth.api("/api/organization/dashboard");
    await refreshLiveShipments();
    appState.page = "shipments";
    renderWorkspace();
  } catch (error) {
    status.textContent = error.message;
    submit.disabled = false;
  }
}

function handleRoleFilter(event) {
  if (appState.user?.role === "admin") window.VitaeAdmin.handleFilter(event, document.getElementById("roleView"), appState, adminActions());
  if (appState.user?.role === "organization_user") window.VitaeOrganization.handleFilter(event, appState, organizationActions());
  if (appState.user?.role === "driver") window.VitaeDriver.handleChange(event, appState, driverActions());
}

function adminActions() {
  return {
    render: renderWorkspace,
    reload: async () => {
      appState.data = await window.VitaeAuth.api("/api/admin/dashboard");
      renderWorkspace();
    },
    openMap,
    notify: showToast,
  };
}

function organizationActions() {
  return {
    render: renderWorkspace,
    reload: async () => {
      appState.data = await window.VitaeAuth.api("/api/organization/dashboard");
      renderWorkspace();
    },
    refreshLive: refreshLiveShipments,
    openMap,
    notify: showToast,
  };
}

function driverActions() {
  return {
    render: renderWorkspace,
    reload: async () => {
      appState.data = await window.VitaeAuth.api("/api/driver/dashboard");
      renderWorkspace();
    },
    openMap,
    openPickupMap: (shipment) => openDriverRoute(shipment, "pickup"),
    openDeliveryMap: (shipment) => openDriverRoute(shipment, "delivery"),
    notify: showToast,
  };
}

function supportActions() {
  return {
    render: renderWorkspace,
    reload: async () => {
      appState.data = await window.VitaeAuth.api("/api/support/dashboard");
      renderWorkspace();
    },
    notify: showToast,
  };
}

function showToast(message, tone = "success") {
  document.querySelector(".foundation-feedback")?.remove();
  const workspace = document.querySelector("#roleView .vitae-main") || document.getElementById("roleView");
  if (workspace && !workspace.hidden) {
    const feedback = document.createElement("div");
    feedback.className = `foundation-feedback ${tone}`;
    feedback.setAttribute("role", tone === "error" ? "alert" : "status");
    feedback.innerHTML = `<strong>${tone === "error" ? "Action not completed" : "Saved"}</strong><span>${window.VitaeUI.escape(message)}</span><button type="button" aria-label="Dismiss message">Ã—</button>`;
    feedback.querySelector("button").addEventListener("click", () => feedback.remove());
    workspace.prepend(feedback);
  }
  document.querySelector(".foundation-toast")?.remove();
  const toast = document.createElement("div");
  toast.className = `foundation-toast ${tone}`;
  toast.setAttribute("role", "status");
  toast.textContent = message;
  document.body.appendChild(toast);
  window.setTimeout(() => toast.remove(), 3200);
}

async function refreshLiveShipments() {
  try {
    const payload = await window.shipmentApi.fetchLiveShipments();
    appState.live = { status: "ready", shipments: payload.shipments || [], alerts: payload.alerts || [], source: payload.source, lastFetchedAt: new Date().toISOString() };
  } catch (error) {
    appState.live = { status: "error", shipments: [], alerts: [], error: error.message, lastFetchedAt: new Date().toISOString() };
  }
  const protectedWorkflow = isProtectedWorkflow();
  if (!protectedWorkflow && document.getElementById("roleView") && !document.getElementById("roleView").hidden) renderWorkspace();
}

function startLivePolling() {
  stopLivePolling();
  appState.liveTimer = window.setInterval(refreshLiveShipments, 5000);
}

function stopLivePolling() {
  if (appState.liveTimer) window.clearInterval(appState.liveTimer);
  appState.liveTimer = null;
}

async function refreshWorkspaceData() {
  if (!appState.user?.role) return;
  const config = ROLE_CONFIG[appState.user.role];
  if (!config) return;
  try {
    const nextData = await window.VitaeAuth.api(config.endpoint);
    if (JSON.stringify(nextData) !== JSON.stringify(appState.data)) {
      appState.data = nextData;
      appState.workspaceRenderPending = true;
    }
    const activeElement = document.activeElement;
    const editing = activeElement && activeElement.matches("input, textarea, select, [contenteditable='true']");
    const protectedWorkflow = isProtectedWorkflow();
    if (appState.workspaceRenderPending && !editing && !protectedWorkflow && !document.querySelector(".foundation-map-modal")) {
      appState.workspaceRenderPending = false;
      renderWorkspace();
    }
  } catch (error) {
    if (![401, 403].includes(error.status)) return;
  }
}

function isProtectedWorkflow() {
  if (appState.user?.role === "organization_user" && appState.page === "create") return true;
  if (appState.user?.role === "driver" && document.querySelector('[data-driver-form="complete"], [data-driver-form="incident"], [data-driver-form="support"]')) return true;
  return false;
}

function startWorkspacePolling() {
  stopWorkspacePolling();
  appState.workspaceTimer = window.setInterval(refreshWorkspaceData, 3000);
}

function stopWorkspacePolling() {
  if (appState.workspaceTimer) window.clearInterval(appState.workspaceTimer);
  appState.workspaceTimer = null;
  appState.workspaceRenderPending = false;
}

async function refreshV2Monitoring(render = true) {
  if (appState.user?.role !== "organization_user") return;
  try {
    const data = await window.VitaeV2MonitoringApi.fetchLive("lot-trip-sim-001");
    appState.v2Monitoring = {
      status: "ready",
      data,
      error: null,
      lastFetchedAt: new Date().toISOString(),
    };
  } catch (error) {
    appState.v2Monitoring = {
      ...appState.v2Monitoring,
      status: "error",
      error: error.message || "Live monitoring is temporarily unavailable.",
      lastFetchedAt: new Date().toISOString(),
    };
  }
  if (render && appState.page === "dashboard" && !isProtectedWorkflow()) renderWorkspace();
}

function startV2MonitoringPolling() {
  stopV2MonitoringPolling();
  appState.stopV2MonitoringPoll = window.VitaeV2MonitoringApi.startPolling(
    "lot-trip-sim-001",
    {
      onData: (data) => {
        appState.v2Monitoring = {
          status: "ready",
          data,
          error: null,
          lastFetchedAt: new Date().toISOString(),
        };
        if (appState.page === "dashboard" && !isProtectedWorkflow()) renderWorkspace();
      },
      onError: (error) => {
        appState.v2Monitoring = {
          ...appState.v2Monitoring,
          status: "error",
          error: error.message || "Live monitoring is temporarily unavailable.",
          lastFetchedAt: new Date().toISOString(),
        };
        if (appState.page === "dashboard" && !isProtectedWorkflow()) renderWorkspace();
      },
    },
  );
}

function stopV2MonitoringPolling() {
  if (appState.stopV2MonitoringPoll) appState.stopV2MonitoringPoll();
  appState.stopV2MonitoringPoll = null;
}

function openMap(shipment) {
  if (!shipment) return;
  closeMap();
  const currentGps = shipment.currentGps || coordinates(shipment.latitude, shipment.longitude);
  const destinationGps = shipment.destinationGps || coordinates(shipment.destinationLatitude, shipment.destinationLongitude);
  const current = gpsParam(currentGps);
  const destination = gpsParam(destinationGps);
  const embed = current && destination ? `https://maps.google.com/maps?saddr=${encodeURIComponent(current)}&daddr=${encodeURIComponent(destination)}&output=embed` : destination ? `https://maps.google.com/maps?q=${encodeURIComponent(destination)}&z=13&output=embed` : current ? `https://maps.google.com/maps?q=${encodeURIComponent(current)}&z=13&output=embed` : "";
  const external = current && destination ? `https://www.google.com/maps/dir/?api=1&origin=${encodeURIComponent(current)}&destination=${encodeURIComponent(destination)}` : destination ? `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(destination)}` : "https://www.google.com/maps";
  const routeTitle = shipment.routeTitle || "Shipment route";
  const modal = document.createElement("div");
  modal.className = "foundation-map-modal";
  modal.innerHTML = `<section role="dialog" aria-modal="true" aria-label="${window.VitaeUI.escape(routeTitle)}"><header><div><span>${window.VitaeUI.escape(routeTitle)}</span><h2>${window.VitaeUI.escape(shipment.shipmentId)}</h2></div><button data-close-map type="button" aria-label="Close route map">Close</button></header>${embed ? `<iframe title="Route for ${window.VitaeUI.escape(shipment.shipmentId)}" src="${window.VitaeUI.escape(embed)}" loading="lazy"></iframe>` : window.VitaeUI.empty("Open Google Maps to navigate using your current device location.")}<footer><span>${window.VitaeUI.escape(shipment.routeDestination || shipment.currentLocation || "Current location")}</span><a href="${window.VitaeUI.escape(external)}" target="_blank" rel="noopener">Start in Google Maps</a></footer></section>`;
  document.body.appendChild(modal);
  modal.addEventListener("click", handleRoleClick);
}

async function openDriverRoute(shipment, target) {
  if (!shipment) return;
  const destinationGps = target === "pickup" ? shipment.pickupGps : shipment.destinationGps;
  let currentGps = null;
  if (navigator.geolocation) {
    try {
      currentGps = await new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(
        (position) => resolve({ lat: position.coords.latitude, lng: position.coords.longitude }),
        reject,
        { enableHighAccuracy: true, timeout: 2500, maximumAge: 60000 },
      ));
    } catch (_error) {
      currentGps = target === "delivery" ? shipment.currentGps : null;
    }
  }
  openMap({
    ...shipment,
    currentGps: currentGps || (target === "delivery" ? shipment.currentGps : null),
    destinationGps,
    routeTitle: target === "pickup" ? "Navigate to pickup" : "Navigate to destination",
    routeDestination: target === "pickup" ? shipment.pickup : shipment.destination,
  });
}

function closeMap() {
  document.querySelector(".foundation-map-modal")?.remove();
}

function coordinates(lat, lng) {
  return typeof lat === "number" && typeof lng === "number" ? { lat, lng } : null;
}

function gpsParam(gps) {
  return gps && typeof gps.lat === "number" && typeof gps.lng === "number" ? `${gps.lat},${gps.lng}` : "";
}

function defaultPage(role) {
  return role === "driver" ? "home" : "dashboard";
}

function logout() {
  stopLivePolling();
  stopWorkspacePolling();
  stopV2MonitoringPolling();
  closeMap();
  window.VitaeAuth.clearSession();
  appState.user = null;
  appState.data = null;
  navigate("/login");
}

function navigate(path, replace = false) {
  if (window.location.protocol === "file:") {
    window.history[replace ? "replaceState" : "pushState"]({}, "", path);
    boot().catch((error) => showLogin(error.message));
    return;
  }
  window.location[replace ? "replace" : "assign"](path);
}

function showLogin(message = "") {
  stopLivePolling();
  stopWorkspacePolling();
  stopV2MonitoringPolling();
  showOnly("loginView");
  const error = document.getElementById("loginError");
  const button = document.getElementById("loginButton");
  button.disabled = false;
  button.textContent = "Sign in";
  error.textContent = message;
  error.hidden = !message;
}

function showDenied(requestedPath, expectedPath) {
  stopLivePolling();
  stopWorkspacePolling();
  stopV2MonitoringPolling();
  showOnly("deniedView");
  document.getElementById("deniedView").innerHTML = `<section><div class="vitae-brand"><span class="vitae-brand-mark">V</span><div><strong>VITAE</strong><span>Access control</span></div></div><span class="foundation-error-code">403</span><h1>Access denied</h1><p>Your ${window.VitaeUI.escape(window.VitaeUI.humanize(appState.user?.role))} account cannot open ${window.VitaeUI.escape(requestedPath)}.</p><div><button class="foundation-primary" data-go-workspace type="button">Return to my workspace</button><button class="foundation-secondary" data-denied-logout type="button">Log out</button></div><small>Allowed workspace: ${window.VitaeUI.escape(expectedPath)}</small></section>`;
}

function showOnly(id) {
  ["loginView", "deniedView", "roleView"].forEach((viewId) => { document.getElementById(viewId).hidden = viewId !== id; });
}
