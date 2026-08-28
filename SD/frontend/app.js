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
  liveRefreshSequence: 0,
  workspaceRefreshSequence: 0,
  v2AlertRefreshSequence: 0,
  v2MonitoringRefreshSequence: 0,
  renderedViewKey: null,
  localDemo: { status: "idle", demo: null, monitoring: null, error: null },
  v2Monitoring: { status: "idle", data: null, error: null },
  v2Alerts: { status: "idle", alerts: [], error: null },
  v2ShipmentOptions: {
    status: "idle",
    productContexts: [],
    sensors: [],
    error: null,
  },
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
  appState.renderedViewKey = null;
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
  appState.renderedViewKey = null;
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
    await loadV2ShipmentOptions();
    const selectedShipmentId = appState.organization?.selectedShipmentId
      || appState.organization?.selectedTrackingId;
    const target = findV2MonitoringTarget(appState.data, selectedShipmentId);
    await setV2MonitoringTarget(target, false);
  }
  if (["organization_user", "driver"].includes(appState.user.role)) {
    await refreshV2Alerts(false);
  }
  renderWorkspace();
  startWorkspacePolling();
}

async function loadV2ShipmentOptions() {
  appState.v2ShipmentOptions = {
    status: "loading",
    productContexts: [],
    sensors: [],
    error: null,
  };
  try {
    const options = await window.VitaeV2ShipmentSetupApi.loadCreationOptions();
    appState.v2ShipmentOptions = {
      status: "ready",
      productContexts: options.productContexts,
      sensors: options.sensors,
      error: null,
    };
  } catch (error) {
    appState.v2ShipmentOptions = {
      status: "error",
      productContexts: [],
      sensors: [],
      error: error.message || "V2 setup options are unavailable.",
    };
  }
}

function handleBootFailure(error) {
  if (!appState.user) return showLogin(error.message);
  showOnly("roleView");
  document.getElementById("roleView").innerHTML = `<main class="foundation-state-page" role="alert"><span class="foundation-error-code">!</span><h1>Workspace unavailable</h1><p>${window.VitaeUI.escape(error.message || "VITAE could not load this workspace.")}</p><button class="foundation-primary" data-retry-workspace type="button">Try again</button></main>`;
}

function renderWorkspace() {
  const config = ROLE_CONFIG[appState.user.role];
  const roleView = document.getElementById("roleView");
  const viewKey = `${appState.user.role}:${appState.page}`;
  const sameView = appState.renderedViewKey === viewKey;
  const interactionState = sameView
    ? window.VitaeUI.captureInteractionState(roleView)
    : null;
  const html = config.renderer().render(appState, appState.page);
  showOnly("roleView");
  if (sameView) window.VitaeUI.reconcileHtml(roleView, html);
  else roleView.innerHTML = html;
  appState.renderedViewKey = viewKey;
  window.VitaeUI.restoreInteractionState(roleView, interactionState);
}

async function handleRoleClick(event) {
  const logoutButton = event.target.closest("[data-logout]");
  if (logoutButton) return logout();

  const pageButton = event.target.closest("[data-role-page]");
  if (pageButton) {
    appState.page = pageButton.dataset.rolePage;
    renderWorkspace();
    if (
      appState.user?.role === "organization_user"
      && appState.page === "create"
    ) {
      await loadV2ShipmentOptions();
      renderWorkspace();
    }
    if (
      ["organization_user", "driver"].includes(appState.user?.role)
      && appState.page === "alerts"
    ) {
      await refreshV2Alerts();
    }
    if (appState.user?.role === "admin" && appState.page === "simulation") {
      await loadLocalDemo();
      renderWorkspace();
    }
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
    reloadLocalDemo: loadLocalDemo,
    advanceLocalDemo: async () => {
      appState.localDemo = { ...appState.localDemo, status: "advancing", error: null };
      renderWorkspace();
      try {
        const payload = await window.VitaeAuth.api("/api/admin/local-demo/next", { method: "POST", body: "{}" });
        appState.localDemo = { status: "ready", ...payload, error: null };
      } catch (error) {
        appState.localDemo = { ...appState.localDemo, status: "error", error: error.message };
        throw error;
      }
      renderWorkspace();
    },
    notify: showToast,
  };
}

