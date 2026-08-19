(function () {
  const NAV = [["home", "Home"], ["deliveries", "My Deliveries"], ["trip", "Active Trip"], ["alerts", "Alerts"], ["support", "Support"]];
  const CHECKS = [["shipmentCollected", "Shipment collected"], ["containerClosed", "Container closed"], ["sensorConnected", "Sensor connected"], ["coolingActive", "Cooling active"], ["vehicleReady", "Vehicle ready"]];

  function driverState(state) {
    state.driverUi ||= { deliveryTab: "requests", selectedDeliveryId: null, checks: {}, completionOpen: false, incidentOpen: false, incidentCategory: "", completionSuccess: null };
    return state.driverUi;
  }

  function render(state, page = "home") {
    if (!NAV.some(([id]) => id === page)) page = "home";
    const ui = window.VitaeUI, local = driverState(state), data = state.data || {};
    const titles = { home: `Hello, ${(data.driver?.name || state.user.name || "Driver").split(" ")[0]}`, deliveries: "My Deliveries", trip: "Active Trip", alerts: "Alerts", support: "Driver Support", profile: "Profile" };
    return ui.shell({ roleClass: "driver-foundation-shell driver-mobile-app", roleLabel: "Driver", user: state.user, nav: NAV, active: page, mobile: true,
      header: `<header class="driver-header"><div><span class="foundation-eyebrow">${page === "home" ? "Today on the road" : "Driver workspace"}</span><h1>${esc(titles[page])}</h1></div><span class="driver-connection"><i></i> Live</span></header>`,
      content: pageContent(page, state, local) });
  }

  function pageContent(page, state, local) {
    if (page === "home") return home(state.data, local);
    if (page === "deliveries") return deliveriesPage(state.data, local);
    if (page === "trip") return activeTripPage(state.data, local);
    if (page === "alerts") return alertsPage(state.data);
    if (page === "support") return supportPage(state.data, local);
    return profilePage(state.data);
  }

  function home(data, local) {
    const focus = data.activeDelivery || (data.acceptedDeliveries || [])[0], active = Boolean(data.activeDelivery);
    return `${local.completionSuccess ? `<div class="driver-success" role="status"><strong>Delivery submitted successfully</strong><span>${esc(local.completionSuccess)} is awaiting organization verification.</span></div>` : ""}
      <section class="driver-request-section"><header><div><span class="foundation-eyebrow">Available to you</span><h2>Delivery Requests</h2></div><span>${(data.deliveryRequests || []).length} new</span></header>${requestCards(data.deliveryRequests || [], 3)}</section>
      ${focus ? focusCard(focus, active) : `<section class="driver-calm-state"><span aria-hidden="true">✓</span><strong>No accepted trip right now</strong><p>Open a delivery request above to accept your next trip.</p></section>`}
      <section class="driver-section driver-home-section"><header><h2>Recent Trips</h2><button data-driver-action="history" type="button">View history</button></header>${tripHistory(data.completedDeliveries || [], 3)}</section>`;
  }

  function focusCard(item, active) {
    const progress = Math.max(0, Math.min(100, Number(item.routeProgress || 0)));
    return `<section class="driver-focus-card ${item.riskLevel === "critical" ? "critical" : item.riskLevel === "high" ? "warning" : ""}"><header><div><span>${active ? "On the way" : "Accepted delivery"}</span><strong>${esc(item.shipmentId)}</strong></div><div class="vitae-status-stack">${window.VitaeUI.badge(item.status)}${v2Lifecycle(item)}</div></header><small>${esc(item.productCategory)}</small><h2>${esc(item.destination)}</h2><p>${esc(item.pickup)} <b aria-hidden="true">→</b> ${esc(item.destination)}</p><div class="driver-focus-facts"><div><span>${active ? "ETA" : "Pickup by"}</span><strong>${shortDate(active ? item.deadline : item.departureAt)}</strong></div><div><span>Container</span><strong>${temperature(item.temperature)}</strong></div></div><div class="driver-progress" aria-label="Route progress ${progress}%"><span style="width:${progress}%"></span></div><button class="driver-primary-action" data-driver-action="${active ? "open-trip" : "delivery-detail"}" data-id="${esc(item.shipmentId)}" type="button">${active ? "Open Active Trip" : "Prepare for Pickup"}</button></section>`;
  }

  function requestCards(items, limit) {
    return items.length ? `<div class="driver-request-list">${items.slice(0, limit).map((item) => `<button data-driver-action="delivery-detail" data-id="${esc(item.shipmentId)}" type="button"><div class="driver-request-route"><span><small>Pickup</small><strong>${esc(item.pickup)}</strong></span><i aria-hidden="true">→</i><span><small>Deliver to</small><strong>${esc(item.destination)}</strong></span></div><footer><span>Requested by ${esc(item.organizationName)} · ${shortDate(item.deadline)}</span><strong>View Request <b aria-hidden="true">›</b></strong></footer></button>`).join("")}</div>` : `<div class="driver-request-empty"><span aria-hidden="true">✓</span><p>No new delivery requests right now.</p></div>`;
  }

  function deliveriesPage(data, local) {
    if (local.selectedDeliveryId) {
      const item = allDeliveries(data).find((delivery) => delivery.shipmentId === local.selectedDeliveryId);
      return item ? deliveryDetails(item, local) : empty("Delivery is no longer available.");
    }
    const groups = { requests: data.deliveryRequests || [], accepted: data.acceptedDeliveries || [], on_the_way: data.activeDeliveries || [], history: data.completedDeliveries || [] };
    return `<div class="driver-tabs" role="tablist" aria-label="Delivery status">${Object.entries(groups).map(([key, items]) => `<button class="${local.deliveryTab === key ? "active" : ""}" data-driver-action="delivery-tab" data-tab="${key}" type="button" role="tab" aria-selected="${local.deliveryTab === key}">${human(key)} <span>${items.length}</span></button>`).join("")}</div><section class="driver-section"><div class="driver-delivery-list">${deliveryCards(groups[local.deliveryTab] || [])}</div></section>`;
  }

  function deliveryCards(items) {
    return items.length ? items.map((item) => `<button class="driver-delivery-card" data-driver-action="delivery-detail" data-id="${esc(item.shipmentId)}" type="button"><header><div><small>${esc(item.shipmentId)}</small><strong>${esc(item.productCategory)}</strong></div><div class="vitae-status-stack">${window.VitaeUI.badge(item.status)}${v2Lifecycle(item)}</div></header><dl><div><dt>Pickup</dt><dd>${esc(item.pickup)}</dd></div><div><dt>Destination</dt><dd>${esc(item.destination)}</dd></div><div><dt>Deadline</dt><dd>${shortDate(item.deadline)}</dd></div></dl><span class="driver-card-chevron" aria-hidden="true">›</span></button>`).join("") : empty("No deliveries in this group.");
  }

  function deliveryDetails(item, local) {
    const request = ["planned", "pending", "assigned"].includes(item.status), accepted = item.status === "accepted", checked = local.checks[item.shipmentId] || {}, ready = CHECKS.every(([key]) => checked[key]);
    const workflow = request
      ? `<div class="driver-request-decision"><p>Accept this request to confirm that you will collect and deliver the shipment.</p><button class="driver-primary-action" data-driver-action="accept-request" data-id="${esc(item.shipmentId)}" type="button">Accept Delivery Request</button></div>`
      : accepted
        ? `<div class="driver-accepted-banner"><span aria-hidden="true">✓</span><div><strong>Request accepted</strong><p>Navigate to the pickup, collect the shipment, then complete the checks.</p></div></div><button class="driver-route-action" data-driver-action="pickup-route" data-id="${esc(item.shipmentId)}" type="button">Navigate to Pickup</button><form data-driver-form="start" data-id="${esc(item.shipmentId)}"><fieldset class="driver-checklist"><legend>Ready to leave?</legend><p>Confirm every item after collecting the shipment.</p>${CHECKS.map(([key, label]) => `<label><input data-driver-check="${key}" data-id="${esc(item.shipmentId)}" type="checkbox" ${checked[key] ? "checked" : ""}><span>${esc(label)}</span></label>`).join("")}</fieldset><button class="driver-primary-action" type="submit" ${ready ? "" : "disabled"}>Start Delivery</button></form>`
        : item.status === "awaiting_verification"
          ? `<div class="driver-submitted-state"><strong>Delivery submitted</strong><span>Waiting for organization verification.</span></div>${tripRecord(item)}`
          : ["delivered", "flagged", "rejected"].includes(item.status)
            ? tripRecord(item)
            : `<button class="driver-primary-action" data-driver-action="open-trip" data-id="${esc(item.shipmentId)}" type="button">Open Active Trip</button>`;
    return `<button class="driver-back" data-driver-action="delivery-back" type="button">‹ My Deliveries</button><section class="driver-section driver-delivery-detail"><header><div><span class="foundation-eyebrow">Delivery request</span><h2>${esc(item.shipmentId)}</h2></div><div class="vitae-status-stack">${window.VitaeUI.badge(item.status)}${v2Lifecycle(item)}</div></header><strong class="driver-detail-product">${esc(item.productCategory)}</strong><dl class="driver-detail-facts">${detail("Pickup location", item.pickup)}${detail("Destination", item.destination)}${detail("Pickup time", shortDate(item.departureAt))}${detail("Delivery deadline", shortDate(item.deadline))}${detail("Required temperature", range(item))}${detail("Container condition", `${temperature(item.temperature)} · ${human(item.coolingUnitStatus || item.sensorStatus || "unknown")}`)}${detail("Organization contact", item.organizationContact || "Not provided")}${detail("Special handling", item.specialHandlingInstructions)}${v2Details(item)}</dl>${workflow}</section>`;
  }

  function activeTripPage(data, local) {
    const item = (data.activeDeliveries || []).find((delivery) => delivery.shipmentId === local.activeShipmentId) || data.activeDelivery;
    if (!item) return local.completionSuccess ? `<div class="driver-success"><strong>Delivery complete</strong><span>${esc(local.completionSuccess)} is awaiting verification.</span><button data-role-page="deliveries" type="button">View deliveries</button></div>` : empty("No delivery is currently active.");
    return `<section class="driver-trip-card"><header><div><span>Destination</span><h2>${esc(item.destination)}</h2><small>${esc(item.shipmentId)} · ${esc(item.productCategory)}</small></div><div class="vitae-status-stack">${window.VitaeUI.badge(item.status)}${v2Lifecycle(item)}</div></header><div class="driver-route-summary"><span>${esc(item.pickup)}</span><i></i><strong>${esc(item.destination)}</strong></div><div class="driver-trip-facts"><article><span>ETA</span><strong>${shortDate(item.deadline)}</strong></article><article><span>Temperature</span><strong>${temperature(item.temperature)}</strong><small>Required ${range(item)}</small></article></div><div class="driver-main-instruction"><span>Main instruction</span><strong>${esc(item.specialHandlingInstructions)}</strong></div><button class="driver-route-action" data-driver-action="route" data-id="${esc(item.shipmentId)}" type="button">Open Route</button>${local.completionOpen ? completionForm(item) : `<button class="driver-primary-action" data-driver-action="completion-open" type="button">Confirm Arrival</button>`}</section>
      <details class="driver-secondary-panel" ${local.incidentOpen ? "open" : ""}><summary>Report an incident</summary>${incidentForm(item, local)}</details>`;
  }

  function completionForm(item) {
    return `<form class="driver-completion-form" data-driver-form="complete" data-id="${esc(item.shipmentId)}"><h3>Confirm destination handoff</h3><p class="driver-form-note">Ask the receiving desk for its one-time code. This form stays open while live data updates.</p><label class="driver-confirm-check"><input name="confirmedArrival" type="checkbox" value="true" required><span>I confirm that I arrived at the destination.</span></label><label><span>Destination handoff code *</span><input name="destinationVerificationCode" required inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" placeholder="6-digit code"></label><label><span>Receiver’s name *</span><input name="receiverName" required autocomplete="name" placeholder="Full name"></label><div class="driver-signature-field"><div><span>Receiver signature *</span><button data-driver-action="signature-clear" type="button">Clear</button></div><canvas data-signature-pad aria-label="Receiver signature pad"></canvas><input name="receiverSignature" type="hidden"><small>Sign inside the box using a finger, stylus, or mouse.</small></div><label><span>Delivery notes</span><textarea name="deliveryNotes" rows="3" placeholder="Seal condition, handoff details, or exceptions"></textarea></label><div><button class="driver-secondary-action" data-driver-action="completion-close" type="button">Cancel</button><button class="driver-primary-action" type="submit">Confirm Handoff</button></div></form>`;
  }

  function incidentForm(item, local) {
    const categories = ["vehicle_problem", "cooling_failure", "sensor_problem", "traffic_delay", "route_blocked", "container_problem", "other_problem"];
    return `<form class="driver-mobile-form" data-driver-form="incident"><label><span>Problem category *</span><select name="category" required><option value="">Select problem</option>${options(categories, local.incidentCategory)}</select></label><label><span>Short description *</span><textarea name="description" rows="3" required maxlength="400"></textarea></label><label><span>Current shipment</span><input value="${esc(item.shipmentId)}" readonly><input name="shipmentId" value="${esc(item.shipmentId)}" type="hidden"></label><label><span>Current location</span><input name="location" value="${esc(item.currentLocation)}"></label><button class="driver-primary-action" type="submit">Report Incident</button></form>`;
  }

  function alertsPage(data) {
    const contact = data.driver?.organizationContact || "";
    return `<section class="driver-section"><div class="driver-action-alerts">${(data.alerts || []).length ? (data.alerts || []).map((item) => `<article class="${item.severity === "critical" ? "critical" : "warning"}"><header><div><span>${human(item.type)}</span><strong>${esc(item.shipmentId)}</strong></div>${window.VitaeUI.badge(item.severity)}</header><p><strong>${esc(item.message)}</strong> ${esc(item.instruction)}</p><small>Updated ${shortDate(item.updatedAt)}</small><button class="driver-alert-primary" data-driver-action="alert-response" data-response="action_completed" data-id="${esc(item.alertId)}" type="button">Action Completed</button><details><summary>More actions</summary><div><button data-driver-action="alert-response" data-response="problem_continues" data-id="${esc(item.alertId)}" type="button">Problem Continues</button><button data-driver-action="alert-response" data-response="contact_organization" data-contact="${esc(contact)}" data-id="${esc(item.alertId)}" type="button">Contact Organization</button><button data-driver-action="alert-support" data-shipment="${esc(item.shipmentId)}" type="button">Request Support</button><button data-driver-action="alert-sensor" data-id="${esc(item.alertId)}" data-shipment="${esc(item.shipmentId)}" type="button">Report Sensor Issue</button></div></details></article>`).join("") : empty("No alerts require action.")}</div></section>`;
  }

  function supportPage(data, local) {
    return `<section class="driver-section"><header><div><span class="foundation-eyebrow">Request help</span><h2>Contact Support</h2></div></header><form class="driver-mobile-form" data-driver-form="support"><label><span>Issue type *</span><select name="issueType" required><option value="">Select issue</option>${options(["delivery_help", "temperature_alert", "cooling_problem", "sensor_problem", "vehicle_problem", "route_problem", "other"], local.supportIssueType)}</select></label><label><span>Active shipment</span><select name="shipmentId"><option value="">General help</option>${recordOptions(data.assignedDeliveries || [], "shipmentId", "shipmentId", local.supportShipmentId)}</select></label><label><span>Short message *</span><textarea name="message" rows="3" required maxlength="500"></textarea></label><button class="driver-primary-action" type="submit">Send Help Request</button></form></section><section class="driver-section"><header><h2>Support responses</h2><span>${(data.tickets || []).length} requests</span></header><div class="driver-support-list">${(data.tickets || []).length ? data.tickets.map(ticketCard).join("") : empty("No support conversations yet.")}</div></section>`;
  }

  function ticketCard(ticket) {
    return `<details><summary><span><strong>${esc(ticket.subject)}</strong><small>${esc(ticket.ticketId)} · ${shortDate(ticket.updatedAt)}</small></span>${window.VitaeUI.badge(ticket.status)}</summary><p>${esc(ticket.summary)}</p><div class="driver-support-messages">${(ticket.messages || []).map((message) => `<article><strong>${esc(message.author)}</strong><small>${shortDate(message.timestamp)}</small><p>${esc(message.body)}</p></article>`).join("") || `<p>No public response yet.</p>`}</div></details>`;
  }

  function profilePage(data) {
    const driver = data.driver || {};
    return `<section class="driver-profile-card"><div class="driver-avatar" aria-hidden="true">${esc((driver.name || "D")[0])}</div><span>Driver profile</span><strong>${esc(driver.name)}</strong><p>${esc(driver.phone || "No phone provided")}</p><dl>${detail("Driver ID", driver.driverId)}${detail("Organization", driver.organizationName)}${detail("Vehicle", driver.vehicleId)}${detail("Availability", human(driver.status))}${detail("Completed trips", String((data.completedDeliveries || []).length))}</dl><button class="driver-secondary-action" data-logout type="button">Log out</button></section>`;
  }

  async function handleClick(event, state, actions) {
    const button = event.target.closest("[data-driver-action]");
    if (!button) return false;
    const local = driverState(state), action = button.dataset.driverAction, id = button.dataset.id;
    if (action === "open-deliveries") { state.page = "deliveries"; actions.render(); return true; }
    if (action === "history") { local.deliveryTab = "history"; local.selectedDeliveryId = null; state.page = "deliveries"; actions.render(); return true; }
    if (action === "delivery-tab") { local.deliveryTab = button.dataset.tab; actions.render(); return true; }
    if (action === "delivery-detail") { local.selectedDeliveryId = id; state.page = "deliveries"; actions.render(); return true; }
    if (action === "accept-request") return perform(async () => { await api(`/api/driver/shipments/${id}/accept`, "PATCH", {}); await actions.reload(); const accepted = allDeliveries(state.data).find((item) => item.shipmentId === id); actions.notify("Request accepted. Opening directions to pickup."); await actions.openPickupMap(accepted); }, actions);
    if (action === "delivery-back") { local.selectedDeliveryId = null; actions.render(); return true; }
    if (action === "open-trip") { state.page = "trip"; local.activeShipmentId = id || state.data.activeDelivery?.shipmentId; local.selectedDeliveryId = null; actions.render(); return true; }
    if (action === "pickup-route") { await actions.openPickupMap(allDeliveries(state.data).find((item) => item.shipmentId === id)); return true; }
    if (action === "route") { await actions.openDeliveryMap(allDeliveries(state.data).find((item) => item.shipmentId === id)); return true; }
    if (action === "completion-open") { local.completionOpen = true; actions.render(); return true; }
    if (action === "completion-close") { local.completionOpen = false; actions.render(); return true; }
    if (action === "signature-clear") { const form = button.closest("form"), canvas = form?.querySelector("[data-signature-pad]"), input = form?.elements.receiverSignature; if (canvas) { const context = canvas.getContext("2d"); context.clearRect(0, 0, canvas.width, canvas.height); canvas.dataset.signed = ""; } if (input) input.value = ""; return true; }
    if (action === "alert-support") { local.supportShipmentId = button.dataset.shipment; local.supportIssueType = "temperature_alert"; state.page = "support"; actions.render(); return true; }
    if (action === "alert-sensor") return perform(async () => { await api(`/api/driver/alerts/${id}`, "PATCH", { action: "sensor_issue" }); local.incidentOpen = true; local.incidentCategory = "sensor_problem"; state.page = "trip"; await actions.reload(); actions.notify("Sensor issue recorded. Add incident details below."); }, actions);
    if (action === "alert-response") return perform(async () => { await api(`/api/driver/alerts/${id}`, "PATCH", { action: button.dataset.response }); await actions.reload(); actions.notify("Alert response recorded."); if (button.dataset.response === "contact_organization" && button.dataset.contact) window.location.href = `mailto:${button.dataset.contact}`; }, actions);
    return true;
  }

  async function handleSubmit(event, state, actions) {
    const form = event.target, local = driverState(state);
    if (!form.dataset.driverForm) return false;
    event.preventDefault();
    if (form.dataset.driverForm === "start") return perform(async () => { const checks = local.checks[form.dataset.id] || {}; await api(`/api/driver/shipments/${form.dataset.id}/start`, "PATCH", { checks }); local.selectedDeliveryId = null; local.activeShipmentId = form.dataset.id; state.page = "trip"; await actions.reload(); actions.notify("Delivery started. Drive safely."); }, actions);
    if (form.dataset.driverForm === "complete") { const payload = Object.fromEntries(new FormData(form).entries()); payload.confirmedArrival = payload.confirmedArrival === "true"; if (!payload.receiverSignature) { actions.notify("Ask the receiver to sign inside the signature box.", "error"); return true; } if (!confirm("Confirm this destination handoff and submit it for organization review?")) return true; return perform(async () => { await api(`/api/driver/shipments/${form.dataset.id}/complete`, "PATCH", payload); local.completionOpen = false; local.completionSuccess = form.dataset.id; local.activeShipmentId = null; state.page = "home"; await actions.reload(); actions.notify("Destination handoff confirmed successfully."); }, actions); }
    if (form.dataset.driverForm === "incident") return perform(async () => { await api("/api/driver/incidents", "POST", Object.fromEntries(new FormData(form).entries())); local.incidentOpen = false; local.incidentCategory = ""; form.reset(); await actions.reload(); actions.notify("Incident reported to operations."); }, actions);
    if (form.dataset.driverForm === "support") return perform(async () => { await api("/api/driver/support", "POST", Object.fromEntries(new FormData(form).entries())); local.supportShipmentId = ""; local.supportIssueType = ""; form.reset(); await actions.reload(); actions.notify("Help request sent to Support."); }, actions);
    return false;
  }

  function handleChange(event, state, actions) {
    const checkbox = event.target.closest("[data-driver-check]");
    if (!checkbox) return false;
    const local = driverState(state), id = checkbox.dataset.id;
    local.checks[id] ||= {};
    local.checks[id][checkbox.dataset.driverCheck] = checkbox.checked;
    actions.render();
    return true;
  }

  function compactDeliveryList(items, limit, completed = false) { return items.length ? `<div class="driver-compact-list">${items.slice(0, limit).map((item) => `<button data-driver-action="delivery-detail" data-id="${esc(item.shipmentId)}" type="button"><span><strong>${esc(item.destination)}</strong><small>${esc(item.shipmentId)} · ${shortDate(item.deadline)}</small></span>${window.VitaeUI.badge(completed ? item.status : item.status)}</button>`).join("")}</div>` : empty(completed ? "No recently completed deliveries." : "No assigned deliveries."); }
  function compactAlerts(items, limit) { return items.length ? `<div class="driver-alert-preview">${items.slice(0, limit).map((item) => `<button data-role-page="alerts" type="button"><span><strong>${esc(item.message)}</strong><small>${esc(item.shipmentId)}</small></span>${window.VitaeUI.badge(item.severity)}</button>`).join("")}</div>` : empty("No important alerts."); }
  function tripHistory(items, limit) { return items.length ? `<div class="driver-trip-history">${items.slice(0, limit).map((item) => `<button data-driver-action="delivery-detail" data-id="${esc(item.shipmentId)}" type="button"><span class="driver-history-mark" aria-hidden="true">✓</span><span><strong>${esc(item.destination)}</strong><small>${esc(item.shipmentId)} · ${shortDate(item.arrivalTime || item.deadline)}</small></span>${window.VitaeUI.badge(item.status)}</button>`).join("")}</div>` : empty("No completed trips yet."); }
  function tripRecord(item) { return `<section class="driver-trip-record"><h3>Trip record</h3><dl>${detail("Accepted", shortDate(item.acceptedAt))}${detail("Arrival", shortDate(item.arrivalTime))}${detail("Receiver", item.receiverName || "Not recorded")}${detail("Signature", item.receiverSignature ? "Captured" : "Not provided")}${detail("Delivery notes", item.deliveryNotes || "No notes")}${detail("Final status", human(item.status))}</dl></section>`; }
  function v2Lifecycle(item) { return item?.lotTripId && item?.tripStatus ? `<span class="vitae-v2-lifecycle"><span>V2 trip</span>${window.VitaeUI.badge(item.tripStatus)}</span>` : ""; }
  function v2Details(item) { return item?.lotTripId ? `${detail("V2 trip lifecycle", human(item.tripStatus))}${detail("Lot trip ID", item.lotTripId)}` : ""; }
  function allDeliveries(data) { return [...(data.deliveryRequests || []), ...(data.acceptedDeliveries || []), ...(data.activeDeliveries || []), ...(data.completedDeliveries || [])]; }
  function detail(label, value) { return `<div><dt>${esc(label)}</dt><dd>${esc(value || "Not available")}</dd></div>`; }
  function recordOptions(items, key, label, selected = "") { return items.map((item) => `<option value="${esc(item[key])}" ${String(item[key]) === String(selected) ? "selected" : ""}>${esc(item[label])}</option>`).join(""); }
  function options(items, selected = "") { return items.map((item) => `<option value="${esc(item)}" ${item === selected ? "selected" : ""}>${esc(human(item))}</option>`).join(""); }
  function range(item) { return item.safeTemperatureMin == null || item.safeTemperatureMax == null ? "Not specified" : `${item.safeTemperatureMin}°C to ${item.safeTemperatureMax}°C`; }
  function temperature(value) { return typeof value === "number" ? `${value.toFixed(1)}°C` : "No reading"; }
  function shortDate(value) { if (!value) return "Not scheduled"; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); }
  function human(value) { return window.VitaeUI.humanize(value); }
  function esc(value) { return window.VitaeUI.escape(value ?? ""); }
  function empty(message) { return window.VitaeUI.empty(message); }
  async function api(url, method, body) { return window.VitaeAuth.api(url, { method, body: JSON.stringify(body) }); }
  async function perform(operation, actions) { try { await operation(); } catch (error) { actions.notify(error.message, "error"); } return true; }

  window.VitaeDriver = { render, handleClick, handleSubmit, handleChange };
})();
