(function () {
  const NAV = [["dashboard", "Dashboard"], ["create", "Request Delivery"], ["shipments", "Shipments"], ["tracking", "Live Tracking"], ["alerts", "Alerts"], ["support", "Support"]];
  const CATEGORIES = ["Vaccines", "Medicine", "Blood products", "Laboratory samples", "Dairy", "Frozen food", "Meat", "Fresh produce", "Other"];
  const PRODUCT_PROFILES = {
    Vaccines: { min: 2, max: 8, hours: 2, handling: "Keep refrigerated and protect from direct light." },
    Medicine: { min: 2, max: 8, hours: 4, handling: "Keep sealed in the cooled container." },
    "Blood products": { min: 2, max: 6, hours: 2, handling: "Prioritize delivery and avoid unnecessary handling." },
    "Laboratory samples": { min: 2, max: 8, hours: 3, handling: "Keep upright, sealed, and continuously cooled." },
    Dairy: { min: 1, max: 4, hours: 4, handling: "Maintain refrigeration and keep the container closed." },
    "Frozen food": { min: -20, max: -18, hours: 6, handling: "Keep frozen and minimize door-open time." },
    Meat: { min: 0, max: 4, hours: 4, handling: "Maintain refrigeration and prevent cross-contamination." },
    "Fresh produce": { min: 2, max: 8, hours: 6, handling: "Keep cool, dry, and protected from crushing." },
    Other: { min: 2, max: 8, hours: 4, handling: "Add any special handling instructions below." },
  };

  function orgState(state) {
    state.organization ||= { step: 1, draft: { submissionId: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}` }, filters: {}, selectedShipmentId: null, selectedTrackingId: null, selectedDriverId: null, selectedTicketId: null, saving: false, errors: {} };
    return state.organization;
  }

  function render(state, page = "dashboard") {
    if (!NAV.some(([id]) => id === page)) page = "dashboard";
    const ui = window.VitaeUI;
    const org = orgState(state);
    const titles = { dashboard: "Shipment Operations", create: "Request a delivery", shipments: "Shipments", tracking: "Live tracking", alerts: "Alerts", drivers: "Drivers", reports: "Reports", support: "Support" };
    const subtitles = { dashboard: "Monitor active shipments, respond to risks, and coordinate deliveries.", create: "Define the shipment and send an assigned Driver a delivery request.", shipments: "Search, review, and manage your organization’s shipments.", tracking: "Follow active shipments with five-second telemetry updates.", alerts: "Respond to cold-chain exceptions and escalate when needed.", drivers: "Manage availability and pre-trip shipment assignments.", reports: "A concise view of your organization’s real shipment outcomes.", support: "Create tickets and continue public conversations with Support." };
    const action = page === "dashboard" ? `<button class="foundation-primary" data-role-page="create" type="button">Request Delivery</button>` : "";
    return ui.shell({ roleClass: `organization-foundation-shell ${page === "dashboard" ? "organization-cockpit" : ""}`, roleLabel: state.data.organization?.name || "Organization Operations", user: state.user, nav: NAV, active: page,
      header: ui.pageHeader("Organization workspace", titles[page], subtitles[page], action), content: pageContent(page, state, org) });
  }

  function pageContent(page, state, org) {
    if (page === "dashboard") return dashboard(state);
    if (page === "create") return createWorkflow(state, org);
    if (page === "shipments") return shipmentsPage(state, org);
    if (page === "tracking") return trackingPage(state, org);
    if (page === "alerts") return alertsPage(state);
    if (page === "drivers") return driversPage(state, org);
    if (page === "reports") return reportsPage(state.data.reports || {});
    return supportPage(state, org);
  }

  function dashboard(state) {
    const data = state.data, summary = data.summary || {};
    const live = [...(data.activeShipments || [])].sort((a, b) => riskRank(a.riskLevel) - riskRank(b.riskLevel)).slice(0, 3);
    const arrivingToday = (data.shipments || []).filter((item) => isToday(item.expectedArrival) && !["delivered", "awaiting_verification", "cancelled", "rejected"].includes(item.status)).length;
    return `<section class="org-cockpit-metrics" aria-label="Essential operations metrics">
        ${cockpitMetric("Active Shipments", summary.activeShipments || 0, "Currently moving or planned")}
        ${cockpitMetric("At Risk", summary.atRiskShipments || 0, "High or critical risk")}
        ${cockpitMetric("Arriving Today", arrivingToday, "Expected before day end")}
        ${cockpitMetric("Available Drivers", summary.availableDrivers || 0, "Ready for assignment")}
      </section>
      ${v2MonitoringCard(state)}
      <div class="org-cockpit-workspace">
        <section class="foundation-panel org-live-board"><header><div><span class="foundation-eyebrow">Current network</span><h2>Live Shipments</h2></div><button class="foundation-link" data-role-page="tracking" type="button">Open Live Tracking</button></header>
          ${live.length ? `<div class="org-live-rows">${live.map(liveShipmentRow).join("")}</div>` : window.VitaeUI.empty("No active shipments are currently moving.")}
          ${(data.activeShipments || []).length > 3 ? `<footer><button class="foundation-link" data-role-page="shipments" type="button">View all shipments</button></footer>` : ""}
        </section>
        <section class="foundation-panel org-action-center"><header><div><span class="foundation-eyebrow">Prioritized work</span><h2>Action Center</h2></div></header>${actionCenter(data)}<footer><button class="foundation-link" data-role-page="alerts" type="button">View all alerts</button></footer></section>
      </div>`;
  }

  function v2MonitoringCard(state) {
    const monitoring = state.v2Monitoring;
    if (!monitoring || monitoring.status === "not_mapped") return "";
    const shipment = (state.data.shipments || []).find(
      (item) => item.shipmentId === monitoring.shipmentId,
    );
    if (!monitoring || monitoring.status === "idle" || monitoring.status === "loading") {
      return `<section class="foundation-panel org-v2-monitor" aria-busy="true" aria-live="polite"><header><div><span class="foundation-eyebrow">Shipment decision support</span><h2>Live shipment monitoring</h2></div></header>${window.VitaeUI.empty("Loading the latest accepted telemetry and decision.")}</section>`;
    }

    const payload = monitoring.data;
    if (!payload) {
      return `<section class="foundation-panel org-v2-monitor"><header><div><span class="foundation-eyebrow">Shipment decision support</span><h2>Live shipment monitoring</h2></div></header><p class="org-v2-monitor-error" role="alert">${esc(monitoring.error || "Live monitoring is temporarily unavailable. The shipment record has not been changed.")}</p></section>`;
    }

    const trip = payload.tripIdentity;
    const live = payload.liveState;
    const alert = payload.latestAlert;
    const status = live?.status || "WAITING_FOR_TELEMETRY";
    const statusLabel = live?.status || "No telemetry yet";
    const alertSummary = alert ? `${human(alert.alertType)} · ${human(alert.severity)}` : "No active alert";
    const journeyAvailable = validJourneyRiskPrediction(payload.journeyRisk);
    const completed = trip.status === "COMPLETED";
    return `<section class="foundation-panel org-v2-monitor" data-v2-monitor-status="${esc(status)}">
      <header><div><span class="foundation-eyebrow">${esc(shipment?.shipmentId || trip.lotTripId)}</span><h2>Live shipment monitoring</h2><p>${esc(productName(trip.productId))} · ${esc(trip.origin)} → ${esc(trip.destination)}</p></div>${monitoring.status === "error" ? `<span class="org-v2-monitor-stale">Latest refresh unavailable</span>` : ""}</header>
      ${completed ? `<p class="org-monitor-complete">Trip completed${trip.completedAt ? ` · ${esc(dateTime(trip.completedAt))}` : ""}. Final accepted condition remains visible for review.</p>` : ""}
      <div class="org-monitor-primary-grid">
        <article class="org-monitor-signal org-monitor-current" aria-labelledby="org-current-status-title">
          <h3 id="org-current-status-title">Current condition</h3>
          <div class="org-monitor-current-value">${window.VitaeUI.badge(statusLabel, statusTone(status))}</div>
          <p>${live ? "Authoritative ProductRules assessment" : "Waiting for the first accepted device reading"}</p>
        </article>
        ${primaryRiskCard(payload.journeyRisk, payload.futureRisk30m || payload.futureRisk, payload.operationalDecision)}
        ${operationalDecisionCard(payload.operationalDecision)}
      </div>
      ${reroutingPanel(payload.operationalDecision?.rerouting, payload.operationalDecision?.journeyContext)}
      <details class="org-monitor-supporting">
        <summary>Supporting details</summary>
        ${journeyAvailable ? additionalForecast(payload.futureRisk30m || payload.futureRisk) : ""}
        <dl class="org-monitor-detail-grid">
          ${monitoringDetail("Telemetry", `${telemetrySourceLabel(payload.telemetrySource)} · ${telemetryFreshness(live?.lastUpdated)}`)}
          ${monitoringDetail("Latest temperature", formatTemperature(live?.latestTemperature))}
          ${monitoringDetail("Excursion used", formatUtilization(live?.excursionUtilization))}
          ${monitoringDetail("Latest alert", alertSummary)}
          ${monitoringDetail("Trip lifecycle", window.VitaeUI.badge(trip.status), true)}
          ${monitoringDetail("Delivery workflow", shipment ? window.VitaeUI.badge(shipment.status) : "Unavailable", Boolean(shipment))}
          ${monitoringDetail("Last accepted reading", live?.lastUpdated ? dateTime(live.lastUpdated) : "No telemetry received")}
          ${journeyDetail(payload.operationalDecision?.journeyContext)}
        </dl>
        <div class="org-monitor-model-note"><strong>Decision basis</strong><p>ProductRules assess configured conditions now. Journey ML estimates deterioration before destination. The operational engine recommends what to do; rerouting compares only eligible destinations with available evidence.</p><small>Simulator-based engineering evaluation. Not real-world performance. Not clinical validation.</small></div>
      </details>
    </section>`;
  }

  function primaryRiskCard(journey, fixed, decision) {
    if (validJourneyRiskPrediction(journey)) {
      return `<article class="org-monitor-signal org-monitor-journey" data-journey-risk-state="PREDICTED" aria-labelledby="org-journey-risk-title">
        <h3 id="org-journey-risk-title">Risk before destination</h3>
        <strong class="org-risk-value">${riskCategory(decision)} <span>${formatProbability(journey.probability)}</span></strong>
        <p>Predicted deterioration before the current destination</p>
        <footer>Remaining route: ${formatDuration(journey.horizonMinutes)}<br><small>Journey-aware ML · simulator-trained engineering model</small></footer>
      </article>`;
    }
    if (validFutureRiskPrediction(fixed)) {
      return `<article class="org-monitor-signal org-monitor-future" data-future-risk-state="PREDICTED" aria-labelledby="org-future-risk-title">
        <h3 id="org-future-risk-title">Future risk</h3>
        <strong class="org-risk-value">${riskCategory(decision)} <span>${formatProbability(fixed.adverseEventProbability)}</span></strong>
        <p>Predicted deterioration in the next 30 minutes</p>
        <footer><small>30-minute fallback · simulator-trained engineering model</small></footer>
      </article>`;
    }
    const message = fixed?.state === "NOT_PREDICTED"
      ? futureRiskReason(fixed.reasonCode)
      : journey?.reason === "REMAINING_JOURNEY_DURATION_UNAVAILABLE"
        ? "Remaining route duration is unavailable"
        : "Prediction is not configured";
    return `<article class="org-monitor-signal org-monitor-future" data-future-risk-state="NOT_PREDICTED" aria-labelledby="org-future-risk-title">
      <h3 id="org-future-risk-title">Future risk</h3><strong class="org-future-risk-unavailable">Forecast unavailable</strong><p>${esc(message)}</p>
    </article>`;
  }

  function operationalDecisionCard(value) {
    if (!value || typeof value.recommendedAction !== "string" || typeof value.reason !== "string") {
      return `<article class="org-monitor-signal org-monitor-decision" data-operational-decision-state="UNAVAILABLE">
        <h3>Recommended action</h3><strong class="org-future-risk-unavailable">Decision unavailable</strong>
      </article>`;
    }
    return `<article class="org-monitor-signal org-monitor-decision" data-operational-decision-action="${esc(value.recommendedAction)}">
      <h3>Recommended action</h3>
      <strong>${esc(human(value.recommendedAction))}</strong>
      <p>${esc(value.reason)}</p>
      <footer>${esc(human(value.urgency || "routine"))} urgency · ${esc(forecastSource(value))}</footer>
    </article>`;
  }

  function reroutingPanel(value, journeyContext) {
    if (!value || typeof value.status !== "string") return "";
    const candidate = value.recommendedCandidate;
    if (candidate && typeof candidate.displayName === "string") {
      const current = value.currentDestination;
      const routeBased = value.routingEvidenceQuality === "ROUTE_DURATION"
        && typeof candidate.etaMinutes === "number"
        && current && typeof current.etaMinutes === "number";
      const progress = journeyContext && typeof journeyContext.estimatedJourneyProgress === "number"
        ? `${(journeyContext.estimatedJourneyProgress * 100).toFixed(0)}%`
        : null;
      const comparison = routeBased
        ? `<div><dt>Current destination</dt><dd>${formatDuration(current.etaMinutes)}</dd></div><div><dt>Recommended facility</dt><dd>${esc(candidate.displayName)} · ${formatDuration(candidate.etaMinutes)}</dd></div><div><dt>Estimated time saved</dt><dd>${formatDuration(Math.max(0, current.etaMinutes - candidate.etaMinutes))}</dd></div>`
        : `<div><dt>Recommended facility</dt><dd>${esc(candidate.displayName)}</dd></div><div><dt>Route evidence</dt><dd>Road ETA unavailable · ${typeof candidate.distanceKm === "number" ? `${candidate.distanceKm.toFixed(1)} km ` : ""}distance fallback</dd></div>`;
      const capability = candidate.capabilityBasis
        ? `<div><dt>Compatibility</dt><dd>Compatible with shipment profile</dd></div><div><dt>Evidence</dt><dd>Demo capability profile</dd></div>`
        : `<div><dt>Compatibility</dt><dd>Not confirmed</dd></div>`;
      return `<section class="org-rerouting-panel" data-rerouting-status="${esc(value.status)}"><header><div><span class="foundation-eyebrow">Destination decision</span><h3>${value.status === "REROUTE_RECOMMENDED" ? "Rerouting recommendation" : "Eligible alternative"}</h3></div><strong>${value.status === "REROUTE_RECOMMENDED" ? "REROUTE" : "AVAILABLE"}</strong></header><dl>${comparison}${progress ? `<div><dt>Estimated journey progress</dt><dd>${progress}</dd></div>` : ""}${capability}<div><dt>Why</dt><dd>${esc(value.reason || "A better eligible alternative is available.")}</dd></div></dl></section>`;
    }
    if (value.status === "INSUFFICIENT_ROUTE_DATA") {
      return `<section class="org-rerouting-panel org-rerouting-empty"><h3>Rerouting unavailable</h3><p>No trustworthy route comparison is available.</p></section>`;
    }
    if (value.status === "NO_BETTER_ALTERNATIVE") {
      const fallback = value.routingEvidenceQuality === "STRAIGHT_LINE_DISTANCE"
        ? " Road ETA is unavailable; comparison used distance fallback only."
        : "";
      return `<section class="org-rerouting-panel org-rerouting-empty"><h3>No better eligible destination found</h3><p>The available route and compatibility evidence does not support rerouting.${fallback}</p></section>`;
    }
    return "";
  }

  function additionalForecast(value) {
    if (!validFutureRiskPrediction(value)) return "";
    return `<div class="org-additional-forecast"><span>Additional forecast</span><strong>${formatProbability(value.adverseEventProbability)} risk in next 30 min</strong><small>Compatibility forecast · not the journey-wide estimate</small></div>`;
  }

  function journeyDetail(value) {
    if (!value) return "";
    const progress = typeof value.estimatedJourneyProgress === "number" ? `${(value.estimatedJourneyProgress * 100).toFixed(0)}% estimated` : "Unavailable";
    const remaining = typeof value.estimatedRemainingTravelMinutes === "number" ? formatDuration(value.estimatedRemainingTravelMinutes) : "Unavailable";
    return `${monitoringDetail("Journey progress", progress)}${monitoringDetail("Remaining route", remaining)}`;
  }

  function riskCategory(value) {
    return value && ["LOW", "MEDIUM", "HIGH"].includes(value.futureRiskCategory)
      ? esc(value.futureRiskCategory)
      : "";
  }

  function formatProbability(value) {
    return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(0)}%` : "Unavailable";
  }

  function formatDuration(minutes) {
    if (typeof minutes !== "number" || !Number.isFinite(minutes) || minutes < 0) return "Unavailable";
    const rounded = Math.round(minutes);
    if (rounded < 60) return `${rounded} min`;
    const hours = Math.floor(rounded / 60);
    const remainder = rounded % 60;
    return remainder ? `${hours} h ${remainder} min` : `${hours} h`;
  }

  function validFutureRiskPrediction(value) {
    return Boolean(value)
      && value.state === "PREDICTED"
      && typeof value.adverseEventProbability === "number"
      && Number.isFinite(value.adverseEventProbability)
      && value.adverseEventProbability >= 0
      && value.adverseEventProbability <= 1
      && value.predictionHorizonMinutes === 30
      && typeof value.cutoffAt === "string"
      && value.cutoffAt.trim() !== ""
      && !Number.isNaN(new Date(value.cutoffAt).getTime());
  }

  function validJourneyRiskPrediction(value) {
    return Boolean(value)
      && value.available === true
      && typeof value.probability === "number"
      && Number.isFinite(value.probability)
      && value.probability >= 0
      && value.probability <= 1
      && typeof value.horizonMinutes === "number"
      && Number.isFinite(value.horizonMinutes)
      && value.horizonMinutes > 0
      && value.target === "DETERIORATION_BEFORE_DESTINATION"
      && value.source === "JOURNEY_AWARE_MODEL";
  }

  function forecastSource(value) {
    if (value.futureRiskSource === "JOURNEY_AWARE_MODEL") return "Journey-aware forecast";
    if (value.futureRiskSource === "FIXED_30_MINUTE_FALLBACK") return "30-minute fallback";
    return "No forecast";
  }

  function futureRiskReason(code) {
    return ({
      NO_ACCEPTED_TELEMETRY: "Waiting for telemetry",
      CURRENT_STATUS_NOT_ELIGIBLE: "Forecast not applicable for current status",
      TRIP_NOT_ACTIVE: "Forecast unavailable for inactive trip",
      HISTORY_NOT_COHERENT: "Forecast temporarily unavailable",
      CONCURRENT_UPDATE: "Updating telemetry - try again shortly",
      INFERENCE_UNAVAILABLE: "Forecast temporarily unavailable",
    })[code] || "Forecast unavailable";
  }

  function monitoringDetail(label, value, html = false) {
    return `<div><dt>${esc(label)}</dt><dd>${html ? value : esc(value)}</dd></div>`;
  }

  function formatTemperature(value) {
    return typeof value === "number" ? `${value.toFixed(1)} °C` : "No reading";
  }

  function formatUtilization(value) {
    return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "Not applicable";
  }

  function telemetrySourceLabel(value) {
    return ({ REAL_DEVICE: "Real device", SIMULATOR: "Simulator", MANUAL_TEST: "Manual test", REPLAY: "Replay" })[value] || "Unavailable";
  }

  function telemetryFreshness(value) {
    if (!value || Number.isNaN(new Date(value).getTime())) return "No accepted reading";
    const ageSeconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
    if (ageSeconds <= 60) return `Current - ${Math.round(ageSeconds)} seconds ago`;
    if (ageSeconds <= 300) return `Recent - ${Math.round(ageSeconds / 60)} minutes ago`;
    return `Delayed - ${Math.round(ageSeconds / 60)} minutes ago`;
  }

  function productName(productId) {
    return String(productId || "Unknown product").replaceAll("-", " ").toUpperCase();
  }

  function statusTone(status) {
    if (status === "SAFE") return "safe";
    if (["CRITICAL", "RULE_VIOLATION"].includes(status)) return "critical";
    if (["MONITOR", "AT_RISK", "DATA_ERROR"].includes(status)) return "warning";
    return "neutral";
  }

  function cockpitMetric(label, value, context) {
    return `<article><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(context)}</small></article>`;
  }

  function liveShipmentRow(item) {
    const progress = Math.max(0, Math.min(100, Number(item.routeProgress || 0)));
    return `<button class="org-live-row" data-org-action="shipment-detail" data-id="${esc(item.shipmentId)}" type="button">
      <span class="org-live-identity"><small>${esc(item.shipmentId)}</small><strong>${esc(item.productName || item.productCategory)}</strong><em>${esc(item.origin)} <b aria-hidden="true">→</b> ${esc(item.destinationHospitalName)}</em></span>
      <span class="org-live-driver"><small>Driver</small><strong>${esc(item.driverName || item.driverId || "Unassigned")}</strong></span>
      <span class="org-live-condition"><small>Condition</small><strong>${temperature(item.temperature)}</strong><em>${range(item)}</em></span>
      <span class="org-live-eta"><small>ETA</small><strong>${dateTime(item.expectedArrival)}</strong></span>
      <span class="org-live-risk">${window.VitaeUI.badge(item.conditionStatus || item.riskLevel)}<i><b style="width:${progress}%"></b></i><small>${progress}% route</small></span>
    </button>`;
  }

  function actionCenter(data) {
    const items = [];
    const criticalAlertShipments = new Set((data.alerts || []).filter((alert) => alert.status !== "resolved" && alert.severity === "critical").map((alert) => alert.shipmentId));
    (data.shipments || []).forEach((shipment) => {
      if ((shipment.riskLevel === "critical" || shipment.riskLevel === "high") && !criticalAlertShipments.has(shipment.shipmentId)) items.push({ kind: "Shipment risk", id: shipment.shipmentId, text: (shipment.riskExplanation || ["Cold-chain risk requires review."])[0], tone: shipment.riskLevel, time: shipment.lastUpdated, rank: riskRank(shipment.riskLevel), action: "shipment-detail" });
      else if (shipment.status === "awaiting_verification") items.push({ kind: "Delivery verification", id: shipment.shipmentId, text: "Arrival evidence is ready for organization review.", tone: shipment.status, time: shipment.arrivalTime || shipment.lastUpdated, rank: 2, action: "shipment-detail" });
      else if (shipment.status === "delayed") items.push({ kind: "Delayed shipment", id: shipment.shipmentId, text: "The expected delivery timeline has changed.", tone: "warning", time: shipment.lastUpdated, rank: 2, action: "shipment-detail" });
      else if (shipment.status === "accepted") items.push({ kind: "Driver accepted", id: shipment.shipmentId, text: "The Driver accepted the request and is heading to the pickup.", tone: "accepted", time: shipment.acceptedAt || shipment.lastUpdated, rank: 2, action: "shipment-detail" });
      else if (["planned", "pending", "assigned"].includes(shipment.status)) items.push({ kind: "Driver response", id: shipment.shipmentId, text: "Waiting for the assigned Driver to accept this delivery request.", tone: "pending", time: shipment.lastUpdated, rank: 3, action: "shipment-detail" });
      else if (shipment.sensorStatus === "offline") items.push({ kind: "Offline sensor", id: shipment.shipmentId, text: "Live temperature telemetry is unavailable.", tone: "offline", time: shipment.lastUpdated, rank: 1, action: "shipment-detail" });
    });
    (data.alerts || []).filter((alert) => alert.status !== "resolved" && alert.severity === "critical").forEach((alert) => items.push({ kind: "Critical alert", id: alert.shipmentId, text: alert.explanation, tone: alert.severity, time: alert.updatedAt, rank: 0, page: "alerts" }));
    const prioritized = items.sort((a, b) => a.rank - b.rank || String(b.time || "").localeCompare(String(a.time || ""))).slice(0, 4);
    if (!prioritized.length) return `<div class="org-action-clear"><span aria-hidden="true">✓</span><p>All active shipments are operating normally.</p></div>`;
    return `<div class="org-action-list">${prioritized.map((item) => `<button ${item.page ? `data-role-page="${item.page}"` : `data-org-action="${item.action}" data-id="${esc(item.id)}"`} type="button"><span class="org-action-copy"><small>${esc(item.kind)}</small><strong>${esc(item.id)}</strong><em>${esc(item.text)}</em></span><span class="org-action-meta">${window.VitaeUI.badge(item.tone)}<time>${compactTime(item.time)}</time></span><b class="org-action-chevron" aria-hidden="true">›</b></button>`).join("")}</div>`;
  }

  function operationalStrip(data) {
    const upcoming = [...(data.shipments || [])].filter((item) => item.expectedArrival && new Date(item.expectedArrival) >= new Date() && !["delivered", "awaiting_verification", "cancelled", "rejected"].includes(item.status)).sort((a, b) => new Date(a.expectedArrival) - new Date(b.expectedArrival))[0];
    const drivers = data.drivers || [], available = drivers.filter((item) => item.status === "available").length, onRoute = drivers.filter((item) => ["on_route", "assigned"].includes(item.status)).length;
    const latest = (data.shipments || []).flatMap((shipment) => (shipment.timeline || []).map((event) => ({ ...event, shipmentId: shipment.shipmentId }))).sort((a, b) => String(b.timestamp || "").localeCompare(String(a.timestamp || "")))[0];
    return `<section class="org-operational-strip" aria-label="Operational snapshot"><article><span>Next Arrival</span><strong>${esc(upcoming?.shipmentId || "No scheduled arrival")}</strong><small>${upcoming ? `${esc(upcoming.destinationHospitalName)} · ${esc(compactTime(upcoming.expectedArrival))}` : "—"}</small></article><article><span>Driver Capacity</span><strong>${available} available</strong><small>${onRoute} currently assigned or on route</small></article><article><span>Latest Update</span><strong>${esc(latest?.label || "No recent update")}</strong><small>${latest ? `${esc(latest.shipmentId)} · ${esc(compactTime(latest.timestamp))}` : "—"}</small></article></section>`;
  }

  function createWorkflow(state, org) {
    const data = state.data, d = org.draft;
    const facilities = data.facilities || [], drivers = (data.drivers || []).filter((item) => ["available", "assigned"].includes(item.status));
    const setup = state.v2ShipmentOptions || { status: "idle", productContexts: [], sensors: [] };
    const contexts = setup.productContexts || [];
    const availableSensors = (setup.sensors || []).filter(isAvailableSensor);
    const v2Enabled = d.v2Enabled === true;
    const selectedContext = d.v2ContextIndex === undefined || d.v2ContextIndex === ""
      ? null
      : contexts[Number(d.v2ContextIndex)];
    const category = d.productCategory || "", profile = PRODUCT_PROFILES[category] || PRODUCT_PROFILES.Medicine;
    const departure = d.departureAt || toLocalInput(new Date(Date.now() + 15 * 60 * 1000));
    const arrival = d.expectedArrival || toLocalInput(new Date(new Date(departure).getTime() + profile.hours * 60 * 60 * 1000));
    const selectedDriver = d.driverId || drivers[0]?.driverId || "";
    return `<section class="foundation-panel org-create org-quick-request"><form class="org-section-form" data-org-form="quick-request">
      <header><span class="foundation-eyebrow">Quick delivery request</span><h2>What needs to be moved?</h2><p>Enter the essentials. VITAE fills the cold-chain settings, equipment, and recommended deadline.</p></header>
      ${org.formError ? `<p class="org-inline-error" role="alert">${esc(org.formError)}</p>` : ""}<p class="org-request-feedback" role="status"></p>
      <div class="org-quick-grid">
        <label class="org-quick-type"><span>Type of item *</span><select name="productCategory" required><option value="">Choose what you are moving</option>${options(CATEGORIES, category)}</select></label>
        ${field("Amount", "quantity", d.quantity, "number", true, "min=\"0.01\" step=\"0.01\" placeholder=\"Example: 10\"")}
        <label><span>Unit *</span><select name="unit" required>${options(["boxes", "units", "bags", "trays", "liters", "kilograms"], d.unit || "boxes")}</select></label>
        ${field(v2Enabled ? "Item name" : "Item name (optional)", "productName", selectedContext?.productName || d.productName, "text", v2Enabled, `${v2Enabled ? "readonly" : ""} placeholder=\"Example: Insulin\"`)}
      </div>
      <div class="org-quick-section"><h3>Pickup and delivery</h3><div class="org-form-grid"><label><span>Pick up from *</span><select name="originFacilityId" required><option value="">Choose pickup</option>${recordOptions(facilities, "facilityId", "name", d.originFacilityId)}</select></label><label><span>Deliver to *</span><select name="destinationFacilityId" required><option value="">Choose destination</option>${recordOptions(facilities, "facilityId", "name", d.destinationFacilityId)}</select></label><label><span>Send request to *</span><select name="driverId" required><option value="">Choose driver</option>${recordOptions(drivers, "driverId", "name", selectedDriver)}</select><small>The Driver must accept before the trip begins.</small></label></div></div>
      <aside class="org-auto-plan"><span aria-hidden="true">✓</span><div><strong>VITAE prepares the rest</strong><p><b data-auto-temperature>${profile.min}°C to ${profile.max}°C</b> storage · target delivery within <b data-auto-hours>${profile.hours} hours</b> · refrigerated vehicle and sensor assigned automatically.</p></div></aside>
      ${v2SetupFields(setup, d, contexts, availableSensors, selectedContext)}
      <details class="org-request-options"><summary>Change temperature, timing, or instructions</summary><p>Use these only when this shipment has special requirements.</p><div class="org-form-grid">${field("Minimum °C", "safeTemperatureMin", d.safeTemperatureMin ?? profile.min, "number", false, "step=\"0.1\"")}${field("Maximum °C", "safeTemperatureMax", d.safeTemperatureMax ?? profile.max, "number", false, "step=\"0.1\"")}${field("Ready for pickup", "departureAt", departure, "datetime-local")}${field("Deliver by", "expectedArrival", arrival, "datetime-local")}<label class="org-wide"><span>Special instructions</span><textarea name="handlingNotes" rows="3" placeholder="Optional">${esc(d.handlingNotes || profile.handling)}</textarea></label></div></details>
      <input name="submissionId" type="hidden" value="${esc(d.submissionId)}"><div class="org-quick-submit"><p>The request appears immediately in the selected Driver’s app.</p><button class="foundation-primary" type="submit" ${org.saving || !drivers.length ? "disabled" : ""}>${org.saving ? "Sending…" : "Send Request to Driver"}</button></div>
    </form></section>`;
  }

  function v2SetupFields(setup, draft, contexts, sensors, selectedContext) {
    const enabled = draft.v2Enabled === true;
    const unavailable = setup.status !== "ready";
    const message = setup.status === "error"
      ? setup.error || "V2 monitoring options are unavailable."
      : setup.status === "loading" || setup.status === "idle"
        ? "Loading verified product contexts and devices."
        : !contexts.length
          ? "No verified product contexts are currently available."
          : "Uses verified ProductRules and deterministic product condition monitoring.";
    return `<section class="org-v2-setup ${enabled ? "enabled" : ""}">
      <label class="org-v2-toggle">
        <input name="v2Enabled" type="checkbox" value="true" ${enabled ? "checked" : ""} ${unavailable || !contexts.length ? "disabled" : ""}>
        <span><strong>Enable V2 monitoring</strong><small>${esc(message)}</small></span>
      </label>
      ${enabled ? `<div class="org-v2-fields">
        <label><span>Product context *</span><select name="v2ContextIndex" required><option value="">Choose a verified context</option>${contexts.map((context, index) => `<option value="${index}" ${String(index) === String(draft.v2ContextIndex) ? "selected" : ""}>${esc(`${context.productName} - ${human(context.presentation)} - ${human(context.state)}`)}</option>`).join("")}</select></label>
        <label><span>Lot ID *</span><input name="v2LotId" value="${esc(draft.v2LotId)}" required maxlength="100" placeholder="Manufacturer lot identifier"></label>
        <label><span>Device *</span><select name="v2DeviceId" required><option value="">Choose an available sensor</option>${sensors.map((sensor) => `<option value="${esc(sensor.sensorId)}" ${sensor.sensorId === draft.v2DeviceId ? "selected" : ""}>${esc(`${sensor.sensorId} - ${human(sensor.status)} - ${sensor.batteryLevel ?? "?"}% battery`)}</option>`).join("")}</select>${sensors.length ? "" : "<small>No available sensors. Legacy shipment creation remains available.</small>"}</label>
        <label><span>Rule version</span><input value="${esc(selectedContext?.productRuleVersion || "Select a product context")}" readonly aria-readonly="true"><small>Verified and pinned by the backend.</small></label>
      </div>` : ""}
    </section>`;
  }

  function shipmentsPage(state, org) {
    const f = org.filters, all = state.data.shipments || [], drivers = state.data.drivers || [];
    const filtered = all.filter((item) => (!f.search || JSON.stringify(item).toLowerCase().includes(f.search.toLowerCase())) && (!f.status || item.status === f.status) && (!f.risk || item.riskLevel === f.risk) && (!f.category || item.productCategory === f.category) && (!f.driver || item.driverId === f.driver));
    if (f.sort === "eta") filtered.sort((a, b) => String(a.expectedArrival || "").localeCompare(String(b.expectedArrival || "")));
    if (f.sort === "risk") filtered.sort((a, b) => ({ critical: 0, high: 1, medium: 2, low: 3 }[a.riskLevel] ?? 4) - ({ critical: 0, high: 1, medium: 2, low: 3 }[b.riskLevel] ?? 4));
    return `<section class="foundation-panel"><div class="org-filters"><input data-org-filter="search" value="${esc(f.search)}" placeholder="Search shipments" aria-label="Search shipments"><select data-org-filter="status"><option value="">All statuses</option>${options(unique(all, "status"), f.status)}</select><select data-org-filter="risk"><option value="">All risk levels</option>${options(unique(all, "riskLevel"), f.risk)}</select><select data-org-filter="category"><option value="">All categories</option>${options(unique(all, "productCategory"), f.category)}</select><select data-org-filter="driver"><option value="">All drivers</option>${recordOptions(drivers, "driverId", "name", f.driver)}</select><select data-org-filter="sort">${options(["newest", "eta", "risk"], f.sort || "newest")}</select></div>${shipmentRows(filtered)}${selectedShipment(state, org)}</section>`;
  }

  function shipmentRows(items, compact = false) {
    const ui = window.VitaeUI;
    if (!items.length) return ui.empty("No shipments match this view.");
    return `<div class="org-shipment-table ${compact ? "compact" : ""}"><div class="org-table-head"><span>Shipment / product</span><span>Route</span><span>Driver</span><span>Condition</span><span>Condition / status</span><span>ETA</span></div>${items.map((item) => `<button class="org-table-row" data-org-action="shipment-detail" data-id="${esc(item.shipmentId)}" type="button"><span><strong>${esc(item.shipmentId)}</strong><small>${esc(item.productName || item.productCategory)} · ${esc(item.productCategory)}</small>${v2Lifecycle(item)}</span><span><strong>${esc(item.origin)}</strong><small>to ${esc(item.destinationHospitalName)}</small></span><span>${esc(item.driverName || item.driverId || "Unassigned")}</span><span><strong>${temperature(item.temperature)}</strong><small>${range(item)}</small></span><span>${ui.badge(item.conditionStatus || item.riskLevel)} ${ui.badge(item.status)}</span><span>${dateTime(item.expectedArrival)}</span></button>`).join("")}</div>`;
  }

  function selectedShipment(state, org) {
    const item = (state.data.shipments || []).find((entry) => entry.shipmentId === org.selectedShipmentId);
    if (!item) return "";
    const canAssign = ["planned", "pending"].includes(item.status), verify = item.status === "awaiting_verification";
    return `<div class="org-modal-backdrop" data-org-modal-backdrop><section class="org-detail" role="dialog" aria-modal="true" aria-label="Shipment details"><header><div><span class="foundation-eyebrow">Shipment ${esc(item.shipmentId)}</span><h2>${esc(item.productName)}</h2><div>${window.VitaeUI.badge(item.status)} ${window.VitaeUI.badge(item.conditionStatus || item.riskLevel)}</div></div><div class="org-row-actions"><button class="foundation-primary" data-org-action="map" data-id="${esc(item.shipmentId)}" type="button">Open route</button><button data-org-action="close-detail" type="button">Close</button></div></header>
      <div class="org-detail-grid">${v2IdentityPanel(item)}<section><h3>Current condition</h3><dl>${item.lotTripId ? detail("Deterministic status", item.conditionStatus || "No telemetry yet") : ""}${detail("Temperature", temperature(item.temperature))}${detail("Required range", range(item))}${detail("Battery", item.batteryLevel == null ? "Unavailable" : `${item.batteryLevel}%`)}${detail("ETA", dateTime(item.expectedArrival))}${detail("Remaining safe time", item.remainingSafeTime || "Not available")}</dl></section><section><h3>Route</h3><dl>${detail("Origin", item.origin)}${detail("Destination", item.destinationHospitalName)}${detail("Current location", item.currentLocation || "GPS unavailable")}</dl><button class="foundation-secondary" data-org-action="map" data-id="${esc(item.shipmentId)}" type="button">Open Google Maps</button></section>
      <section><h3>Temperature history</h3>${temperatureHistory(item)}</section><section><h3>AI / risk analysis</h3>${window.VitaeUI.badge(item.conditionStatus || item.riskLevel)}<p>${esc(item.conditionReasonCode ? human(item.conditionReasonCode) : (item.riskExplanation || []).join(" ") || "No risk explanation is available.")}</p><strong>Recommended action</strong><p>${esc(item.recommendedAction)}</p></section><section><h3>Timeline</h3>${timeline(item.timeline || [])}</section><section><h3>Assignment and handoff</h3><dl>${detail("Driver", item.driverName)}${detail("Vehicle / container", item.vehicleId || item.containerId)}${detail("Sensor", item.sensorId)}${detail("Destination confirmation", human(item.destinationVerificationStatus || "pending"))}${item.destinationVerificationCode ? detail("Destination handoff code", item.destinationVerificationCode) : ""}</dl>${item.destinationVerificationCode ? `<p class="org-handoff-help">Share this one-time code with the receiving desk. The Driver must enter it at arrival.</p>` : ""}${canAssign ? assignmentForm(state, item) : ""}</section></div>${verify ? verificationPanel(item) : item.verification ? finalDeliveryReport(item) : ""}</section></div>`;
  }

  function trackingPage(state, org) {
    const active = state.live.shipments || [], selected = active.find((item) => item.shipmentId === org.selectedTrackingId) || active[0];
    if (state.live.status === "error") return `<section class="foundation-panel">${window.VitaeUI.empty(`Live tracking unavailable: ${state.live.error}`)}</section>`;
    if (!active.length) return `<section class="foundation-panel">${window.VitaeUI.empty("No active shipments are currently reporting.")}</section>`;
    org.selectedTrackingId ||= selected.shipmentId;
    const delayed = selected.lastUpdated && Date.now() - new Date(selected.lastUpdated).getTime() > 15 * 60 * 1000;
    return `<div class="org-tracking"><section class="foundation-panel org-map-stage"><header><div><span class="foundation-eyebrow">Selected route</span><h2>${esc(selected.shipmentId)}</h2></div><button class="foundation-primary" data-org-action="map-live" data-id="${esc(selected.shipmentId)}" type="button">Open route map</button></header><div class="org-route-canvas"><span>${selected.currentGps ? "Live position available" : "GPS position unavailable"}</span><strong>${esc(selected.origin || "Origin")}</strong><i></i><strong>${esc(selected.destinationHospitalName || "Destination")}</strong><small>${esc(selected.currentLocation || "No current location")}</small></div>${delayed ? `<p class="org-inline-error">Telemetry update is delayed.</p>` : ""}</section><aside class="foundation-panel org-track-list"><h2>Active shipments</h2>${active.map((item) => `<button class="${item.shipmentId === selected.shipmentId ? "active" : ""}" data-org-action="select-tracking" data-id="${esc(item.shipmentId)}" type="button"><strong>${esc(item.shipmentId)}</strong><span>${temperature(item.temperature)} · ${esc(item.driverName || item.driverId)}</span>${window.VitaeUI.badge(item.riskLevel)}</button>`).join("")}<dl>${detail("ETA", dateTime(selected.expectedArrival))}${detail("Current temperature", temperature(selected.temperature))}${detail("Required range", range(selected))}${detail("Battery", selected.batteryLevel == null ? "Unavailable" : `${selected.batteryLevel}%`)}${detail("Sensor", selected.sensorStatus || "Unknown")}${detail("Driver", selected.driverName || selected.driverId)}${detail("Last update", dateTime(selected.lastUpdated))}</dl></aside></div>`;
  }

  function alertsPage(state) {
    const mappedShipmentIds = new Set(
      (state.data.shipments || [])
        .filter((shipment) => shipment.lotTripId)
        .map((shipment) => shipment.shipmentId),
    );
    const legacyItems = (state.data.alerts || []).filter(
      (alert) => !mappedShipmentIds.has(alert.shipmentId),
    );
    const v2 = state.v2Alerts || { status: "idle", alerts: [] };
    return `${v2AlertSection(v2)}<section class="foundation-panel"><header><div><span class="foundation-eyebrow">Legacy shipments</span><h2>Legacy Alerts</h2></div></header><div class="org-alert-list">${legacyItems.length ? legacyItems.map((item) => `<article><header><div><strong>${esc(item.type)}</strong><span>${esc(item.shipmentId)} · ${dateTime(item.detectedAt)}</span></div>${window.VitaeUI.badge(item.severity)}</header><p>${esc(item.explanation)}</p><dl>${detail("Recommended action", item.recommendedAction)}${detail("Status", human(item.status))}${detail("Driver response", item.driverResponse)}${detail("Last update", dateTime(item.updatedAt))}</dl><div class="org-row-actions">${alertActions(item)}</div></article>`).join("") : window.VitaeUI.empty("No legacy alerts for this organization.")}</div></section>`;
  }

  function v2AlertSection(v2) {
    let content;
    if (v2.status === "error") content = `<p class="org-inline-error" role="alert">${esc(v2.error || "V2 alerts are unavailable.")}</p>`;
    else if (v2.status === "loading" || v2.status === "idle") content = window.VitaeUI.empty("Loading V2 alerts.");
    else content = v2.alerts.length ? v2.alerts.map(v2AlertCard).join("") : window.VitaeUI.empty("No V2 alert history for monitored shipments.");
    return `<section class="foundation-panel"><header><div><span class="foundation-eyebrow">Deterministic pipeline</span><h2>V2 Alerts</h2></div></header><div class="org-alert-list">${content}</div></section>`;
  }

  function v2AlertCard(item) {
    const resolved = item.status === "RESOLVED";
    return `<article data-v2-alert-id="${esc(item.alertId)}"><header><div><strong>${esc(human(item.alertType))}</strong><span>${esc(item.shipmentId)} · ${dateTime(item.detectedAt)}</span></div><div class="vitae-status-stack">${window.VitaeUI.badge(item.severity)}${window.VitaeUI.badge(item.status)}</div></header><p>${esc(item.message)}</p><dl>${detail("Product condition at detection", human(item.sourceStatus))}${detail("Reason", human(item.reasonCode))}${detail("Recommended action", item.recommendedAction)}${detail("Recorded actions", item.actions?.length || 0)}${detail("Last update", dateTime(item.updatedAt))}</dl>${resolved ? `<span class="org-action-complete">Resolved · retained in history</span>` : `<div class="org-row-actions">${item.status === "OPEN" ? `<button data-org-action="v2-alert-ack" data-lot-trip-id="${esc(item.lotTripId)}" data-id="${esc(item.alertId)}" type="button">Acknowledge</button>` : ""}<details><summary>Record action</summary><form class="v2-alert-command-form" data-org-form="v2-alert-action" data-lot-trip-id="${esc(item.lotTripId)}" data-id="${esc(item.alertId)}"><label><span>Action taken</span><input name="description" required maxlength="300"></label><button type="submit">Save action</button></form></details><details><summary>Resolve</summary><form class="v2-alert-command-form" data-org-form="v2-alert-resolve" data-lot-trip-id="${esc(item.lotTripId)}" data-id="${esc(item.alertId)}"><label><span>Resolution note</span><input name="resolutionNote" required maxlength="300"></label><button type="submit">Resolve alert</button></form></details></div>`}</article>`;
  }

  function driversPage(state, org) {
    const f = org.filters, items = (state.data.drivers || []).filter((item) => (!f.driverSearch || JSON.stringify(item).toLowerCase().includes(f.driverSearch.toLowerCase())) && (!f.availability || item.status === f.availability) && (!f.assignment || (f.assignment === "assigned") === Boolean(item.currentAssignment)));
    return `<section class="foundation-panel"><div class="org-filters driver"><input data-org-filter="driverSearch" value="${esc(f.driverSearch)}" placeholder="Search drivers" aria-label="Search drivers"><select data-org-filter="availability"><option value="">All availability</option>${options(["available", "assigned", "on_route", "unavailable"], f.availability)}</select><select data-org-filter="assignment"><option value="">All assignments</option>${options(["assigned", "unassigned"], f.assignment)}</select></div><div class="org-driver-grid">${items.map((item) => `<article><header><div><strong>${esc(item.name)}</strong><span>${esc(item.username || item.driverId)}</span></div>${window.VitaeUI.badge(item.status)}</header><dl>${detail("Current assignment", item.currentAssignment || "None")}${detail("Contact", item.phone || "Not provided")}${detail("Completed deliveries", item.completedDeliveries ?? 0)}${detail("Vehicle", item.vehicleId || "Not assigned")}</dl><div class="org-row-actions"><button data-org-action="driver-detail" data-id="${esc(item.driverId)}" type="button">View details</button>${!item.currentAssignment ? `<button data-org-action="driver-availability" data-status="${item.status === "available" ? "unavailable" : "available"}" data-id="${esc(item.driverId)}" type="button">Mark ${item.status === "available" ? "unavailable" : "available"}</button>` : ""}</div>${org.selectedDriverId === item.driverId ? `<div class="org-expanded"><h3>Delivery history</h3>${shipmentRows(item.deliveryHistory || [], true)}${eligibleAssignmentForm(state, item)}</div>` : ""}</article>`).join("")}</div></section>`;
  }

  function reportsPage(r) {
    const metrics = [["Total shipments", r.totalShipments], ["Active shipments", r.activeShipments], ["Completed shipments", r.completedShipments], ["Safe shipments", r.safeShipments], ["At-risk shipments", r.atRiskShipments], ["Critical shipments", r.criticalShipments], ["On-time delivery", r.onTimePercentage == null ? "Not calculable" : `${r.onTimePercentage}%`], ["Value protected", money(r.estimatedValueProtected)]];
    return `<section class="org-report-grid">${metrics.map(([label, value]) => `<article class="foundation-panel"><span>${esc(label)}</span><strong>${esc(value ?? 0)}</strong></article>`).join("")}<section class="foundation-panel org-report-wide"><h2>Driver delivery summary</h2>${(r.driverSummary || []).map((item) => `<div class="org-report-row"><span>${esc(item.name)}</span><strong>${esc(item.completed)} completed</strong></div>`).join("") || window.VitaeUI.empty("No completed driver deliveries.")}</section><section class="foundation-panel"><h2>Shipment outcomes</h2>${distributionFromObject(r.shipmentOutcomes)}</section><section class="foundation-panel"><h2>Alert summary</h2>${distributionFromObject(r.alertSummary)}</section></section>`;
  }

  function supportPage(state, org) {
    const tickets = state.data.tickets || [], selected = tickets.find((item) => item.ticketId === org.selectedTicketId);
    return `<div class="org-support-grid"><section class="foundation-panel"><header><div><span class="foundation-eyebrow">New request</span><h2>Create support ticket</h2></div></header><form class="org-section-form compact" data-org-form="ticket"><label><span>Problem category *</span><select name="category" required>${options(["sensor_problem", "temperature_alert", "cooling_problem", "shipment_issue", "driver_issue", "login_or_account_issue", "incorrect_data", "other"])}</select></label><label><span>Urgency *</span><select name="urgency" required>${options(["low", "medium", "high", "critical"], "medium")}</select></label><label><span>Link shipment</span><select name="shipmentId"><option value="">None</option>${recordOptions(state.data.shipments || [], "shipmentId", "shipmentId")}</select></label><label><span>Link alert</span><select name="alertId"><option value="">None</option>${recordOptions(state.data.alerts || [], "alertId", "alertId")}</select></label><label><span>Subject</span><input name="subject"></label><label><span>Description *</span><textarea name="description" rows="4" required></textarea></label><button class="foundation-primary" type="submit">Create ticket</button></form></section><section class="foundation-panel"><header><div><span class="foundation-eyebrow">Organization tickets</span><h2>Support conversations</h2></div></header><div class="org-ticket-list">${tickets.map((item) => `<button data-org-action="ticket-detail" data-id="${esc(item.ticketId)}" type="button"><span><strong>${esc(item.subject)}</strong><small>${esc(item.ticketId)} · ${dateTime(item.updatedAt)}</small></span>${window.VitaeUI.badge(item.status)}</button>`).join("") || window.VitaeUI.empty("No support tickets.")}</div>${selected ? ticketDetail(selected) : ""}</section></div>`;
  }

  function ticketDetail(item) { return `<div class="org-ticket-detail"><h3>${esc(item.subject)}</h3><p>${esc(item.summary)}</p><div class="org-messages">${(item.messages || []).map((message) => `<article><strong>${esc(message.author)}</strong><time>${dateTime(message.timestamp)}</time><p>${esc(message.body)}</p></article>`).join("") || `<p>No public replies yet.</p>`}</div><form data-org-form="ticket-reply" data-id="${esc(item.ticketId)}"><label><span>Reply to Support</span><textarea name="message" rows="3" required></textarea></label><button class="foundation-primary" type="submit">Send reply</button></form></div>`; }

  async function handleClick(event, state, actions) {
    if (event.target.matches("[data-org-modal-backdrop]")) {
      orgState(state).selectedShipmentId = null;
      actions.render();
      return true;
    }
    const button = event.target.closest("[data-org-action]");
    if (!button) return false;
    const org = orgState(state), action = button.dataset.orgAction, id = button.dataset.id;
    if (action === "back-step") { org.step = Math.max(1, org.step - 1); org.formError = ""; actions.render(); return true; }
    if (action === "shipment-detail") { org.selectedShipmentId = id; org.selectedTrackingId = null; state.page = "shipments"; await actions.selectV2Target(id); actions.render(); return true; }
    if (action === "close-detail") { org.selectedShipmentId = null; await actions.selectV2Target(null); actions.render(); return true; }
    if (action === "select-tracking") { org.selectedTrackingId = id; org.selectedShipmentId = null; await actions.selectV2Target(id); actions.render(); return true; }
    if (action === "driver-detail") { org.selectedDriverId = org.selectedDriverId === id ? null : id; actions.render(); return true; }
    if (action === "ticket-detail") { org.selectedTicketId = id; actions.render(); return true; }
    if (["map", "map-live"].includes(action)) { actions.openMap(findShipment(state, id)); return true; }
    if (action === "alert-notify") return perform(async () => { await api(`/api/organization/alerts/${id}`, "PATCH", { status: "acknowledged", driverResponse: "Driver notified by organization operations." }); await actions.reload(); actions.notify("Driver notification recorded."); }, actions);
    if (action === "alert-update") return perform(async () => { const status = button.dataset.status; if (status === "escalated" && !confirm("Escalate this alert to Support and create a linked ticket?")) return; await api(`/api/organization/alerts/${id}`, "PATCH", { status }); await actions.reload(); actions.notify(status === "escalated" ? "Alert escalated and linked to Support." : `Alert marked ${human(status).toLowerCase()}.`); }, actions);
    if (action === "v2-alert-ack") return perform(async () => { await actions.v2AlertCommand(button.dataset.lotTripId, id, "acknowledge"); actions.notify("V2 alert acknowledged."); }, actions);
    if (action === "driver-availability") return perform(async () => { await api(`/api/organization/drivers/${id}`, "PATCH", { status: button.dataset.status }); await actions.reload(); actions.notify("Driver availability updated."); }, actions);
    return true;
  }

  async function handleSubmit(event, state, actions) {
    const form = event.target, org = orgState(state);
    if (form.dataset.orgForm === "quick-request") {
      event.preventDefault();
      const formValues = Object.fromEntries(new FormData(form).entries());
      const v2Enabled = form.elements.v2Enabled?.checked === true;
      Object.assign(org.draft, formValues, { v2Enabled });
      const payload = { ...formValues };
      delete payload.v2Enabled;
      delete payload.v2ContextIndex;
      delete payload.v2LotId;
      delete payload.v2DeviceId;
      org.formError = "";
      if (v2Enabled) {
        const setup = state.v2ShipmentOptions || {};
        const context = formValues.v2ContextIndex === undefined || formValues.v2ContextIndex === ""
          ? null
          : (setup.productContexts || [])[Number(formValues.v2ContextIndex)];
        const sensor = (setup.sensors || []).find(
          (item) => item.sensorId === formValues.v2DeviceId && isAvailableSensor(item),
        );
        if (!context) { org.formError = "Choose a verified product context."; actions.render(); return true; }
        if (!String(formValues.v2LotId || "").trim()) { org.formError = "Enter the manufacturer lot ID."; actions.render(); return true; }
        if (!sensor) { org.formError = "Choose an available sensor for V2 monitoring."; actions.render(); return true; }
        payload.productName = context.productName;
        payload.sensorId = sensor.sensorId;
        payload.v2Monitoring = {
          enabled: true,
          productId: context.productId,
          presentation: context.presentation,
          state: context.state,
          lotId: String(formValues.v2LotId).trim(),
          deviceId: sensor.sensorId,
        };
      }
      if (payload.originFacilityId === payload.destinationFacilityId) { org.formError = "Pickup and destination must be different."; actions.render(); return true; }
      if (payload.safeTemperatureMin !== "" && payload.safeTemperatureMax !== "" && Number(payload.safeTemperatureMin) >= Number(payload.safeTemperatureMax)) { org.formError = "Minimum temperature must be below maximum temperature."; actions.render(); return true; }
      if (payload.departureAt && payload.expectedArrival && new Date(payload.expectedArrival) <= new Date(payload.departureAt)) { org.formError = "The delivery deadline must be after the pickup time."; actions.render(); return true; }
      if (org.saving) return true;
      org.saving = true;
      const button = form.querySelector('button[type="submit"]'), feedback = form.querySelector(".org-request-feedback");
      button.disabled = true; button.textContent = "Sending request…"; feedback.textContent = "Sending the request to the Driver.";
      return perform(async () => { const result = await api("/api/organization/shipments", "POST", payload); org.draft = { submissionId: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}` }; org.saving = false; await actions.reload(); await actions.reloadV2Options(); await actions.refreshLive(); state.page = "shipments"; actions.render(); actions.notify(result.created === false ? "This request already exists; no duplicate was added." : "Request sent. Waiting for the Driver to accept it."); }, actions, (error) => { org.saving = false; feedback.textContent = ""; button.disabled = false; button.textContent = "Send Request to Driver"; org.formError = error.message; const errorBox = form.querySelector(".org-inline-error") || document.createElement("p"); errorBox.className = "org-inline-error"; errorBox.setAttribute("role", "alert"); errorBox.textContent = error.message; if (!errorBox.parentNode) form.querySelector("header").after(errorBox); });
    }
    if (form.dataset.orgForm === "assign") { event.preventDefault(); const payload = Object.fromEntries(new FormData(form).entries()); return perform(async () => { await api(`/api/organization/shipments/${form.dataset.id}/driver`, "PATCH", payload); await actions.reload(); actions.notify("Driver assignment updated."); }, actions); }
    if (form.dataset.orgForm === "verify") { event.preventDefault(); const payload = Object.fromEntries(new FormData(form).entries()); if (!confirm(`Confirm final decision: ${payload.decision}?`)) return true; return perform(async () => { await api(`/api/organization/shipments/${form.dataset.id}/verification`, "PATCH", payload); org.selectedShipmentId = null; await actions.reload(); actions.notify("Delivery verification recorded."); }, actions); }
    if (form.dataset.orgForm === "ticket") { event.preventDefault(); const payload = Object.fromEntries(new FormData(form).entries()); return perform(async () => { const result = await api("/api/organization/tickets", "POST", payload); org.selectedTicketId = result.ticket.ticketId; await actions.reload(); actions.notify("Support ticket created."); }, actions); }
    if (form.dataset.orgForm === "ticket-reply") { event.preventDefault(); const payload = Object.fromEntries(new FormData(form).entries()); return perform(async () => { await api(`/api/organization/tickets/${form.dataset.id}/messages`, "POST", payload); await actions.reload(); actions.notify("Public reply sent to Support."); }, actions); }
    if (form.dataset.orgForm === "v2-alert-action") { event.preventDefault(); const payload = Object.fromEntries(new FormData(form).entries()); return perform(async () => { await actions.v2AlertCommand(form.dataset.lotTripId, form.dataset.id, "action", payload); actions.notify("V2 alert action recorded."); }, actions); }
    if (form.dataset.orgForm === "v2-alert-resolve") { event.preventDefault(); const payload = Object.fromEntries(new FormData(form).entries()); return perform(async () => { await actions.v2AlertCommand(form.dataset.lotTripId, form.dataset.id, "resolve", payload); actions.notify("V2 alert resolved."); }, actions); }
    return false;
  }

  function handleFilter(event, state, actions) {
    const control = event.target.closest("[data-org-filter]");
    if (!control) {
      if (["v2Enabled", "v2ContextIndex", "v2LotId", "v2DeviceId"].includes(event.target.name)) {
        const org = orgState(state), form = event.target.form;
        const values = Object.fromEntries(new FormData(form).entries());
        Object.assign(org.draft, values, {
          v2Enabled: form.elements.v2Enabled?.checked === true,
        });
        if (event.target.name === "v2ContextIndex") {
          const context = event.target.value === ""
            ? null
            : (state.v2ShipmentOptions?.productContexts || [])[Number(event.target.value)];
          org.draft.productName = context?.productName || "";
        }
        if (["v2Enabled", "v2ContextIndex"].includes(event.target.name)) actions.render();
        return true;
      }
      if (event.target.name === "productCategory" && PRODUCT_PROFILES[event.target.value]) {
        const form = event.target.form, profile = PRODUCT_PROFILES[event.target.value];
        if (form?.elements.safeTemperatureMin) form.elements.safeTemperatureMin.value = profile.min;
        if (form?.elements.safeTemperatureMax) form.elements.safeTemperatureMax.value = profile.max;
        if (form?.elements.handlingNotes) form.elements.handlingNotes.value = profile.handling;
        const departure = form?.elements.departureAt?.value ? new Date(form.elements.departureAt.value) : new Date(Date.now() + 15 * 60 * 1000);
        if (form?.elements.departureAt && !form.elements.departureAt.value) form.elements.departureAt.value = toLocalInput(departure);
        if (form?.elements.expectedArrival) form.elements.expectedArrival.value = toLocalInput(new Date(departure.getTime() + profile.hours * 60 * 60 * 1000));
        const temperature = form?.querySelector("[data-auto-temperature]"), hours = form?.querySelector("[data-auto-hours]");
        if (temperature) temperature.textContent = `${profile.min}°C to ${profile.max}°C`;
        if (hours) hours.textContent = `${profile.hours} hours`;
      }
      return false;
    }
    orgState(state).filters[control.dataset.orgFilter] = control.value;
    if (event.type === "change" || control.tagName !== "INPUT") actions.render();
    else window.clearTimeout(control._filterTimer), control._filterTimer = window.setTimeout(actions.render, 180);
    return true;
  }

  function assignmentForm(state, item) { const eligible = (state.data.drivers || []).filter((driver) => ["available", "assigned"].includes(driver.status)); return `<form class="org-inline-form" data-org-form="assign" data-id="${esc(item.shipmentId)}"><label><span>Assign before departure</span><select name="driverId" required>${recordOptions(eligible, "driverId", "name", item.driverId)}</select></label><button type="submit">Update driver</button></form>`; }
  function v2Lifecycle(item) { return item?.lotTripId && item?.tripStatus ? `<small class="vitae-v2-lifecycle"><span>V2 trip</span>${window.VitaeUI.badge(item.tripStatus)}</small>` : ""; }
  function v2IdentityPanel(item) { return item?.lotTripId ? `<section><h3>V2 monitoring identity</h3><dl>${detail("Trip lifecycle", human(item.tripStatus))}${detail("Lot trip ID", item.lotTripId)}${detail("Trip ID", item.tripId)}${detail("Rule version", item.productRuleVersion)}</dl></section>` : ""; }
  function eligibleAssignmentForm(state, driver) { const shipments = (state.data.shipments || []).filter((item) => ["planned", "pending"].includes(item.status)); return shipments.length ? `<form class="org-inline-form" data-org-form="assign" data-id="${esc(shipments[0].shipmentId)}"><label><span>Assign ${esc(driver.name)} to an eligible shipment</span><select name="driverId"><option value="${esc(driver.driverId)}">${esc(shipments[0].shipmentId)} · ${esc(shipments[0].productName)}</option></select></label><button type="submit">Assign</button></form>` : ""; }
  function verificationPanel(item) { return `<section class="org-verification"><span class="foundation-eyebrow">Destination confirmed</span><h3>Review and finalize the delivery</h3><dl>${detail("Driver", item.driverName)}${detail("Destination handoff", human(item.destinationVerificationStatus))}${detail("Handoff confirmed", dateTime(item.destinationVerifiedAt))}${detail("Arrival time", dateTime(item.arrivalTime))}${detail("Receiver", item.receiverName || "Not recorded")}${detail("Delivery notes", item.deliveryNotes || "None")}${detail("Temperature violations", item.alerts?.length || 0)}${detail("Driver actions", (item.driverActions || []).join(", ") || "None recorded")}</dl>${signatureEvidence(item.receiverSignature)}<form data-org-form="verify" data-id="${esc(item.shipmentId)}"><label><span>Final decision *</span><select name="decision" required>${options(["accept", "flag", "reject"])}</select></label><label><span>Verification notes</span><textarea name="notes" rows="3" placeholder="Required for flag or rejection"></textarea></label><button class="foundation-primary" type="submit">Finalize Delivery</button></form></section>`; }
  function finalDeliveryReport(item) { const v = item.verification; return `<section class="org-verification"><span class="foundation-eyebrow">Final delivery report</span><h3>${esc(human(v.decision))} delivery</h3><dl>${detail("Shipment", item.shipmentId)}${detail("Driver", item.driverName)}${detail("Destination handoff", human(item.destinationVerificationStatus))}${detail("Receiver", item.receiverName || "Not recorded")}${detail("Organization user", v.userName)}${detail("Decision time", dateTime(v.timestamp))}${detail("Arrival temperature", temperature(item.temperature))}${detail("Required range", range(item))}${detail("Notes", v.notes || "No notes")}</dl>${signatureEvidence(item.receiverSignature)}</section>`; }
  function signatureEvidence(value) { return value?.startsWith("data:image/") ? `<div class="org-signature-evidence"><span>Receiver signature</span><img src="${esc(value)}" alt="Receiver signature"></div>` : `<div class="org-signature-evidence"><span>Receiver signature</span><p>Not provided</p></div>`; }

  function formActions(back, next, disabled = false) { return `<div class="org-form-actions">${back ? `<button data-org-action="back-step" class="foundation-secondary" type="button">Back</button>` : ""}<button class="foundation-primary" type="submit" ${disabled ? "disabled" : ""}>${esc(next)}</button></div>`; }
  function field(label, name, value, type = "text", required = false, attrs = "") { return `<label><span>${esc(label)}${required ? " *" : ""}</span><input name="${esc(name)}" type="${esc(type)}" value="${esc(value)}" ${required ? "required" : ""} ${attrs}></label>`; }
  function options(items, selected = "") { return (items || []).map((item) => `<option value="${esc(item)}" ${String(item) === String(selected) ? "selected" : ""}>${esc(human(item))}</option>`).join(""); }
  function recordOptions(items, key, label, selected = "") { return (items || []).map((item) => `<option value="${esc(item[key])}" ${String(item[key]) === String(selected) ? "selected" : ""}>${esc(item[label])}</option>`).join(""); }
  function detail(label, value) { return `<div><dt>${esc(label)}</dt><dd>${esc(value ?? "—")}</dd></div>`; }
  function timeline(items) { return items.length ? `<div class="org-timeline">${items.slice().reverse().slice(0, 8).map((item) => `<article><i></i><div><strong>${esc(item.label)}</strong><time>${dateTime(item.timestamp)}</time></div></article>`).join("")}</div>` : window.VitaeUI.empty("No activity recorded."); }
  function driverBrief(items) { return items.length ? `<div class="org-driver-brief">${items.map((item) => `<button data-role-page="drivers" type="button"><span><strong>${esc(item.name)}</strong><small>${esc(item.currentAssignment || "No active assignment")}</small></span>${window.VitaeUI.badge(item.status)}</button>`).join("")}</div>` : window.VitaeUI.empty("No drivers available."); }
  function distribution(items, field) { const counts = {}; items.forEach((item) => counts[item[field]] = (counts[item[field]] || 0) + 1); return distributionFromObject(counts); }
  function distributionFromObject(counts = {}) { const max = Math.max(1, ...Object.values(counts)); return `<div class="org-distribution">${Object.entries(counts).map(([label, value]) => `<div><span>${esc(human(label))}</span><i><b style="width:${value / max * 100}%"></b></i><strong>${esc(value)}</strong></div>`).join("") || window.VitaeUI.empty("No data available.")}</div>`; }
  function temperatureHistory(item) { const history = item.temperatureHistory || []; return history.length ? `<div class="org-temp-history">${history.map((entry) => `<div><time>${dateTime(entry.timestamp)}</time><i class="${entry.value < item.safeTemperatureMin || entry.value > item.safeTemperatureMax ? "outside" : ""}" style="height:${Math.max(15, Math.min(80, 24 + Number(entry.value || 0) * 2))}px"></i><strong>${temperature(entry.value)}</strong></div>`).join("")}</div><small>Required ${range(item)}</small>` : window.VitaeUI.empty("No sensor history is available."); }
  function actionButton(label, status, id) { return `<button data-org-action="alert-update" data-status="${status}" data-id="${esc(id)}" type="button">${esc(label)}</button>`; }
  function alertActions(item) {
    if (item.status === "resolved") return `<span class="org-action-complete">No further action required</span>`;
    const acknowledge = item.status === "new" ? actionButton("Acknowledge", "acknowledged", item.alertId) : "";
    const actionTaken = ["new", "acknowledged", "escalated"].includes(item.status) ? actionButton("Record action taken", "action_taken", item.alertId) : "";
    const notify = ["new", "acknowledged"].includes(item.status) ? `<button data-org-action="alert-notify" data-id="${esc(item.alertId)}" type="button">Notify driver</button>` : "";
    const escalate = item.status !== "escalated" ? actionButton("Escalate to Support", "escalated", item.alertId) : "";
    return `${acknowledge}${actionTaken}${notify}${escalate}${actionButton("Mark resolved", "resolved", item.alertId)}`;
  }
  function findShipment(state, id) { return (state.live.shipments || []).find((item) => item.shipmentId === id) || (state.data.shipments || []).find((item) => item.shipmentId === id); }
  function isAvailableSensor(sensor) { return sensor && sensor.status !== "offline" && sensor.connectionStatus !== "offline" && !sensor.shipmentId; }
  function unique(items, field) { return [...new Set(items.map((item) => item[field]).filter(Boolean))]; }
  function range(item) { return item.safeTemperatureMin == null || item.safeTemperatureMax == null ? "Not specified" : `${item.safeTemperatureMin}°C to ${item.safeTemperatureMax}°C`; }
  function temperature(value) { return value == null ? "No reading" : `${value}°C`; }
  function money(value) { return value == null ? "$0" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(Number(value)); }
  function dateTime(value) { if (!value) return "Not available"; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); }
  function compactTime(value) { if (!value) return "Recently"; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); }
  function toLocalInput(value) { const date = value instanceof Date ? value : new Date(value); if (Number.isNaN(date.getTime())) return ""; const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000); return local.toISOString().slice(0, 16); }
  function isToday(value) { if (!value) return false; const date = new Date(value), today = new Date(); return !Number.isNaN(date.getTime()) && date.getFullYear() === today.getFullYear() && date.getMonth() === today.getMonth() && date.getDate() === today.getDate(); }
  function riskRank(value) { return ({ critical: 0, high: 1, medium: 2, low: 3 }[value] ?? 4); }
  function human(value) { return window.VitaeUI.humanize(value); }
  function esc(value) { return window.VitaeUI.escape(value ?? ""); }
  async function api(url, method, body) { return window.VitaeAuth.api(url, { method, body: JSON.stringify(body) }); }
  async function perform(operation, actions, onError) { try { await operation(); } catch (error) { if (onError) onError(error); else actions.notify(error.message, "error"); } return true; }

  window.VitaeOrganization = { render, handleClick, handleSubmit, handleFilter };
})();
