const assert = require("node:assert/strict");

const httpCalls = [];
global.window = {
  VitaeAuth: {
    api: async (url, options = {}) => {
      httpCalls.push({ url, options });
      return { success: true, alerts: [], alert: {} };
    },
  },
  VitaeUI: {
    badge: (value) => `<b>${String(value)}</b>`,
    empty: (value) => String(value),
    escape: (value) => String(value ?? ""),
    humanize: (value) => String(value ?? "").replaceAll("_", " ").replaceAll("-", " "),
    pageHeader: () => "",
    shell: ({ content }) => content,
  },
};
global.crypto = { randomUUID: () => "test-id" };
global.confirm = () => true;
global.FormData = class {
  constructor(form) { this.form = form; }
  entries() { return Object.entries(this.form.values || {}); }
};

require("./services/v2AlertApi.js");
require("./roles/organization.js");
require("./roles/driver.js");

const mappedShipment = {
  shipmentId: "shipment-v2",
  lotTripId: "lot/trip v2",
  tripStatus: "ACTIVE",
  productName: "Catalog Product",
  productCategory: "Vaccines",
  status: "in_transit",
  riskLevel: "low",
  origin: "Origin",
  destination: "Destination",
  destinationHospitalName: "Destination",
};
const legacyShipment = {
  ...mappedShipment,
  shipmentId: "shipment-legacy",
  lotTripId: null,
};
const v2Alert = {
  alertId: "alert/v2 1",
  lotTripId: mappedShipment.lotTripId,
  shipmentId: mappedShipment.shipmentId,
  alertType: "EXCURSION_MONITOR",
  severity: "INFO",
  status: "OPEN",
  sourceStatus: "MONITOR",
  reasonCode: "PERMITTED_EXCURSION",
  message: "Authoritative V2 alert",
  recommendedAction: "Continue monitoring",
  detectedAt: "2026-08-19T18:10:00Z",
  updatedAt: "2026-08-19T18:10:00Z",
  actions: [],
};

function legacyAlert(shipmentId, message) {
  return {
    alertId: `legacy-${shipmentId}`,
    shipmentId,
    type: "legacy warning",
    severity: "high",
    status: "new",
    message,
    explanation: message,
    instruction: "Legacy instruction",
    recommendedAction: "Legacy action",
    detectedAt: "2026-08-19T18:00:00Z",
    updatedAt: "2026-08-19T18:00:00Z",
  };
}

function organizationState() {
  return {
    user: {},
    data: {
      organization: { name: "Organization" },
      shipments: [mappedShipment, legacyShipment],
      alerts: [
        legacyAlert(mappedShipment.shipmentId, "mapped legacy alert must be hidden"),
        legacyAlert(legacyShipment.shipmentId, "legacy-only alert remains"),
      ],
    },
    organization: { draft: {}, filters: {} },
    live: { shipments: [] },
    v2Alerts: { status: "ready", alerts: [v2Alert], error: null },
  };
}

function driverState() {
  return {
    user: { name: "Driver" },
    data: {
      driver: { name: "Driver", organizationContact: "ops@example.test" },
      deliveryRequests: [],
      acceptedDeliveries: [],
      activeDeliveries: [mappedShipment],
      completedDeliveries: [legacyShipment],
      alerts: [
        legacyAlert(mappedShipment.shipmentId, "mapped legacy alert must be hidden"),
        legacyAlert(legacyShipment.shipmentId, "legacy-only alert remains"),
      ],
    },
    driverUi: { checks: {} },
    v2Alerts: { status: "ready", alerts: [v2Alert], error: null },
  };
}

async function clientContract() {
  httpCalls.length = 0;
  await window.VitaeV2AlertApi.listAlerts("lot/trip v2");
  await window.VitaeV2AlertApi.getAlert("lot/trip v2", "alert/v2 1");
  await window.VitaeV2AlertApi.acknowledge("lot/trip v2", "alert/v2 1");
  await window.VitaeV2AlertApi.recordAction("lot/trip v2", "alert/v2 1", " Inspected cooling ");
  await window.VitaeV2AlertApi.resolve("lot/trip v2", "alert/v2 1", " Reviewed and closed ");

  const base = "/api/v2/alerts/lot%2Ftrip%20v2";
  assert.deepEqual(httpCalls.map((call) => call.url), [
    base,
    `${base}/alert%2Fv2%201`,
    `${base}/alert%2Fv2%201/acknowledge`,
    `${base}/alert%2Fv2%201/actions`,
    `${base}/alert%2Fv2%201/resolve`,
  ]);
  assert.equal(httpCalls[2].options.method, "POST");
  assert.deepEqual(JSON.parse(httpCalls[3].options.body), { description: "Inspected cooling" });
  assert.deepEqual(JSON.parse(httpCalls[4].options.body), { resolutionNote: "Reviewed and closed" });
}

