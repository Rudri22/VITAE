(function () {
  const NAV = [["dashboard", "Dashboard"], ["shipments", "Shipments"], ["alerts", "Alerts"]];
  const ORG_TYPES = [["hospital", "Hospital"], ["pharmacy", "Pharmacy"], ["laboratory", "Laboratory"], ["supermarket", "Supermarket"], ["food_distributor", "Food distributor"], ["refrigerated_warehouse", "Refrigerated warehouse"]];
  const ROLES = [["admin", "Admin"], ["organization_user", "Organization User"], ["driver", "Driver"], ["support", "Support Agent"]];

  function render(state, page = "dashboard") {
    const nav = state.data.localDemoControlsEnabled ? [...NAV, ["simulation", "Demo Flow"]] : NAV;
    if (!nav.some(([id]) => id === page)) page = "dashboard";
    const ui = window.VitaeUI;
    const pageMeta = {
      dashboard: ["Operations Overview", "Monitor platform health and access key operations."],
      simulation: ["Continuous Demo Flow", "Advance one local shipment through VITAE's real monitoring and lifecycle pipeline."],
      organizations: ["Organizations", "Manage every organization connected to VITAE."],
      users: ["Platform users", "Manage access, roles, and organization assignments."],
      shipments: ["All shipments", "Monitor cold-chain movements across the platform."],
      devices: ["Devices and sensors", "Manage telemetry devices and their assignments."],
      alerts: ["Alerts", "Review and resolve cold-chain exceptions."],
      tickets: ["Support ticket oversight", "Assign and prioritize customer support work."],
      reports: ["Platform reports", "Concise operational totals from current VITAE data."],
      settings: ["Settings", "Safe MVP display, alert, and notification preferences."],
    };
    const [title, subtitle] = pageMeta[page] || pageMeta.dashboard;
    const action = page === "organizations" ? primaryAction("Add Organization", "add-organization")
      : page === "users" ? primaryAction("Add User", "add-user")
        : page === "devices" ? primaryAction("Register Sensor", "add-device") : "";
    return ui.shell({
      roleClass: `admin-foundation-shell ${page === "dashboard" ? "admin-dashboard-shell" : ""}`,
      roleLabel: "Platform Administration",
      user: state.user,
      nav,
      active: page,
      header: ui.pageHeader("Platform control", title, subtitle, action),
      content: pageContent(page, state.data, state),
    });
  }

  function pageContent(page, data, state) {
    if (page === "simulation") return localDemoPage(state);
    if (page === "organizations") return organizationsPage(data);
    if (page === "users") return usersPage(data);
    if (page === "shipments") return shipmentsPage(data);
    if (page === "devices") return devicesPage(data);
    if (page === "alerts") return alertsPage(data);
    if (page === "tickets") return ticketsPage(data);
    if (page === "reports") return reportsPage(data);
    if (page === "settings") return settingsPage(data);
    return dashboard(data);
  }

  function simulationPage(state) {
    const source = state.adminSimulation || { status: "loading" };
    if (source.status === "loading" || source.status === "idle") return `<section class="foundation-panel admin-sim-state" aria-busy="true"><span class="foundation-state-spinner" aria-hidden="true"></span><h2>Loading simulation controls</h2><p>Checking eligible active shipments and previous runs.</p></section>`;
    if (source.status === "error") return `<section class="foundation-panel admin-sim-state" role="alert"><span class="foundation-error-code">!</span><h2>Simulation Center unavailable</h2><p>${e(source.error || "The simulator could not be loaded.")}</p><button class="foundation-primary" data-admin-action="simulation-retry" type="button">Try again</button></section>`;
    const ui = state.adminSimulationUi || (state.adminSimulationUi = {});
    const simulations = source.simulations || [];
    const eligibleShipments = source.eligibleShipments || [];
    const completedRunShipments = simulations.map((item) => item.shipment ? { ...item.shipment, eligible: false } : null).filter(Boolean);
    const shipments = [...eligibleShipments, ...completedRunShipments.filter((item) => !eligibleShipments.some((eligible) => eligible.shipmentId === item.shipmentId))];
    const selectedId = shipments.some((item) => item.shipmentId === ui.shipmentId) ? ui.shipmentId : shipments[0]?.shipmentId || "";
    const scenarioId = (source.scenarios || []).some((item) => item.id === ui.scenario) ? ui.scenario : "normal_delivery";
    const speedId = (source.speeds || []).some((item) => item.id === ui.speed) ? ui.speed : "normal";
    ui.shipmentId = selectedId;
    ui.scenario = scenarioId;
    ui.speed = speedId;
    const selected = shipments.find((item) => item.shipmentId === selectedId);
    const current = [...simulations].reverse().find((item) => item.shipmentId === selectedId) || simulations[simulations.length - 1];
    const reading = current?.lastGeneratedReading;
    const currentShipment = current?.shipment;
    const status = current?.status || "Ready";
    const active = ["Running", "Paused"].includes(status);
    const canReset = current && ["Stopped", "Completed", "Failed"].includes(status);
    const progress = Math.max(0, Math.min(100, Number(currentShipment?.routeProgress || selected?.routeProgress || 0)));
    return `<div class="admin-simulation-layout">
      <section class="foundation-panel admin-sim-control">
        <header><div><span class="foundation-eyebrow">Admin-only demo tool</span><h2>Configure a simulation</h2><p>Only active, assigned shipments with a registered sensor and known route are available.</p></div>${badge(status, status.toLowerCase())}</header>
        ${shipments.length ? `<form class="foundation-form admin-sim-form" data-admin-form="simulation-start">
          <label><span>Active shipment</span><select name="shipmentId" data-simulation-control="shipmentId" required>${shipments.map((item) => `<option value="${attr(item.shipmentId)}" ${item.shipmentId === selectedId ? "selected" : ""}>${e(item.shipmentId)} — ${e(item.organizationName)} (${e(item.driverName)})${item.eligible === false ? " · reset required" : ""}</option>`).join("")}</select></label>
          <label><span>Scenario</span><select name="scenario" data-simulation-control="scenario" required>${(source.scenarios || []).map((item) => `<option value="${attr(item.id)}" ${item.id === scenarioId ? "selected" : ""}>${e(item.name)}</option>`).join("")}</select></label>
          <label><span>Playback speed</span><select name="speed" data-simulation-control="speed" required>${(source.speeds || []).map((item) => `<option value="${attr(item.id)}" ${item.id === speedId ? "selected" : ""}>${e(item.name)} · every ${e(item.intervalSeconds)}s</option>`).join("")}</select></label>
          <div class="admin-sim-shipment-summary">${selected ? `${detail("Shipment", selected.shipmentId)}${detail("Product", selected.product)}${detail("Organization", selected.organizationName)}${detail("Origin", selected.origin)}${detail("Destination", selected.destination)}${detail("Assigned Driver", selected.driverName)}${detail("Assigned sensor", selected.sensorId)}${detail("Required temperature", `${selected.safeTemperatureMin}–${selected.safeTemperatureMax}°C`)}${detail("Current temperature", selected.temperature != null ? `${Number(selected.temperature).toFixed(1)}°C` : "Unavailable")}${detail("Current battery", selected.batteryLevel != null ? `${Number(selected.batteryLevel).toFixed(0)}%` : "Unavailable")}${detail("Current risk", h(selected.riskClassification))}${detail("Trip status", h(selected.status))}` : ""}</div>
          <p class="admin-form-feedback" role="status"></p>
          <div class="admin-sim-actions"><button class="foundation-primary" type="submit" ${active || selected?.eligible === false ? "disabled" : ""}>Start simulation</button>${current ? `${status === "Running" ? `<button class="foundation-secondary" data-admin-action="simulation-pause" data-id="${attr(current.simulationId)}" type="button">Pause</button>` : ""}${status === "Paused" ? `<button class="foundation-secondary" data-admin-action="simulation-resume" data-id="${attr(current.simulationId)}" type="button">Resume</button>` : ""}${active ? `<button class="foundation-secondary danger" data-admin-action="simulation-stop" data-id="${attr(current.simulationId)}" type="button">Stop</button>` : ""}${canReset ? `<button class="foundation-secondary" data-admin-action="simulation-reset" data-id="${attr(current.simulationId)}" type="button">Reset shipment</button>` : ""}` : ""}</div>
        </form>` : `<div class="foundation-empty"><strong>No eligible shipments</strong><p>Start a Driver trip with a supported route, assigned Driver, and registered sensor before running a simulation.</p></div>`}
      </section>
      <section class="foundation-panel admin-sim-monitor" aria-live="polite">
        <header><div><span class="foundation-eyebrow">Live generated state</span><h2>${current ? e(current.scenarioName) : "No simulation selected"}</h2><p>${current ? `${e(current.shipmentId)} · ${e(current.speedName)}` : "Start a scenario to monitor readings and risk changes."}</p></div>${current ? `<time>${e(formatElapsed(current.elapsedSeconds))}</time>` : ""}</header>
        <div class="admin-sim-route"><div><span>Route progress</span><strong>${e(progress.toFixed(0))}%</strong></div><i aria-label="${e(progress.toFixed(0))} percent complete"><b style="width:${attr(progress)}%"></b></i><small>${e(currentShipment?.currentLocation || selected?.currentLocation || "Waiting for first generated GPS reading")}</small></div>
        <div class="admin-sim-readings">
          ${simMetric("Temperature", reading?.temperature != null ? `${Number(reading.temperature).toFixed(1)}°C` : "—", currentShipment?.riskClassification)}
          ${simMetric("Battery", reading?.batteryLevel != null ? `${Number(reading.batteryLevel).toFixed(0)}%` : "—")}
          ${simMetric("Risk", h(currentShipment?.riskClassification || selected?.riskClassification || "not evaluated"))}
          ${simMetric("GPS", reading?.latitude != null ? `${Number(reading.latitude).toFixed(4)}, ${Number(reading.longitude).toFixed(4)}` : "—")}
          ${simMetric("Last reading", reading?.timestamp ? timeOnly(reading.timestamp) : "Waiting")}
        </div>
        ${current?.failureMessage ? `<p class="admin-sim-error" role="alert">${e(current.failureMessage)}</p>` : ""}
        <div class="admin-sim-pipeline"><span>Generated reading</span><i>→</i><span>Sensor validation</span><i>→</i><span>Risk + alerts</span><i>→</i><span>Role dashboards</span></div>
      </section>
      <section class="foundation-panel admin-sim-scenarios">
        <header><div><span class="foundation-eyebrow">Controlled behavior</span><h2>Available scenarios</h2><p>Synthetic data is clearly isolated to this local academic demo tool.</p></div></header>
        <div>${(source.scenarios || []).map((item) => `<article class="${item.id === scenarioId ? "selected" : ""}"><strong>${e(item.name)}</strong><p>${e(item.description)}</p></article>`).join("")}</div>
      </section>
    </div>`;
  }

  function localDemoPage(state) {
    const source = state.localDemo || { status: "loading" };
    if (["loading", "idle"].includes(source.status)) return `<section class="foundation-panel admin-sim-state" aria-busy="true"><span class="foundation-state-spinner" aria-hidden="true"></span><h2>Loading local demo flow</h2></section>`;
    if (source.status === "error" && !source.demo) return `<section class="foundation-panel admin-sim-state" role="alert"><span class="foundation-error-code">!</span><h2>Local demo unavailable</h2><p>${e(source.error)}</p><button class="foundation-primary" data-admin-action="local-demo-retry" type="button">Try again</button></section>`;
    const demo = source.demo || {};
    const monitoring = source.monitoring || {};
    const live = monitoring.liveState;
    const decision = monitoring.operationalDecision;
    const futureRisk = monitoring.futureRisk30m || monitoring.futureRisk;
    const next = demo.nextStep;
    const last = source.result || demo.lastResult;
    const accepted = last?.telemetryResponse?.telemetryAccepted === true;
    const acceptedCount = Number(last?.acceptedSampleCount || (accepted ? 1 : 0));
    const revision = monitoring.liveState?.revision;
    return `<div class="admin-simulation-layout">
      <section class="foundation-panel admin-sim-control">
        <header><div><span class="foundation-eyebrow">Local-only controlled demonstration</span><h2>One shipment, real state transitions</h2><p>Every step uses validated telemetry, authoritative persistence, alerts, and lifecycle services.</p></div>${badge(demo.complete ? "Complete" : `${demo.currentStep}/${demo.totalSteps}`)}</header>
        ${last ? `<div class="admin-demo-confirmation" role="status"><strong>${accepted ? `${e(acceptedCount)} accepted telemetry ${acceptedCount === 1 ? "sample" : "samples"}` : `${e(last.step?.label || "Demo step")} completed`}</strong><span>Authoritative revision ${revision == null ? "unchanged" : e(revision)} · ${e(live?.status ? h(live.status) : "No reading")} · ${e(decision?.recommendedAction ? h(decision.recommendedAction) : "No action")}</span></div>` : ""}
        <dl class="admin-demo-current">${detail("Shipment", demo.lotTripId)}${detail("Current step", last?.step?.label || "No reading")}${detail("Authoritative revision", revision == null ? "No revision" : String(revision))}${detail("Current condition", live?.status ? h(live.status) : "No reading")}${detail("Temperature", live?.latestTemperature == null ? "No reading" : `${Number(live.latestTemperature).toFixed(1)} C`)}${detail("Predicted 30-minute risk", demoFutureRisk(futureRisk))}${detail("Recommended action", live?.status && decision?.recommendedAction ? h(decision.recommendedAction) : "Waiting for telemetry")}${detail("Why", live?.status && decision?.reason ? decision.reason : "Waiting for accepted telemetry")}${detail("Active alerts", String(monitoring.openAlertCount || 0))}${detail("Trip", h(monitoring.tripIdentity?.status || "ACTIVE"))}</dl>
        ${demoHeroComparison(demo.heroComparison)}
        ${next ? `<div class="admin-demo-next"><span>Next event to inject</span><strong>${e(next.label)}</strong><small>${next.kind === "TELEMETRY" ? `${e(next.temperature)} C submitted through the real telemetry pipeline` : next.kind === "INTERVENTION" ? "Acknowledge and record corrective action" : "Complete through the atomic lifecycle transaction"}</small></div><button class="foundation-primary" data-admin-action="local-demo-next" type="button" ${source.status === "advancing" ? "disabled" : ""}>${source.status === "advancing" ? "Processing..." : "Next state"}</button>` : `<p class="admin-demo-complete">Sequence complete. Restart the isolated memory-mode server to reset the demo safely.</p>`}
        ${source.error ? `<p class="admin-sim-error" role="alert">${e(source.error)}</p>` : ""}
      </section>
      <section class="foundation-panel admin-sim-monitor" aria-live="polite"><header><div><span class="foundation-eyebrow">Evidence trail</span><h2>Cause and effect</h2></div></header><div class="admin-demo-pipeline"><span>Telemetry accepted</span><i>&rarr;</i><span>Revision ${revision == null ? "—" : e(revision)}</span><i>&rarr;</i><span>${e(live?.status ? h(live.status) : "No reading")}</span><i>&rarr;</i><span>${e(demoFutureRisk(futureRisk))}</span><i>&rarr;</i><strong>${e(decision?.recommendedAction ? h(decision.recommendedAction) : "Waiting")}</strong></div><ol class="admin-demo-steps">${(demo.steps || []).map((step, index) => `<li class="${index < demo.currentStep ? "complete" : index === demo.currentStep ? "next" : ""}"><span>${index < demo.currentStep ? "Done" : index === demo.currentStep ? "Next" : index + 1}</span><strong>${e(step.label)}</strong><small>${e(h(step.kind))}</small></li>`).join("")}</ol></section>
      <section class="foundation-panel admin-sim-scenarios"><header><div><span class="foundation-eyebrow">Safety boundary</span><h2>Why this is trustworthy</h2></div></header><p>This control exists only when explicitly enabled in local memory mode. It cannot run against DynamoDB or production, and it never assigns a status directly.</p><div class="admin-sim-pipeline"><span>Telemetry</span><i>&rarr;</i><span>Validation</span><i>&rarr;</i><span>Product rules</span><i>&rarr;</i><span>Alerts + UI</span></div></section>
    </div>`;
  }

  function demoFutureRisk(value) {
    return value?.state === "PREDICTED" && Number.isFinite(value.adverseEventProbability)
      ? `${(value.adverseEventProbability * 100).toFixed(2)}% predicted adverse-event probability`
      : "Unavailable";
  }

  function demoHeroComparison(comparison = {}) {
    const baseline = comparison.baseline;
    const intervene = comparison.intervene;
    if (!baseline) return "";
    const card = (item, fallbackLabel) => item ? `<article class="${item === intervene ? "decision-change" : ""}">
      <span>Comparison ${e(item.label || fallbackLabel)}</span>
      <strong>${e(h(item.currentCondition))}</strong>
      <dl>${detail("Forecast", Number.isFinite(item.adverseEventProbability) ? `${(item.adverseEventProbability * 100).toFixed(3)}%` : "Unavailable")}${detail("Action", h(item.recommendedAction))}${detail("Accepted samples", String(item.acceptedSamples))}${detail("Excursion history", `${Number(item.excursionMinutes).toFixed(0)} minutes`)}${detail("Utilization", `${(Number(item.excursionUtilization) * 100).toFixed(1)}%`)}</dl>
    </article>` : `<article class="pending"><span>Comparison ${fallbackLabel}</span><strong>Next state</strong><p>One more accepted reading completes the comparison.</p></article>`;
    return `<section class="admin-demo-hero" aria-label="Same current condition future-risk comparison"><header><span>ML evidence</span><h3>Same current condition, different accumulated history</h3></header><div>${card(baseline, "A")}<i aria-hidden="true">&rarr;</i>${card(intervene, "B")}</div><p>ProductRules remains <strong>MONITOR</strong>. Only accepted shipment history changes; when the forecast crosses the existing 50% engineering threshold, the recommendation changes from <strong>MONITOR</strong> to <strong>INTERVENE</strong>.</p></section>`;
  }

  function simMetric(label, value, tone = "") { return `<article class="${attr(tone)}"><span>${e(label)}</span><strong>${e(value)}</strong></article>`; }
  function formatElapsed(seconds) { const value = Number(seconds || 0); return `${Math.floor(value / 60)}m ${value % 60}s`; }
  function timeOnly(value) { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }

  function dashboard(data) {
    const s = data.summary || {};
    return `
      <section class="admin-essential-metrics" aria-label="Essential platform metrics">
        ${metricLink("Active Shipments", s.activeShipments, "shipments", "status", "__active_shipments", "neutral")}
        ${metricLink("At Risk", s.atRiskShipments, "shipments", "risk", "high", "warning")}
        ${metricLink("Critical", s.criticalShipments, "shipments", "risk", "critical", "critical")}
      </section>
      <div class="admin-command-layout">
        <section class="foundation-panel admin-control-panel">
          <header><div><span class="foundation-eyebrow">Main tools</span><h2>Platform Control</h2><p>Monitor deliveries and respond to cold-chain risks.</p></div></header>
          <div class="admin-module-grid">
            ${moduleTile("shipments", "Shipments", "Monitor all shipments", "shipment", s.activeShipments)}
            ${moduleTile("alerts", "Alerts", "Respond to platform risks", "alert", s.criticalShipments ? `${s.criticalShipments} critical` : "Clear")}
          </div>
        </section>
        <section class="foundation-panel admin-attention-panel">
          <header><div><span class="foundation-eyebrow">Urgent queue</span><h2>Needs Attention</h2></div><button class="admin-section-link" data-role-page="alerts" type="button">View all</button></header>
          ${needsAttention(data)}
        </section>
      </div>`;
  }

  function organizationsPage(data) {
    const rows = (data.organizations || []).map((item) => `<tr data-search="${attr(`${item.name} ${item.email} ${item.contactPerson}`)}" data-type="${attr(item.type)}" data-status="${attr(item.accountStatus)}"><td><strong>${e(item.name)}</strong></td><td>${e(h(item.type))}</td><td>${e(item.contactPerson)}</td><td><span>${e(item.email)}</span><small>${e(item.phone)}</small></td><td>${e(item.address)}</td><td>${badge(item.accountStatus)}</td><td>${e(item.activeShipments)}</td><td>${date(item.createdAt)}</td><td>${actionsMenu([
      action("View details", "view-organization", item.organizationId), action("Edit", "edit-organization", item.organizationId), action("View users", "organization-users", item.organizationId), action("View shipments", "organization-shipments", item.organizationId), action(item.accountStatus === "active" ? "Suspend" : "Activate", "toggle-organization", item.organizationId, item.accountStatus === "active" ? "danger" : "")
    ], `organization:${item.organizationId}`)}</td></tr>`).join("");
    return tablePage(filters([searchFilter("Search organizations"), selectFilter("type", "Organization type", ORG_TYPES), selectFilter("status", "Account status", [["active", "Active"], ["suspended", "Suspended"]])]), ["Organization", "Type", "Contact", "Email / phone", "Address", "Status", "Active shipments", "Created", "Actions"], rows, "No organizations match these filters.");
  }

  function usersPage(data) {
    const organizations = (data.organizations || []).map((item) => [item.organizationId, item.name]);
    const rows = (data.users || []).map((item) => `<tr data-search="${attr(`${item.name} ${item.username} ${item.email}`)}" data-role="${attr(item.role)}" data-organization="${attr(item.organizationId || "none")}" data-status="${attr(item.accountStatus)}"><td><strong>${e(item.name)}</strong><small>${e(item.email || "No email")}</small></td><td>${e(item.username)}</td><td>${badge(roleLabel(item.role), item.role)}</td><td>${e(item.organizationName || "Platform-wide")}</td><td>${badge(item.accountStatus)}</td><td>${date(item.lastActivity)}</td><td>${actionsMenu([action("View", "view-user", item.userId), action("Edit / assign", "edit-user", item.userId), action(item.accountStatus === "active" ? "Deactivate" : "Activate", "toggle-user", item.userId, item.accountStatus === "active" ? "danger" : "")], `user:${item.userId}`)}</td></tr>`).join("");
    return tablePage(filters([searchFilter("Search users"), selectFilter("role", "Role", ROLES), selectFilter("organization", "Organization", [["none", "Platform-wide"], ...organizations]), selectFilter("status", "Account status", [["active", "Active"], ["inactive", "Inactive"]])]), ["Name", "Username", "Role", "Organization", "Status", "Last activity", "Actions"], rows, "No users match these filters.");
  }

  function shipmentsPage(data) {
    const organizations = optionsFrom(data.organizations, "organizationId", "name");
    const categories = uniqueOptions(data.shipments, "product");
    const rows = (data.shipments || []).map((item, index) => `<tr data-search="${attr(`${item.shipmentId} ${item.organizationName} ${item.product} ${item.driverName} ${item.origin} ${item.destinationHospitalName}`)}" data-organization="${attr(item.organizationId)}" data-risk="${attr(item.riskLevel)}" data-status="${attr(item.status)}" data-product="${attr(item.product)}"><td><strong>${e(item.shipmentId)}</strong></td><td>${e(item.organizationName)}</td><td>${e(item.product)}</td><td>${e(item.driverName)}</td><td><span>${e(item.origin)}</span><small>to ${e(item.destinationHospitalName)}</small></td><td>${temperature(item)}</td><td>${badge(item.riskLevel)}</td><td>${badge(item.status)}</td><td>${e(item.expectedArrival)}</td><td>${actionsMenu([action("View details", "view-shipment", index), action("View map", "map-shipment", index)], `shipment:${item.shipmentId}`)}</td></tr>`).join("");
    return tablePage(filters([searchFilter("Search shipments"), selectFilter("organization", "Organization", organizations), selectFilter("risk", "Risk", [["low", "Safe"], ["high", "At risk"], ["critical", "Critical"]]), selectFilter("status", "Delivery status", [["__active_shipments", "Active shipments"], ...uniqueOptions(data.shipments, "status", true)]), selectFilter("product", "Product category", categories)]), ["Shipment", "Organization", "Product", "Driver", "Route", "Temperature", "Risk", "Delivery", "Expected arrival", "Actions"], rows, "No shipments match these filters.");
  }

  function devicesPage(data) {
    const organizations = optionsFrom(data.organizations, "organizationId", "name");
    const rows = (data.devices || []).map((item) => `<tr data-search="${attr(`${item.sensorId} ${item.deviceType} ${item.organizationName} ${item.assignment}`)}" data-organization="${attr(item.organizationId || "none")}" data-connection="${attr(item.connectionStatus)}" data-battery="${attr(item.batteryCondition)}"><td><strong>${e(item.sensorId)}</strong></td><td>${e(item.deviceType)}</td><td>${e(item.organizationName || "Unassigned")}</td><td>${e(item.assignment)}</td><td>${badge(item.connectionStatus)}</td><td>${battery(item)}</td><td>${date(item.lastReadingTime)}</td><td>${badge(item.deviceStatus)}</td><td>${actionsMenu([action("View details", "view-device", item.sensorId), action("Edit assignment", "edit-device", item.sensorId), action(item.active ? "Deactivate" : "Activate", "toggle-device", item.sensorId, item.active ? "danger" : "")], `device:${item.sensorId}`)}</td></tr>`).join("");
    return tablePage(filters([searchFilter("Search devices"), selectFilter("organization", "Organization", [["none", "Unassigned"], ...organizations]), selectFilter("connection", "Connection", [["online", "Online"], ["offline", "Offline"]]), selectFilter("battery", "Battery", [["normal", "Normal"], ["low", "Low"], ["critical", "Critical"]])]), ["Sensor ID", "Type", "Organization", "Assignment", "Connection", "Battery", "Last reading", "Status", "Actions"], rows, "No devices match these filters.");
  }

  function alertsPage(data) {
    const organizations = optionsFrom(data.organizations, "organizationId", "name");
    const rows = (data.alerts || []).map((item) => `<tr data-search="${attr(`${item.alertId} ${item.shipmentId} ${item.organizationName} ${item.type} ${item.explanation}`)}" data-severity="${attr(item.severity)}" data-organization="${attr(item.organizationId)}" data-status="${attr(item.status)}"><td>${badge(item.severity)}</td><td><strong>${e(item.shipmentId)}</strong></td><td>${e(item.organizationName)}</td><td>${e(h(item.type))}</td><td>${date(item.detectedAt)}</td><td class="admin-wide-cell"><strong>${e(item.explanation)}</strong><small>${e(item.recommendedAction)}</small></td><td>${badge(item.status)}</td><td>${e(item.driverResponse)}</td><td>${actionsMenu([action("View details", "view-alert", item.alertId), ...(item.status !== "acknowledged" ? [action("Acknowledge", "status-alert", item.alertId, "", "acknowledged")] : []), action("Escalate", "status-alert", item.alertId, "", "escalated"), action("Resolve", "status-alert", item.alertId, "", "resolved")], `alert:${item.alertId}`)}</td></tr>`).join("");
    return tablePage(filters([searchFilter("Search alerts"), selectFilter("severity", "Severity", [["low", "Low"], ["medium", "Medium"], ["high", "High"], ["critical", "Critical"]]), selectFilter("organization", "Organization", organizations), selectFilter("status", "Status", [["new", "New"], ["acknowledged", "Acknowledged"], ["action_taken", "Action Taken"], ["escalated", "Escalated"], ["resolved", "Resolved"]])]), ["Severity", "Shipment", "Organization", "Type", "Detected", "Explanation / action", "Status", "Driver response", "Actions"], rows, "No alerts match these filters.");
  }

  function ticketsPage(data) {
    const supportUsers = (data.users || []).filter((user) => user.role === "support");
    const rows = (data.tickets || []).map((item) => `<tr data-search="${attr(`${item.ticketId} ${item.organizationName} ${item.reportingUser} ${item.shipmentId} ${item.assignedAgent}`)}" data-priority="${attr(item.priority)}" data-status="${attr(item.status)}" data-assigned="${attr(item.assignedTo || "unassigned")}"><td><strong>${e(item.ticketId)}</strong></td><td>${e(item.organizationName)}</td><td>${e(item.reportingUser)}</td><td>${e(item.shipmentId || "Not linked")}</td><td>${badge(item.priority)}</td><td>${e(item.assignedAgent)}</td><td>${badge(item.status)}</td><td>${date(item.createdAt)}</td><td>${actionsMenu([action("View details", "view-ticket", item.ticketId), action("Assign / prioritize", "edit-ticket", item.ticketId), action("Escalate", "status-ticket", item.ticketId, "", "escalated"), action("Close", "status-ticket", item.ticketId, "danger", "closed")], `ticket:${item.ticketId}`)}</td></tr>`).join("");
    return tablePage(filters([searchFilter("Search tickets"), selectFilter("priority", "Priority", [["low", "Low"], ["medium", "Medium"], ["high", "High"], ["critical", "Critical"]]), selectFilter("status", "Status", [["__open_tickets", "Open tickets"], ["new", "New"], ["in_progress", "In progress"], ["waiting_for_response", "Waiting"], ["escalated", "Escalated"], ["resolved", "Resolved"], ["closed", "Closed"]]), selectFilter("assigned", "Assigned agent", [["unassigned", "Unassigned"], ...supportUsers.map((user) => [user.userId, user.name])])]), ["Ticket", "Organization", "Reporting user", "Shipment", "Priority", "Support agent", "Status", "Created", "Actions"], rows, "No tickets match these filters.");
  }

  function reportsPage(data) {
    const r = data.reports || {};
    return `<div class="admin-report-grid">
      ${reportGroup("Shipment outcomes", [["Total shipments", r.totalShipments], ["Completed", r.completedShipments], ["Safe", r.safeShipments], ["At risk", r.atRiskShipments], ["Critical", r.criticalShipments]])}
      ${reportGroup("Device health", [["Online sensors", r.onlineSensors], ["Offline sensors", r.offlineSensors]])}
      ${reportGroup("Support", [["Open tickets", r.openTickets], ["Resolved tickets", r.resolvedTickets]])}
      ${reportGroup("Protection estimate", [["Shipments protected", r.protectedShipments], ["Estimated value protected", money(r.estimatedValueProtected)]], "Estimate based on safe or completed shipments and MVP category values.")}
      <section class="foundation-panel admin-report-wide"><header><div><span class="foundation-eyebrow">Network composition</span><h2>Organizations by type</h2></div></header><div class="report-type-list">${Object.entries(r.organizationsByType || {}).map(([type, count]) => `<div><span>${e(h(type))}</span><strong>${e(count)}</strong></div>`).join("")}</div></section>
    </div>`;
  }

  function settingsPage(data) {
    const s = data.settings || {};
    return `<section class="foundation-panel admin-settings-panel"><header><div><span class="foundation-eyebrow">MVP configuration</span><h2>Platform settings</h2></div></header><form class="foundation-form foundation-form-grid" data-admin-form="settings"><label><span>Platform display name</span><input name="displayName" value="${attr(s.displayName)}" required></label><label><span>Temperature warning margin (°C)</span><input name="temperatureWarningMargin" type="number" min="0" max="20" step="0.1" value="${attr(s.temperatureWarningMargin)}" required></label><label><span>Low-battery threshold (%)</span><input name="lowBatteryThreshold" type="number" min="0" max="100" value="${attr(s.lowBatteryThreshold)}" required></label><label><span>Critical battery threshold (%)</span><input name="criticalBatteryThreshold" type="number" min="0" max="100" value="${attr(s.criticalBatteryThreshold)}" required></label><label class="admin-check"><input name="notifyCriticalAlerts" type="checkbox" ${s.notifyCriticalAlerts ? "checked" : ""}><span>Notify Admins about critical alerts</span></label><label class="admin-check"><input name="notifyOfflineSensors" type="checkbox" ${s.notifyOfflineSensors ? "checked" : ""}><span>Notify Admins when sensors go offline</span></label><p class="admin-form-feedback" role="status"></p><div class="form-actions"><button class="foundation-primary" type="submit">Save settings</button></div></form></section>`;
  }

  async function handleClick(event, state, actions) {
    const button = event.target.closest("[data-admin-action]");
    if (!button) return false;
    const actionName = button.dataset.adminAction;
    const id = button.dataset.id;
    const value = button.dataset.value;
    const data = state.data;
    if (actionName === "local-demo-retry") return actions.reloadLocalDemo(), true;
    if (actionName === "local-demo-next") {
      try {
        await actions.advanceLocalDemo();
        actions.notify("Demo shipment advanced through the real application path");
      } catch (error) {
        actions.notify(error.message, "error");
      }
      return true;
    }
    if (actionName === "simulation-retry") return actions.reloadSimulation(), true;
    if (["simulation-pause", "simulation-resume", "simulation-stop", "simulation-reset"].includes(actionName)) {
      const verb = actionName.replace("simulation-", "");
      const method = verb === "reset" ? "POST" : "PATCH";
      try {
        await window.VitaeAuth.api(`/api/admin/simulations/${encodeURIComponent(id)}/${verb}`, { method, body: "{}" });
        await actions.reloadSimulation();
        const confirmations = { pause: "Simulation paused", resume: "Simulation resumed", stop: "Simulation stopped", reset: "Simulation shipment reset" };
        actions.notify(confirmations[verb]);
      } catch (error) {
        actions.notify(error.message, "error");
      }
      return true;
    }
    if (actionName === "metric-filter") return openRelatedPage(state, actions, button.dataset.page, button.dataset.filter, button.dataset.value), true;
    if (actionName === "close-dialog") return closeDialog(), true;
    if (actionName === "add-organization") return openOrganizationForm(), true;
    if (actionName === "edit-organization") return openOrganizationForm(find(data.organizations, "organizationId", id)), true;
    if (actionName === "view-organization") return openDetails("Organization details", organizationDetails(find(data.organizations, "organizationId", id))), true;
    if (actionName === "organization-users") return openRelatedPage(state, actions, "users", "organization", id), true;
    if (actionName === "organization-shipments") return openRelatedPage(state, actions, "shipments", "organization", id), true;
    if (actionName === "toggle-organization") return updateWithConfirmation(`/api/admin/organizations/${encodeURIComponent(id)}`, { accountStatus: find(data.organizations, "organizationId", id).accountStatus === "active" ? "suspended" : "active" }, find(data.organizations, "organizationId", id).accountStatus === "active" ? "Suspend this organization? Its users will retain records but the account will be marked unavailable." : "Activate this organization?", "Organization status updated", actions), true;
    if (actionName === "add-user") return openUserForm(null, data), true;
    if (actionName === "edit-user") return openUserForm(find(data.users, "userId", id), data), true;
    if (actionName === "view-user") return openDetails("User details", userDetails(find(data.users, "userId", id))), true;
    if (actionName === "toggle-user") return updateWithConfirmation(`/api/admin/users/${encodeURIComponent(id)}`, { accountStatus: find(data.users, "userId", id).accountStatus === "active" ? "inactive" : "active" }, find(data.users, "userId", id).accountStatus === "active" ? "Deactivate this user? They will no longer be able to sign in." : "Activate this user?", "User status updated", actions), true;
    if (actionName === "view-shipment") return openShipmentDetails(data.shipments[Number(id)]), true;
    if (actionName === "map-shipment") return actions.openMap(data.shipments[Number(id)]), true;
    if (actionName === "add-device") return openDeviceForm(null, data), true;
    if (actionName === "edit-device") return openDeviceForm(find(data.devices, "sensorId", id), data), true;
    if (actionName === "view-device") return openDetails("Sensor details", deviceDetails(find(data.devices, "sensorId", id))), true;
    if (actionName === "toggle-device") return updateWithConfirmation(`/api/admin/devices/${encodeURIComponent(id)}`, { active: !find(data.devices, "sensorId", id).active }, find(data.devices, "sensorId", id).active ? "Deactivate this sensor? Incoming readings should be investigated before reactivation." : "Activate this sensor?", "Sensor status updated", actions), true;
    if (actionName === "view-alert") return openDetails("Alert details", alertDetails(find(data.alerts, "alertId", id))), true;
    if (actionName === "status-alert") return updateRecord(`/api/admin/alerts/${encodeURIComponent(id)}`, { status: value }, "Alert status updated", actions), true;
    if (actionName === "view-ticket") return openDetails("Ticket details", ticketDetails(find(data.tickets, "ticketId", id))), true;
    if (actionName === "edit-ticket") return openTicketForm(find(data.tickets, "ticketId", id), data), true;
    if (actionName === "status-ticket") return updateWithConfirmation(`/api/admin/tickets/${encodeURIComponent(id)}`, { status: value }, value === "closed" ? "Close this ticket?" : "Escalate this ticket?", `Ticket marked ${h(value)}`, actions), true;
    return false;
  }

  async function handleSubmit(event, _state, actions) {
    const form = event.target.closest("[data-admin-form]");
    if (!form) return false;
    event.preventDefault();
    const type = form.dataset.adminForm;
    const id = form.dataset.id;
    const payload = Object.fromEntries(new FormData(form).entries());
    if (type === "simulation-start") {
      const feedback = form.querySelector(".admin-form-feedback");
      const submit = form.querySelector("button[type=submit]");
      submit.disabled = true;
      feedback.textContent = "Starting controlled telemetry…";
      feedback.className = "admin-form-feedback";
      try {
        await window.VitaeAuth.api("/api/admin/simulations/start", { method: "POST", body: JSON.stringify(payload) });
        await actions.reloadSimulation();
        actions.notify("Shipment simulation started");
      } catch (error) {
        feedback.textContent = error.message;
        feedback.className = "admin-form-feedback error";
        submit.disabled = false;
      }
      return true;
    }
    if (type === "settings") {
      payload.notifyCriticalAlerts = form.elements.notifyCriticalAlerts.checked;
      payload.notifyOfflineSensors = form.elements.notifyOfflineSensors.checked;
    }
    if (type === "device") payload.active = form.elements.active ? form.elements.active.checked : true;
    const routes = {
      organization: [id ? `/api/admin/organizations/${encodeURIComponent(id)}` : "/api/admin/organizations", id ? "PATCH" : "POST", "Organization saved"],
      user: [id ? `/api/admin/users/${encodeURIComponent(id)}` : "/api/admin/users", id ? "PATCH" : "POST", "User saved"],
      device: [id ? `/api/admin/devices/${encodeURIComponent(id)}` : "/api/admin/devices", id ? "PATCH" : "POST", "Sensor saved"],
      ticket: [`/api/admin/tickets/${encodeURIComponent(id)}`, "PATCH", "Ticket updated"],
      settings: ["/api/admin/settings", "POST", "Settings saved"],
    };
    const [url, method, success] = routes[type];
    const feedback = form.querySelector(".admin-form-feedback");
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    feedback.textContent = "Saving…";
    feedback.className = "admin-form-feedback";
    try {
      await window.VitaeAuth.api(url, { method, body: JSON.stringify(payload) });
      closeDialog();
      await actions.reload();
      actions.notify(success);
    } catch (error) {
      feedback.textContent = error.message;
      feedback.className = "admin-form-feedback error";
      submit.disabled = false;
    }
    return true;
  }

  function handleFilter(event, root, state, actions) {
    if (event.target.matches("[data-simulation-control]")) {
      state.adminSimulationUi = state.adminSimulationUi || {};
      state.adminSimulationUi[event.target.dataset.simulationControl] = event.target.value;
      actions.render();
      return true;
    }
    if (!event.target.matches("[data-admin-filter]")) return false;
    const table = root.querySelector(`[data-filter-table="${event.target.dataset.table}"]`);
    if (!table) return false;
    const filters = [...root.querySelectorAll(`[data-admin-filter][data-table="${event.target.dataset.table}"]`)];
    let visible = 0;
    table.querySelectorAll("tbody tr[data-search]").forEach((row) => {
      const matches = filters.every((filter) => {
        const value = String(filter.value || "").toLowerCase();
        if (!value) return true;
        const key = filter.dataset.adminFilter;
        const rowValue = String(row.dataset[key] || "").toLowerCase();
        if (key === "search") return String(row.dataset.search || "").toLowerCase().includes(value);
        if (value === "__active_shipments") return !["arrived", "delivered"].includes(rowValue);
        if (value === "__open_tickets") return !["resolved", "closed"].includes(rowValue);
        return rowValue === value;
      });
      row.hidden = !matches;
      if (matches) visible += 1;
    });
    const empty = root.querySelector(`[data-filter-empty="${event.target.dataset.table}"]`);
    if (empty) empty.hidden = visible > 0;
    return true;
  }

  function openOrganizationForm(item = {}) {
    openDialog(item.organizationId ? "Edit organization" : "Add organization", `<form class="foundation-form foundation-form-grid" data-admin-form="organization" ${item.organizationId ? `data-id="${attr(item.organizationId)}"` : ""}><label><span>Organization name</span><input name="name" value="${attr(item.name)}" required></label><label><span>Organization type</span>${select("type", ORG_TYPES, item.type, true)}</label><label><span>Contact person</span><input name="contactPerson" value="${attr(item.contactPerson)}"></label><label><span>Email</span><input name="email" type="email" value="${attr(item.email)}" required></label><label><span>Phone</span><input name="phone" value="${attr(item.phone)}"></label><label><span>Address</span><input name="address" value="${attr(item.address)}"></label>${formFooter(item.organizationId ? "Save changes" : "Add organization")}</form>`);
  }

  function openUserForm(item = {}, data) {
    const organizations = optionsFrom(data.organizations, "organizationId", "name");
    openDialog(item.userId ? "Edit platform user" : "Add platform user", `<form class="foundation-form foundation-form-grid" data-admin-form="user" ${item.userId ? `data-id="${attr(item.userId)}"` : ""}><label><span>Name</span><input name="name" value="${attr(item.name)}" required></label><label><span>Username</span><input name="username" value="${attr(item.username)}" required></label><label><span>Email</span><input name="email" type="email" value="${attr(item.email)}"></label><label><span>Role</span>${select("role", ROLES, item.role, true)}</label><label><span>Organization</span>${select("organizationId", organizations, item.organizationId, false, "Platform-wide")}</label><label><span>${item.userId ? "New password (optional)" : "Temporary password"}</span><input name="password" type="password" ${item.userId ? "" : "required"}></label>${formFooter(item.userId ? "Save changes" : "Add user")}</form>`);
  }

  function openDeviceForm(item = {}, data) {
    const organizations = optionsFrom(data.organizations, "organizationId", "name");
    openDialog(item.sensorId ? "Edit sensor" : "Register sensor", `<form class="foundation-form foundation-form-grid" data-admin-form="device" ${item.sensorId ? `data-id="${attr(item.sensorId)}"` : ""}><label><span>Sensor ID</span><input name="sensorId" value="${attr(item.sensorId)}" ${item.sensorId ? "readonly" : "required"}></label><label><span>Device type</span><input name="deviceType" value="${attr(item.deviceType || "Temperature and location sensor")}" required></label><label><span>Organization</span>${select("organizationId", organizations, item.organizationId, false, "Unassigned")}</label><label><span>Shipment ID</span><input name="shipmentId" value="${attr(item.shipmentId)}"></label><label><span>Container ID</span><input name="containerId" value="${attr(item.containerId)}"></label><label><span>Battery level</span><input name="batteryLevel" type="number" min="0" max="100" value="${attr(item.batteryLevel ?? 100)}" required></label>${item.sensorId ? `<label class="admin-check"><input name="active" type="checkbox" ${item.active ? "checked" : ""}><span>Sensor active</span></label>` : ""}${formFooter(item.sensorId ? "Save changes" : "Register sensor")}</form>`);
  }

  function openTicketForm(item, data) {
    const agents = (data.users || []).filter((user) => user.role === "support").map((user) => [user.userId, user.name]);
    openDialog("Assign and prioritize ticket", `<form class="foundation-form" data-admin-form="ticket" data-id="${attr(item.ticketId)}"><label><span>Support agent</span>${select("assignedTo", agents, item.assignedTo, false, "Unassigned")}</label><label><span>Priority</span>${select("priority", [["low", "Low"], ["medium", "Medium"], ["high", "High"], ["critical", "Critical"]], item.priority, true)}</label><label><span>Status</span>${select("status", [["new", "New"], ["in_progress", "In progress"], ["waiting_for_response", "Waiting for response"], ["escalated", "Escalated"], ["resolved", "Resolved"], ["closed", "Closed"]], item.status, true)}</label>${formFooter("Save ticket")}</form>`);
  }

  function openShipmentDetails(item) {
    openDialog(`Shipment ${e(item.shipmentId)}`, `<div class="admin-detail-grid">${detail("Organization", item.organizationName)}${detail("Product", item.product)}${detail("Driver", item.driverName)}${detail("Status", h(item.status))}${detail("Temperature", typeof item.temperature === "number" ? `${item.temperature.toFixed(1)}°C` : "Unavailable")}${detail("Required range", item.safeTemperatureMin != null ? `${item.safeTemperatureMin}–${item.safeTemperatureMax}°C` : "Not supplied")}${detail("Origin", item.origin)}${detail("Destination", item.destinationHospitalName)}${detail("Current location", item.currentLocation)}${detail("Expected arrival", item.expectedArrival)}${detail("Risk", h(item.riskLevel))}${detail("Estimated value", money(item.estimatedValue))}</div>`);
  }

  function openDetails(title, content) {
    openDialog(title, `<div class="admin-detail-grid">${content}</div>`);
  }

  function openDialog(title, content) {
    closeDialog();
    const modal = document.createElement("div");
    modal.className = "admin-dialog-backdrop";
    modal.innerHTML = `<section class="admin-dialog" role="dialog" aria-modal="true" aria-label="${attr(title)}"><header><div><span class="foundation-eyebrow">Admin action</span><h2>${title}</h2></div><button data-admin-action="close-dialog" type="button" aria-label="Close dialog">Close</button></header><div class="admin-dialog-body">${content}</div></section>`;
    document.getElementById("roleView").appendChild(modal);
  }

  function closeDialog() { document.querySelector(".admin-dialog-backdrop")?.remove(); }

  async function updateWithConfirmation(url, payload, message, success, actions) {
    if (!window.confirm(message)) return;
    await updateRecord(url, payload, success, actions);
  }

  async function updateRecord(url, payload, success, actions) {
    try {
      await window.VitaeAuth.api(url, { method: "PATCH", body: JSON.stringify(payload) });
      await actions.reload();
      actions.notify(success);
    } catch (error) {
      actions.notify(error.message, "error");
    }
  }

  function openRelatedPage(state, actions, page, filter, value) {
    state.page = page;
    actions.render();
    const input = document.querySelector(`[data-admin-filter="${filter}"]`);
    if (input) {
      input.value = value;
      handleFilter({ target: input }, document.getElementById("roleView"));
    }
  }

  function metricLink(label, value, page, filter, filterValue, tone) {
    return `<button class="admin-metric-link ${attr(tone)}" data-admin-action="metric-filter" data-page="${attr(page)}" data-filter="${attr(filter)}" data-value="${attr(filterValue)}" type="button"><span><i aria-hidden="true"></i>${e(label)}</span><strong>${e(value || 0)}</strong></button>`;
  }

  function moduleTile(page, title, description, iconName, count = "") {
    return `<button class="admin-module-tile" data-role-page="${attr(page)}" type="button"><span class="admin-module-icon" aria-hidden="true">${moduleIcon(iconName)}</span><span class="admin-module-copy"><strong>${e(title)}</strong><small>${e(description)}</small></span>${count !== "" ? `<span class="admin-module-count">${e(count)}</span>` : ""}</button>`;
  }

  function needsAttention(data) {
    const urgent = [];
    (data.shipments || []).forEach((item, index) => {
      if (item.riskLevel === "critical") urgent.push({ rank: 0, type: "Critical shipment", title: item.shipmentId, organization: item.organizationName, severity: "critical", time: item.lastUpdated, action: "view-shipment", id: index });
    });
    (data.alerts || []).forEach((item) => {
      if (item.severity === "critical" && !["resolved"].includes(item.status)) urgent.push({ rank: 0, type: "Critical alert", title: item.shipmentId || item.alertId, organization: item.organizationName, severity: "critical", time: item.detectedAt, action: "view-alert", id: item.alertId });
    });
    (data.devices || []).forEach((item) => {
      if (item.connectionStatus === "offline") urgent.push({ rank: 1, type: "Offline sensor", title: item.sensorId, organization: item.organizationName, severity: "warning", time: item.lastReadingTime, action: "view-device", id: item.sensorId });
    });
    (data.tickets || []).forEach((item) => {
      if (["critical", "high"].includes(item.priority) && !["resolved", "closed"].includes(item.status)) urgent.push({ rank: item.priority === "critical" ? 1 : 2, type: "Support ticket", title: item.ticketId, organization: item.organizationName, severity: item.priority, time: item.updatedAt, action: "view-ticket", id: item.ticketId });
    });
    urgent.sort((a, b) => a.rank - b.rank || String(b.time || "").localeCompare(String(a.time || "")));
    const items = urgent.slice(0, 3);
    if (!items.length) return `<div class="admin-attention-empty"><span aria-hidden="true">✓</span><p>No critical issues require attention.</p></div>`;
    return `<div class="admin-attention-list">${items.map((item) => `<button data-admin-action="${attr(item.action)}" data-id="${attr(item.id)}" type="button"><span class="admin-attention-main"><small>${e(item.type)}</small><strong>${e(item.title)}</strong><span>${e(item.organization || "Platform-wide")}</span></span><span class="admin-attention-meta">${badge(item.severity)}<time>${compactTime(item.time)}</time></span><span class="admin-attention-chevron" aria-hidden="true">›</span></button>`).join("")}</div>`;
  }

  function moduleIcon(name) {
    const paths = {
      building: '<path d="M4 20V6l8-3 8 3v14M8 9h2m4 0h2M8 13h2m4 0h2M9 20v-3h6v3"/>',
      users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m7-10a4 4 0 1 0 0-8 4 4 0 0 0 0 8m13 10v-2a4 4 0 0 0-3-3.87m-2-11.96a4 4 0 0 1 0 7.75"/>',
      shipment: '<path d="M3 7l9-4 9 4-9 4-9-4Zm0 0v10l9 4 9-4V7M12 11v10"/>',
      sensor: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 7h6m-6 4h6m-3 4v2"/>',
      alert: '<path d="M12 3 2 20h20L12 3Zm0 6v5m0 3h.01"/>',
      ticket: '<path d="M4 5h16v5a2 2 0 0 0 0 4v5H4v-5a2 2 0 0 0 0-4V5Zm6 0v14"/>',
      report: '<path d="M4 20V10m6 10V4m6 16v-7m4 7H2"/>',
      settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1v.1h-4V21a1.7 1.7 0 0 0-1.1-1.6 1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1-.4h-.1v-4H3a1.7 1.7 0 0 0 1.6-1.1 1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1v-.1h4V3a1.7 1.7 0 0 0 1.1 1.6 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9c.15.37.37.7.6 1 .3.23.65.37 1 .4h.1v4H21a1.7 1.7 0 0 0-1.6.6Z"/>',
    };
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.report}</svg>`;
  }

  function compactTime(value) {
    if (!value) return "Recently";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return e(value);
    return e(parsed.toLocaleDateString([], { month: "short", day: "numeric" }));
  }

  function tablePage(filterMarkup, headers, rows, emptyMessage) {
    const id = `admin-table-${Math.random().toString(36).slice(2, 8)}`;
    const filtersWithId = filterMarkup.replaceAll("__TABLE__", id);
    return `<section class="admin-table-page"><div class="admin-filter-bar">${filtersWithId}</div><div class="admin-table-wrap"><table data-filter-table="${id}"><thead><tr>${headers.map((label) => `<th>${e(label)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div><div class="foundation-empty" data-filter-empty="${id}" hidden>${e(emptyMessage)}</div></section>`;
  }

  function filters(items) { return items.join(""); }
  function searchFilter(placeholder) { return `<label class="admin-search"><span class="sr-only">${e(placeholder)}</span><input data-admin-filter="search" data-table="__TABLE__" placeholder="${attr(placeholder)}"></label>`; }
  function selectFilter(key, label, options) { return `<label><span>${e(label)}</span>${select("", options, "", false, `All ${label.toLowerCase()}`, `data-admin-filter="${attr(key)}" data-table="__TABLE__"`)}</label>`; }
  function select(name, options, selected, required = false, blankLabel = "Select", extra = "") { return `<select ${name ? `name="${attr(name)}"` : ""} ${required ? "required" : ""} ${extra}><option value="">${e(blankLabel)}</option>${options.map(([value, label]) => `<option value="${attr(value)}" ${String(value) === String(selected || "") ? "selected" : ""}>${e(label)}</option>`).join("")}</select>`; }
  function optionsFrom(items = [], key, label) { return items.map((item) => [item[key], item[label]]); }
  function uniqueOptions(items = [], key, human = false) { return [...new Set(items.map((item) => item[key]).filter(Boolean))].map((value) => [value, human ? h(value) : value]); }
  function primaryAction(label, actionName) { return `<button class="foundation-primary" data-admin-action="${actionName}" type="button">${e(label)}</button>`; }
  function action(label, name, id, tone = "", value = "") { return `<button class="${tone}" data-admin-action="${attr(name)}" data-id="${attr(id)}" ${value ? `data-value="${attr(value)}"` : ""} type="button">${e(label)}</button>`; }
  function actionsMenu(items, key) { return `<details class="admin-row-actions" data-ui-state-key="admin-actions:${attr(key)}"><summary>Actions</summary><div>${items.join("")}</div></details>`; }
  function formFooter(label) { return `<p class="admin-form-feedback" role="status"></p><div class="form-actions"><button class="foundation-primary" type="submit">${e(label)}</button></div>`; }
  function dashboardPanel(eyebrow, title, page, content, className = "") { return `<section class="foundation-panel ${className}"><header><div><span class="foundation-eyebrow">${e(eyebrow)}</span><h2>${e(title)}</h2></div><button class="admin-section-link" data-role-page="${attr(page)}" type="button">View all</button></header>${content}</section>`; }
  function summaryMetric(label, value, tone = "") { return `<span>${e(label)}</span><strong class="${tone}">${e(value ?? 0)}</strong>`; }
  function statusOverview(statuses) { const entries = Object.entries(statuses); return entries.length ? `<div class="status-overview">${entries.map(([status, count]) => `<div><span>${e(h(status))}</span><strong>${e(count)}</strong></div>`).join("")}</div>` : empty("No shipment status data."); }
  function incidentList(items) { return items.length ? `<div class="foundation-list">${items.slice(0, 5).map((item) => `<article><div><strong>${e(item.shipmentId)}</strong><span>${e(item.product)} · ${e(item.organizationName)}</span></div>${badge(item.riskLevel)}</article>`).join("")}</div>` : empty("No critical incidents."); }
  function deviceHealth(data) { return `<div class="device-health-strip"><div><strong>${e(data.healthy || 0)}</strong><span>Healthy</span></div><div><strong>${e(data.lowBattery || 0)}</strong><span>Low battery</span></div><div><strong>${e(data.offline || 0)}</strong><span>Offline</span></div></div>${deviceList((data.sensors || []).slice(0, 4))}`; }
  function deviceList(items) { return `<div class="foundation-list">${items.map((item) => `<article><div><strong>${e(item.sensorId)}</strong><span>${e(item.batteryLevel)}% battery</span></div>${badge(item.deviceStatus)}</article>`).join("")}</div>`; }
  function organizationList(items) { return items.length ? `<div class="foundation-list">${items.map((item) => `<article><div><strong>${e(item.name)}</strong><span>${e(h(item.type))} · ${e(item.address)}</span></div>${badge(item.accountStatus)}</article>`).join("")}</div>` : empty("No organizations available."); }
  function ticketList(items) { return items.length ? `<div class="foundation-list">${items.map((item) => `<article><div><strong>${e(item.subject)}</strong><span>${e(item.ticketId)} · ${e(item.organizationName)}</span></div>${badge(item.priority)}</article>`).join("")}</div>` : empty("No tickets available."); }
  function reportGroup(title, rows, note = "") { return `<section class="foundation-panel"><header><div><span class="foundation-eyebrow">Current data</span><h2>${e(title)}</h2></div></header><div class="admin-report-list">${rows.map(([label, value]) => `<div><span>${e(label)}</span><strong>${e(value ?? 0)}</strong></div>`).join("")}</div>${note ? `<p class="admin-report-note">${e(note)}</p>` : ""}</section>`; }
  function detail(label, value) { return `<article><span>${e(label)}</span><strong>${e(value ?? "Not available")}</strong></article>`; }
  function organizationDetails(item) { return detail("Name", item.name) + detail("Type", h(item.type)) + detail("Contact", item.contactPerson) + detail("Email", item.email) + detail("Phone", item.phone) + detail("Address", item.address) + detail("Status", h(item.accountStatus)) + detail("Active shipments", item.activeShipments); }
  function userDetails(item) { return detail("Name", item.name) + detail("Username", item.username) + detail("Email", item.email) + detail("Role", roleLabel(item.role)) + detail("Organization", item.organizationName || "Platform-wide") + detail("Status", h(item.accountStatus)) + detail("Last activity", date(item.lastActivity)); }
  function deviceDetails(item) { return detail("Sensor ID", item.sensorId) + detail("Device type", item.deviceType) + detail("Organization", item.organizationName || "Unassigned") + detail("Shipment", item.shipmentId || "Unassigned") + detail("Container", item.containerId || "Unassigned") + detail("Connection", h(item.connectionStatus)) + detail("Battery", `${item.batteryLevel}%`) + detail("Last reading", date(item.lastReadingTime)); }
  function alertDetails(item) { return detail("Severity", h(item.severity)) + detail("Shipment", item.shipmentId) + detail("Organization", item.organizationName) + detail("Type", h(item.type)) + detail("Detected", date(item.detectedAt)) + detail("Explanation", item.explanation) + detail("Recommended action", item.recommendedAction) + detail("Status", h(item.status)) + detail("Driver response", item.driverResponse); }
  function ticketDetails(item) { return detail("Ticket", item.ticketId) + detail("Organization", item.organizationName) + detail("Reporting user", item.reportingUser) + detail("Shipment", item.shipmentId || "Not linked") + detail("Priority", h(item.priority)) + detail("Assigned agent", item.assignedAgent) + detail("Status", h(item.status)) + detail("Created", date(item.createdAt)) + detail("Summary", item.summary); }
  function find(items = [], key, value) { return items.find((item) => String(item[key]) === String(value)) || {}; }
  function roleLabel(role) { return ({ admin: "Admin", organization_user: "Organization User", driver: "Driver", support: "Support Agent" })[role] || h(role); }
  function temperature(item) { const value = typeof item.temperature === "number" ? `${item.temperature.toFixed(1)}°C` : "Unavailable"; const range = item.safeTemperatureMin != null ? `${item.safeTemperatureMin}–${item.safeTemperatureMax}°C` : "No range"; return `<strong>${e(value)}</strong><small>${e(range)}</small>`; }
  function battery(item) { return `<strong>${e(item.batteryLevel)}%</strong><small>${e(h(item.batteryCondition))}</small>`; }
  function badge(value, tone) { return window.VitaeUI.badge(value, tone); }
  function empty(value) { return window.VitaeUI.empty(value); }
  function h(value) { return window.VitaeUI.humanize(value); }
  function e(value) { return window.VitaeUI.escape(value); }
  function attr(value) { return e(value ?? ""); }
  function date(value) { if (!value) return "Unavailable"; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? e(value) : e(parsed.toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" })); }
  function money(value) { return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(Number(value || 0)); }

  window.VitaeAdmin = { handleClick, handleFilter, handleSubmit, render };
})();
