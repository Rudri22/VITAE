(function () {
  const NAV = [["dashboard", "Dashboard"], ["tickets", "Tickets"], ["lookup", "Shipment Lookup"]];

  function supportState(state) {
    state.supportUi ||= { selectedTicketId: null, filters: {}, page: 1, lookupQuery: "", organizationQuery: "", knowledgeQuery: "" };
    return state.supportUi;
  }

  function render(state, page = "dashboard") {
    if (!NAV.some(([id]) => id === page)) page = "dashboard";
    const ui = window.VitaeUI, local = supportState(state);
    const titles = { dashboard: "Resolution Workspace", tickets: "All Tickets", critical: "Critical Tickets", lookup: "Shipment Lookup", organizations: "Organizations", knowledge: "Knowledge Base", profile: "Agent Profile" };
    const subtitles = { dashboard: "Prioritize customer issues and resolve cold-chain interruptions.", tickets: "Search and manage the complete permitted ticket queue.", critical: "Focus on unresolved critical-priority cases.", lookup: "Find diagnostic shipment context without changing operations.", organizations: "View support-safe organization contacts and ticket history.", knowledge: "Search concise troubleshooting guidance.", profile: "Your support identity and current workload." };
    return ui.shell({ roleClass: "support-foundation-shell support-resolution-app", roleLabel: "Support Desk", user: state.user, nav: NAV, active: page,
      header: ui.pageHeader("Ticket resolution", titles[page], subtitles[page]), content: pageContent(page, state, local) });
  }

  function pageContent(page, state, local) {
    const data = state.data || {};
    if (local.selectedTicketId && ["tickets", "critical", "dashboard"].includes(page)) {
      const ticket = (data.tickets || []).find((item) => item.ticketId === local.selectedTicketId);
      if (ticket) return ticketDetails(ticket, state);
      local.selectedTicketId = null;
    }
    if (page === "dashboard") return dashboard(data);
    if (page === "tickets") return ticketListPage(data, local, false);
    if (page === "critical") return ticketListPage(data, local, true);
    if (page === "lookup") return shipmentLookup(data, local);
    if (page === "organizations") return organizationsPage(data, local);
    if (page === "knowledge") return knowledgePage(data, local);
    return profilePage(state);
  }

  function dashboard(data) {
    const s = data.summary || {};
    const requests = data.incomingRequests || data.priorityQueue || [];
    const trips = data.activeTrips || (data.shipments || []).filter((item) => ["active", "in_transit", "at_risk", "delayed"].includes(item.status));
    const channels = requests.reduce((counts, item) => { const key = requestSource(item).key; counts[key] = (counts[key] || 0) + 1; return counts; }, {});
    return `<section class="support-workload-strip">${[["New", s.new || 0], ["Critical", s.critical || 0], ["In Progress", s.inProgress || 0]].map(([label, value]) => `<article><span>${esc(label)}</span><strong>${esc(value)}</strong></article>`).join("")}</section>
      <section class="support-channel-strip" aria-label="Request sources"><div><span class="support-source admin">Admin</span><strong>${channels.admin || 0}</strong><small>platform requests</small></div><div><span class="support-source organization">Organization</span><strong>${channels.organization || 0}</strong><small>operations requests</small></div><div><span class="support-source driver">Driver</span><strong>${channels.driver || 0}</strong><small>on-road requests</small></div><p><strong>${trips.length} live trips</strong><span> visible across permitted organizations</span></p></section>
      <div class="support-operations-grid"><section class="foundation-panel support-inbox-panel"><header><div><span class="foundation-eyebrow">Unified intake</span><h2>Incoming Support Requests</h2><p>Requests from platform Admins, organizations, and drivers in one prioritized queue.</p></div><button class="support-link" data-role-page="tickets" type="button">View all tickets</button></header>${inboxRows(requests.slice(0, 7))}</section><section class="foundation-panel support-live-panel"><header><div><span class="foundation-eyebrow">Operations visibility</span><h2>Live Trips</h2><p>Read-only cold-chain status across active deliveries.</p></div><button class="support-link" data-role-page="lookup" type="button">View all trips</button></header>${liveTripRows(trips.slice(0, 6))}</section></div>`;
  }

  function requestSource(item) {
    const key = item.sourceType || (item.driverId ? "driver" : "organization");
    return { key, label: item.sourceLabel || { admin: "Admin", driver: "Driver", organization: "Organization" }[key] || "Organization" };
  }

  function inboxRows(items) {
    return items.length ? `<div class="support-inbox-list">${items.map((item) => { const source = requestSource(item); return `<button data-support-action="ticket-open" data-id="${esc(item.ticketId)}" type="button"><span class="support-priority-mark ${esc(item.priority)}" aria-hidden="true"></span><span class="support-inbox-copy"><span><span class="support-source ${esc(source.key)}">${esc(source.label)}</span><small>${esc(item.ticketId)} · ${esc(item.requester)}</small></span><strong>${esc(item.subject)}</strong><em>${esc(item.summary)}</em></span><span class="support-inbox-meta">${window.VitaeUI.badge(item.priority)}${window.VitaeUI.badge(item.status)}<time>${shortDate(item.updatedAt)}</time></span><b aria-hidden="true">›</b></button>`; }).join("")}</div>` : empty("No incoming support requests.");
  }

  function liveTripRows(items) {
    return items.length ? `<div class="support-live-list">${items.map((item) => { const outside = typeof item.temperature === "number" && ((typeof item.safeTemperatureMin === "number" && item.temperature < item.safeTemperatureMin) || (typeof item.safeTemperatureMax === "number" && item.temperature > item.safeTemperatureMax)); return `<button data-support-action="trip-open" data-id="${esc(item.shipmentId)}" type="button"><span class="support-live-state ${outside ? "risk" : "stable"}" aria-hidden="true"></span><span><small>${esc(item.shipmentId)} · ${esc(item.organizationName)}</small><strong>${esc(item.currentLocation || "Location unavailable")}</strong><em>${esc(item.driverName || "Driver not assigned")}</em></span><span class="support-live-condition"><strong>${temperature(item.temperature)}</strong><small>${outside ? "Outside required range" : `Required ${range(item)}`}</small>${window.VitaeUI.badge(item.status)}</span><b aria-hidden="true">›</b></button>`; }).join("")}</div>` : empty("No trips are currently on the road.");
  }

  function queueRows(items) {
    return items.length ? `<div class="support-queue-list">${items.map((item) => `<button data-support-action="ticket-open" data-id="${esc(item.ticketId)}" type="button"><i class="${esc(item.priority)}"></i><span><small>${esc(item.ticketId)} · ${esc(item.organizationName)}</small><strong>${esc(item.subject)}</strong><em>${esc(item.summary)}</em></span><span class="support-queue-meta">${window.VitaeUI.badge(item.status)}<time>${shortDate(item.updatedAt)}</time></span><b aria-hidden="true">›</b></button>`).join("")}</div>` : empty("No tickets in this queue.");
  }

  function ticketListPage(data, local, criticalOnly) {
    const f = local.filters, all = (data.tickets || []).filter((item) => !criticalOnly || (item.priority === "critical" && item.status !== "resolved"));
    const filtered = all.filter((item) => (!f.search || JSON.stringify(item).toLowerCase().includes(f.search.toLowerCase())) && (!f.priority || item.priority === f.priority) && (!f.status || item.status === f.status) && (!f.organization || item.organizationId === f.organization) && (!f.agent || item.assignedTo === f.agent));
    filtered.sort((a, b) => f.sort === "oldest" ? String(a.createdAt).localeCompare(String(b.createdAt)) : f.sort === "priority" ? priorityRank(a.priority) - priorityRank(b.priority) : String(b.updatedAt).localeCompare(String(a.updatedAt)));
    const pageSize = 5, pages = Math.max(1, Math.ceil(filtered.length / pageSize)); local.page = Math.min(local.page, pages); const visible = filtered.slice((local.page - 1) * pageSize, local.page * pageSize);
    return `<section class="foundation-panel support-ticket-list-panel"><form class="support-ticket-filters" data-support-form="filters"><input name="search" value="${esc(f.search)}" placeholder="Search tickets" aria-label="Search tickets"><select name="priority"><option value="">All priorities</option>${options(["low", "medium", "high", "critical"], f.priority)}</select><select name="status"><option value="">All statuses</option>${options(["new", "in_progress", "waiting_for_user", "escalated", "resolved"], f.status)}</select><select name="organization"><option value="">All organizations</option>${recordOptions(data.organizations || [], "organizationId", "name", f.organization)}</select><select name="agent"><option value="">All agents</option>${recordOptions(data.agents || [], "userId", "name", f.agent)}</select><select name="sort">${options(["updated", "oldest", "priority"], f.sort || "updated")}</select><button type="submit">Apply</button></form><div class="support-ticket-table"><div class="support-ticket-head"><span>Ticket</span><span>Organization / reporter</span><span>Shipment / category</span><span>Created</span><span>Priority</span><span>Agent / status</span><span>Actions</span></div>${visible.map(ticketRow).join("") || empty("No tickets match these filters.")}</div><footer class="support-pagination"><span>${filtered.length} tickets · page ${local.page} of ${pages}</span><div><button data-support-action="ticket-page" data-page="${local.page - 1}" type="button" ${local.page <= 1 ? "disabled" : ""}>Previous</button><button data-support-action="ticket-page" data-page="${local.page + 1}" type="button" ${local.page >= pages ? "disabled" : ""}>Next</button></div></footer></section>`;
  }

  function ticketRow(item) {
    return `<article class="support-ticket-row"><button class="support-ticket-id" data-support-action="ticket-open" data-id="${esc(item.ticketId)}" type="button"><strong>${esc(item.ticketId)}</strong><small>${esc(item.subject)}</small></button><span><strong>${esc(item.organizationName)}</strong><small>${esc(item.requester)}</small></span><span><strong>${esc(item.shipmentId || "Not linked")}</strong><small>${esc(human(item.category))}</small></span><time>${shortDate(item.createdAt)}</time><span>${window.VitaeUI.badge(item.priority)}</span><span><strong>${esc(item.assignedAgentName)}</strong><small>${esc(human(item.status))}</small></span><details class="support-row-menu" data-ui-state-key="support-ticket-menu:${esc(item.ticketId)}"><summary aria-label="Ticket actions">•••</summary><div><button data-support-action="ticket-open" data-id="${esc(item.ticketId)}" type="button">Open ticket</button></div></details></article>`;
  }

  function ticketDetails(ticket, state) {
    const context = ticket.shipmentContext;
    return `<button class="support-back" data-support-action="ticket-back" type="button">‹ Back to tickets</button><div class="support-ticket-detail-layout"><main class="support-ticket-main"><section class="foundation-panel support-ticket-hero"><header><div><span class="foundation-eyebrow">${esc(ticket.ticketId)}</span><h2>${esc(ticket.subject)}</h2><p>${esc(ticket.summary)}</p></div><div>${window.VitaeUI.badge(ticket.priority)} ${window.VitaeUI.badge(ticket.status)}</div></header></section><section class="foundation-panel"><header><div><span class="foundation-eyebrow">Public conversation</span><h2>Conversation History</h2></div></header><div class="support-conversation">${(ticket.messages || []).map(message).join("") || empty("No public messages yet.")}</div><form class="support-reply-form" data-support-form="reply" data-id="${esc(ticket.ticketId)}"><label><span>Reply to user</span><textarea name="message" rows="4" required></textarea></label><div><button class="support-secondary" name="requestMoreInfo" value="true" type="submit">Request More Information</button><button class="foundation-primary" type="submit">Send Reply</button></div></form></section><section class="foundation-panel support-internal-panel"><header><div><span class="foundation-eyebrow">Support only</span><h2>Internal Notes</h2></div><span>Never visible to users</span></header><div class="support-internal-notes">${(ticket.internalNotes || []).map(note).join("") || `<p>No internal notes.</p>`}</div><form data-support-form="note" data-id="${esc(ticket.ticketId)}"><label><span>Add internal note</span><textarea name="note" rows="3" required></textarea></label><button class="support-secondary" type="submit">Save Internal Note</button></form></section><section class="foundation-panel support-resolution-panel"><header><div><span class="foundation-eyebrow">Case controls</span><h2>Resolution</h2></div></header><form data-support-form="update" data-id="${esc(ticket.ticketId)}"><label><span>Priority</span><select name="priority">${options(["low", "medium", "high", "critical"], ticket.priority)}</select></label><label><span>Status</span><select name="status">${options(["new", "in_progress", "waiting_for_user", "escalated", "resolved"], ticket.status)}</select></label><label class="support-wide"><span>Resolution summary</span><textarea name="resolutionSummary" rows="3" placeholder="Required before resolving">${esc(ticket.resolutionSummary)}</textarea></label><div class="support-wide support-resolution-actions"><button class="support-secondary" data-support-action="ticket-escalate" data-id="${esc(ticket.ticketId)}" type="button">Escalate to Admin</button><button class="foundation-primary" type="submit">Save Ticket Changes</button></div></form></section></main><aside class="support-context-panel">${ticketContext(ticket, context)}</aside></div>`;
  }

  function ticketContext(ticket, context) {
    return `<section class="foundation-panel"><header><div><span class="foundation-eyebrow">Customer context</span><h2>Ticket Context</h2></div></header><dl class="support-context-list">${detail("Organization", ticket.organizationName)}${detail("Reporting user", ticket.requester)}${detail("Assigned agent", ticket.assignedAgentName)}${detail("Related shipment", ticket.shipmentId || "Not linked")}</dl></section>${context ? `<section class="foundation-panel"><header><div><span class="foundation-eyebrow">Read-only diagnostics</span><h2>Shipment Condition</h2></div></header><dl class="support-context-list">${detail("Status", human(context.status))}${detail("Current location", context.currentLocation)}${detail("Latest temperature", temperature(context.temperature))}${detail("Required range", range(context))}${detail("Sensor", `${context.sensorId || "Not linked"} · ${human(context.sensorStatus || "unknown")}`)}${detail("Battery health", context.batteryLevel == null ? "Unavailable" : `${context.batteryLevel}%`)}${detail("Last update", shortDate(context.lastUpdated))}</dl><h3>Recent alerts</h3>${(context.recentAlerts || []).length ? `<div class="support-context-alerts">${context.recentAlerts.map((alert) => `<article>${window.VitaeUI.badge(alert.severity)}<span>${esc(alert.explanation)}</span></article>`).join("")}</div>` : empty("No recent alerts.")}<h3>Actions already taken</h3>${(context.actionsTaken || []).length ? `<ul>${context.actionsTaken.map((action) => `<li>${esc(action)}</li>`).join("")}</ul>` : `<p class="support-muted">No actions recorded.</p>`}</section>` : `<section class="foundation-panel">${empty("No shipment is linked to this ticket.")}</section>`}`;
  }

  function shipmentLookup(data, local) {
    const q = local.lookupQuery.toLowerCase(), tickets = data.tickets || [];
    const rows = (data.shipments || []).filter((item) => !q || JSON.stringify(item).toLowerCase().includes(q) || tickets.some((ticket) => ticket.shipmentId === item.shipmentId && ticket.ticketId.toLowerCase().includes(q)));
    return `<section class="foundation-panel"><form class="support-search-form" data-support-form="lookup"><label><span>Shipment ID, organization, driver, or linked ticket</span><input name="query" value="${esc(local.lookupQuery)}" placeholder="Search diagnostic records"></label><button class="foundation-primary" type="submit">Search</button></form></section><section class="foundation-panel"><div class="support-lookup-results">${rows.length ? rows.map((item) => `<article><header><div><strong>${esc(item.shipmentId)}</strong><span>${esc(item.organizationName)} · ${esc(item.driverName || "No driver")}</span></div>${window.VitaeUI.badge(item.status)}</header><dl>${detail("Location", item.currentLocation)}${detail("Temperature", temperature(item.temperature))}${detail("Required range", range(item))}${detail("Sensor", `${item.sensorId || "None"} · ${human(item.sensorStatus || "unknown")}`)}${detail("Battery", item.batteryLevel == null ? "Unavailable" : `${item.batteryLevel}%`)}${detail("Updated", shortDate(item.lastUpdated))}</dl><small>Read-only diagnostic access</small></article>`).join("") : empty("No shipment diagnostics match your search.")}</div></section>`;
  }

  function organizationsPage(data, local) {
    const q = local.organizationQuery.toLowerCase(), items = (data.organizations || []).filter((item) => !q || JSON.stringify(item).toLowerCase().includes(q));
    return `<section class="foundation-panel"><form class="support-search-form" data-support-form="organization-search"><label><span>Search organizations</span><input name="query" value="${esc(local.organizationQuery)}" placeholder="Name, type, contact, or region"></label><button class="foundation-primary" type="submit">Search</button></form></section><div class="support-organization-grid">${items.map((item) => `<section class="foundation-panel"><header><div><span class="foundation-eyebrow">${esc(human(item.type))}</span><h2>${esc(item.name)}</h2></div><span class="support-open-count">${item.openTickets} open</span></header><dl class="support-context-list">${detail("Main contact", item.contact)}${detail("Region", item.region)}</dl><h3>Related ticket history</h3>${queueRows((item.ticketHistory || []).slice(0, 4))}</section>`).join("") || empty("No organizations match your search.")}</div>`;
  }

  function knowledgePage(data, local) {
    const q = local.knowledgeQuery.toLowerCase(), items = (data.knowledgeBase || []).filter((item) => !q || JSON.stringify(item).toLowerCase().includes(q));
    return `<section class="foundation-panel"><form class="support-search-form" data-support-form="knowledge-search"><label><span>Search troubleshooting guidance</span><input name="query" value="${esc(local.knowledgeQuery)}" placeholder="Sensor, battery, GPS, cooling, login..."></label><button class="foundation-primary" type="submit">Search</button></form></section><div class="support-knowledge-grid">${items.map((item) => `<details class="foundation-panel" data-ui-state-key="support-knowledge:${esc(item.articleId || item.id || item.title)}"><summary><span><small>${esc(item.category)}</small><strong>${esc(item.title)}</strong></span><b aria-hidden="true">+</b></summary><p>${esc(item.summary)}</p><ol>${(item.steps || []).map((step) => `<li>${esc(step)}</li>`).join("")}</ol></details>`).join("") || empty("No knowledge article matches your search.")}</div>`;
  }

  function profilePage(state) {
    const assigned = (state.data.assignedTickets || []).length;
    return `<section class="foundation-panel support-profile"><div class="support-avatar" aria-hidden="true">${esc((state.user.name || "S")[0])}</div><span>Support Agent</span><h2>${esc(state.user.name)}</h2><p>Monitoring Center Support</p><dl>${detail("User ID", state.user.userId)}${detail("Assigned open tickets", String(assigned))}${detail("Access", "Ticket resolution and diagnostic context")}</dl><button class="support-secondary" data-logout type="button">Log out</button></section>`;
  }

  async function handleClick(event, state, actions) {
    const button = event.target.closest("[data-support-action]");
    if (!button) return false;
    const local = supportState(state), action = button.dataset.supportAction;
    if (action === "ticket-open") { local.selectedTicketId = button.dataset.id; state.page = state.page === "dashboard" ? "tickets" : state.page; actions.render(); return true; }
    if (action === "trip-open") { local.lookupQuery = button.dataset.id; local.selectedTicketId = null; state.page = "lookup"; actions.render(); return true; }
    if (action === "ticket-back") { local.selectedTicketId = null; actions.render(); return true; }
    if (action === "ticket-page") { local.page = Math.max(1, Number(button.dataset.page)); actions.render(); return true; }
    if (action === "ticket-escalate") { if (!confirm("Escalate this ticket to Admin?")) return true; return perform(async () => { await api(`/api/support/tickets/${button.dataset.id}`, "PATCH", { status: "escalated" }); await actions.reload(); actions.notify("Ticket escalated to Admin."); }, actions); }
    return true;
  }

  async function handleSubmit(event, state, actions) {
    const form = event.target, local = supportState(state), kind = form.dataset.supportForm;
    if (!kind) return false;
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form).entries());
    if (kind === "filters") { local.filters = values; local.page = 1; actions.render(); return true; }
    if (kind === "lookup") { local.lookupQuery = values.query.trim(); actions.render(); return true; }
    if (kind === "organization-search") { local.organizationQuery = values.query.trim(); actions.render(); return true; }
    if (kind === "knowledge-search") { local.knowledgeQuery = values.query.trim(); actions.render(); return true; }
    if (kind === "reply") { values.requestMoreInfo = event.submitter?.name === "requestMoreInfo"; return perform(async () => { await api(`/api/support/tickets/${form.dataset.id}/messages`, "POST", values); await actions.reload(); actions.notify(values.requestMoreInfo ? "Information requested from the user." : "Reply sent."); }, actions); }
    if (kind === "note") return perform(async () => { await api(`/api/support/tickets/${form.dataset.id}/notes`, "POST", values); await actions.reload(); actions.notify("Internal note saved."); }, actions);
    if (kind === "update") { if (values.status === "resolved" && !values.resolutionSummary.trim()) { actions.notify("Resolution summary is required.", "error"); return true; } if (values.status === "resolved" && !confirm("Resolve this ticket with the entered summary?")) return true; return perform(async () => { await api(`/api/support/tickets/${form.dataset.id}`, "PATCH", values); await actions.reload(); actions.notify(values.status === "resolved" ? "Ticket resolved." : "Ticket updated."); }, actions); }
    return false;
  }

  function message(item) { return `<article><header><strong>${esc(item.author)}</strong><time>${shortDate(item.timestamp)}</time></header><p>${esc(item.body)}</p></article>`; }
  function note(item) { return `<article><header><strong>${esc(item.author)}</strong><time>${shortDate(item.timestamp)}</time></header><p>${esc(item.body)}</p></article>`; }
  function detail(label, value) { return `<div><dt>${esc(label)}</dt><dd>${esc(value || "Not available")}</dd></div>`; }
  function options(items, selected = "") { return items.map((item) => `<option value="${esc(item)}" ${String(item) === String(selected) ? "selected" : ""}>${esc(human(item))}</option>`).join(""); }
  function recordOptions(items, key, label, selected = "") { return items.map((item) => `<option value="${esc(item[key])}" ${String(item[key]) === String(selected) ? "selected" : ""}>${esc(item[label])}</option>`).join(""); }
  function priorityRank(value) { return ({ critical: 0, high: 1, medium: 2, low: 3 }[value] ?? 4); }
  function temperature(value) { return typeof value === "number" ? `${value.toFixed(1)}°C` : "No reading"; }
  function range(item) { return item?.safeTemperatureMin == null || item?.safeTemperatureMax == null ? "Not specified" : `${item.safeTemperatureMin}°C to ${item.safeTemperatureMax}°C`; }
  function shortDate(value) { if (!value) return "Not recorded"; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); }
  function human(value) { return window.VitaeUI.humanize(value); }
  function esc(value) { return window.VitaeUI.escape(value ?? ""); }
  function empty(value) { return window.VitaeUI.empty(value); }
  async function api(url, method, body) { return window.VitaeAuth.api(url, { method, body: JSON.stringify(body) }); }
  async function perform(operation, actions) { try { await operation(); } catch (error) { actions.notify(error.message, "error"); } return true; }

  window.VitaeSupport = { render, handleClick, handleSubmit };
})();