async function roleContract() {
  const orgHtml = window.VitaeOrganization.render(organizationState(), "alerts");
  assert.match(orgHtml, /Authoritative V2 alert/);
  assert.match(orgHtml, /What happened/);
  assert.match(orgHtml, /Recommended action/);
  assert.match(orgHtml, /Supporting evidence/);
  assert.match(orgHtml, /Condition at detection/);
  assert.match(orgHtml, /MONITOR/);
  assert.match(orgHtml, /v2-alert-ack/);
  assert.match(orgHtml, /v2-alert-resolve/);
  assert.doesNotMatch(orgHtml, /mapped legacy alert must be hidden/);
  assert.match(orgHtml, /legacy-only alert remains/);

  const driverHtml = window.VitaeDriver.render(driverState(), "alerts");
  assert.match(driverHtml, /Authoritative V2 alert/);
  assert.match(driverHtml, /What happened/);
  assert.match(driverHtml, /Recommended action/);
  assert.match(driverHtml, /Supporting evidence/);
  assert.match(driverHtml, /Condition at detection/);
  assert.match(driverHtml, /v2-alert-ack/);
  assert.match(driverHtml, /v2-alert-action/);
  assert.doesNotMatch(driverHtml, /v2-alert-resolve/);
  assert.doesNotMatch(driverHtml, /mapped legacy alert must be hidden/);
  assert.match(driverHtml, /legacy-only alert remains/);

  const driverHomeState = driverState();
  driverHomeState.data.activeDelivery = {
    ...mappedShipment,
    conditionStatus: "RULE_VIOLATION",
    temperature: 9,
  };
  driverHomeState.v2Alerts.alerts[0] = {
    ...v2Alert,
    severity: "CRITICAL",
    sourceStatus: "RULE_VIOLATION",
    message: "Excursion limit reached",
    recommendedAction: "Stop transport and replace the affected shipment",
  };
  const driverHomeHtml = window.VitaeDriver.render(driverHomeState, "home");
  assert.match(driverHomeHtml, /Current condition/);
  assert.match(driverHomeHtml, /RULE_VIOLATION/);
  assert.match(driverHomeHtml, /Excursion limit reached/);
  assert.match(driverHomeHtml, /Required action/);
  assert.match(driverHomeHtml, /Stop transport and replace the affected shipment/);

  const commands = [];
  const actions = {
    render() {},
    notify() {},
    v2AlertCommand: async (...args) => commands.push(args),
  };
  const button = {
    dataset: {
      orgAction: "v2-alert-ack",
      driverAction: "v2-alert-ack",
      lotTripId: mappedShipment.lotTripId,
      id: v2Alert.alertId,
    },
  };
  const event = {
    target: {
      matches: () => false,
      closest: () => button,
    },
  };
  await window.VitaeOrganization.handleClick(event, organizationState(), actions);
  await window.VitaeDriver.handleClick(event, driverState(), actions);

  const actionForm = {
    dataset: {
      orgForm: "v2-alert-action",
      lotTripId: mappedShipment.lotTripId,
      id: v2Alert.alertId,
    },
    values: { description: "Inspected cooling" },
  };
  await window.VitaeOrganization.handleSubmit(
    { target: actionForm, preventDefault() {} },
    organizationState(),
    actions,
  );
  const driverActionForm = {
    dataset: {
      driverForm: "v2-alert-action",
      lotTripId: mappedShipment.lotTripId,
      id: v2Alert.alertId,
    },
    values: { description: "Checked cooling unit" },
  };
  await window.VitaeDriver.handleSubmit(
    { target: driverActionForm, preventDefault() {} },
    driverState(),
    actions,
  );
  const resolveForm = {
    dataset: {
      orgForm: "v2-alert-resolve",
      lotTripId: mappedShipment.lotTripId,
      id: v2Alert.alertId,
    },
    values: { resolutionNote: "Disposition recorded" },
  };
  await window.VitaeOrganization.handleSubmit(
    { target: resolveForm, preventDefault() {} },
    organizationState(),
    actions,
  );
  assert.deepEqual(commands, [
    [mappedShipment.lotTripId, v2Alert.alertId, "acknowledge"],
    [mappedShipment.lotTripId, v2Alert.alertId, "acknowledge"],
    [mappedShipment.lotTripId, v2Alert.alertId, "action", { description: "Inspected cooling" }],
    [mappedShipment.lotTripId, v2Alert.alertId, "action", { description: "Checked cooling unit" }],
    [mappedShipment.lotTripId, v2Alert.alertId, "resolve", { resolutionNote: "Disposition recorded" }],
  ]);
}

async function main() {
  await clientContract();
  await roleContract();
  console.log("V2 alert frontend contract tests passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