async function loadLocalDemo() {
  appState.localDemo = { ...appState.localDemo, status: "loading", error: null };
  try {
    const payload = await window.VitaeAuth.api("/api/admin/local-demo");
    appState.localDemo = { status: "ready", ...payload, error: null };
  } catch (error) {
    appState.localDemo = { status: "error", demo: null, monitoring: null, error: error.message };
  }
}

function organizationActions() {
  return {
    render: renderWorkspace,
    reload: async () => {
      appState.data = await window.VitaeAuth.api("/api/organization/dashboard");
      renderWorkspace();
    },
    reloadV2Options: loadV2ShipmentOptions,
    refreshLive: refreshLiveShipments,
    v2AlertCommand: runV2AlertCommand,
    selectV2Target: selectV2MonitoringShipment,
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
    v2AlertCommand: runV2AlertCommand,
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
  const sequence = ++appState.liveRefreshSequence;
  try {
    const payload = await window.shipmentApi.fetchLiveShipments();
    if (sequence !== appState.liveRefreshSequence) return;
    appState.live = { status: "ready", shipments: payload.shipments || [], alerts: payload.alerts || [], source: payload.source, lastFetchedAt: new Date().toISOString() };
  } catch (error) {
    if (sequence !== appState.liveRefreshSequence) return;
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
  const sequence = ++appState.workspaceRefreshSequence;
  try {
    const nextData = await window.VitaeAuth.api(config.endpoint);
    if (sequence !== appState.workspaceRefreshSequence) return;
    if (JSON.stringify(nextData) !== JSON.stringify(appState.data)) {
      appState.data = nextData;
      appState.workspaceRenderPending = true;
    }
    if (
      appState.page === "alerts"
      && ["organization_user", "driver"].includes(appState.user.role)
    ) {
      await refreshV2Alerts(false);
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

async function refreshV2Alerts(render = true) {
  const sequence = ++appState.v2AlertRefreshSequence;
  const shipments = v2AlertShipments();
  if (!shipments.length) {
    appState.v2Alerts = { status: "ready", alerts: [], error: null };
    if (render && appState.page === "alerts") renderWorkspace();
    return;
  }
  appState.v2Alerts = { ...appState.v2Alerts, status: "loading", error: null };
  try {
    const responses = await Promise.all(
      shipments.map(async (shipment) => ({
        shipment,
        payload: await window.VitaeV2AlertApi.listAlerts(shipment.lotTripId),
      })),
    );
    if (sequence !== appState.v2AlertRefreshSequence) return;
    appState.v2Alerts = {
      status: "ready",
      alerts: responses.flatMap(({ shipment, payload }) =>
        (payload.alerts || []).map((alert) => ({
          ...alert,
          shipmentId: shipment.shipmentId,
          lotTripId: shipment.lotTripId,
          v2: true,
        }))),
      error: null,
    };
  } catch (error) {
    if (sequence !== appState.v2AlertRefreshSequence) return;
    appState.v2Alerts = {
      ...appState.v2Alerts,
      status: "error",
      error: error.message || "V2 alerts are temporarily unavailable.",
    };
  }
  if (render && appState.page === "alerts") renderWorkspace();
}

function v2AlertShipments() {
  const data = appState.data || {};
  const candidates = appState.user?.role === "organization_user"
    ? data.shipments || []
    : [
        ...(data.deliveryRequests || []),
        ...(data.acceptedDeliveries || []),
        ...(data.activeDeliveries || []),
        ...(data.completedDeliveries || []),
      ];
  const byLotTrip = new Map();
  candidates.forEach((shipment) => {
    if (shipment?.lotTripId) byLotTrip.set(shipment.lotTripId, shipment);
  });
  return [...byLotTrip.values()];
}

async function runV2AlertCommand(lotTripId, alertId, command, payload = {}) {
  if (command === "acknowledge") {
    await window.VitaeV2AlertApi.acknowledge(lotTripId, alertId);
  } else if (command === "action") {
    await window.VitaeV2AlertApi.recordAction(
      lotTripId,
      alertId,
      payload.description,
    );
  } else if (command === "resolve") {
    await window.VitaeV2AlertApi.resolve(
      lotTripId,
      alertId,
      payload.resolutionNote,
    );
  } else {
    throw new Error("Unsupported V2 alert command.");
  }
  await refreshV2Alerts(false);
  if (
    appState.user?.role === "organization_user"
    && appState.v2Monitoring.lotTripId === lotTripId
  ) {
    await refreshV2Monitoring(false);
  }
  renderWorkspace();
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
  const lotTripId = appState.v2Monitoring.lotTripId;
  if (!lotTripId) return;
  const sequence = ++appState.v2MonitoringRefreshSequence;
  try {
    const data = await window.VitaeV2MonitoringApi.fetchLive(lotTripId);
    if (sequence !== appState.v2MonitoringRefreshSequence || appState.v2Monitoring.lotTripId !== lotTripId) return;
    appState.v2Monitoring = {
      ...appState.v2Monitoring,
      status: "ready",
      data,
      error: null,
      lastFetchedAt: new Date().toISOString(),
    };
  } catch (error) {
    if (sequence !== appState.v2MonitoringRefreshSequence || appState.v2Monitoring.lotTripId !== lotTripId) return;
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
  const lotTripId = appState.v2Monitoring.lotTripId;
  if (!lotTripId) return;
  appState.stopV2MonitoringPoll = window.VitaeV2MonitoringApi.startPolling(
    lotTripId,
    {
      onData: (data) => {
        if (appState.v2Monitoring.lotTripId !== lotTripId) return;
        appState.v2Monitoring = {
          ...appState.v2Monitoring,
          status: "ready",
          data,
          error: null,
          lastFetchedAt: new Date().toISOString(),
        };
        if (appState.page === "dashboard" && !isProtectedWorkflow()) renderWorkspace();
      },
      onError: (error) => {
        if (appState.v2Monitoring.lotTripId !== lotTripId) return;
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

function findV2MonitoringTarget(data, selectedShipmentId = null) {
  const shipments = data?.shipments || [];
  if (selectedShipmentId) {
    const selected = shipments.find(
      (shipment) => shipment.shipmentId === selectedShipmentId,
    );
    return selected?.lotTripId ? selected : null;
  }
  const mapped = shipments.filter((shipment) => shipment.lotTripId);
  return mapped.length === 1 ? mapped[0] : null;
}

async function selectV2MonitoringShipment(shipmentId) {
  const target = findV2MonitoringTarget(appState.data, shipmentId);
  await setV2MonitoringTarget(target, false);
}

async function setV2MonitoringTarget(target, render = true) {
  if (!target) {
    stopV2MonitoringPolling();
    appState.v2Monitoring = { status: "not_mapped", data: null, error: null };
    if (render && appState.page === "dashboard") renderWorkspace();
    return;
  }

  if (appState.v2Monitoring.lotTripId === target.lotTripId) {
    if (!appState.stopV2MonitoringPoll) {
      await refreshV2Monitoring(false);
      startV2MonitoringPolling();
    }
    return;
  }

  stopV2MonitoringPolling();
  appState.v2Monitoring = {
    status: "loading",
    data: null,
    error: null,
    lotTripId: target.lotTripId,
    shipmentId: target.shipmentId,
  };
  await refreshV2Monitoring(false);
  startV2MonitoringPolling();
  if (render && appState.page === "dashboard") renderWorkspace();
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
  const routeTitle = shipment.routeTitle || "External map preview";
  const modal = document.createElement("div");
  modal.className = "foundation-map-modal";
  modal.innerHTML = `<section role="dialog" aria-modal="true" aria-label="${window.VitaeUI.escape(routeTitle)}"><header><div><span>${window.VitaeUI.escape(routeTitle)}</span><h2>${window.VitaeUI.escape(shipment.shipmentId)}</h2></div><button data-close-map type="button" aria-label="Close route map">Close</button></header>${embed ? `<iframe title="Google Maps preview for ${window.VitaeUI.escape(shipment.shipmentId)}" src="${window.VitaeUI.escape(embed)}" loading="lazy"></iframe>` : window.VitaeUI.empty("Open Google Maps to navigate using your current device location.")}<footer><span>Google Maps navigation · not VITAE-computed route evidence</span><a href="${window.VitaeUI.escape(external)}" target="_blank" rel="noopener">Open in Google Maps</a></footer></section>`;
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
  appState.renderedViewKey = null;
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
