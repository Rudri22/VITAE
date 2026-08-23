const assert = require("node:assert/strict");

global.window = {
  VitaeUI: {
    badge: (value) => `<b>${String(value)}</b>`,
    empty: (value) => String(value),
    escape: (value) => String(value ?? ""),
    humanize: (value) => String(value ?? "").replaceAll("-", " ").replaceAll("_", " "),
    pageHeader: () => "",
    shell: ({ content }) => content,
  },
};
global.crypto = { randomUUID: () => "test-id" };
global.FormData = class {
  constructor(form) { this.form = form; }
  entries() { return Object.entries(this.form.values || {}); }
};
global.confirm = () => true;

require("./roles/organization.js");
require("./roles/driver.js");

function mappedShipment(overrides = {}) {
  return {
    shipmentId: "shipment-v2",
    productName: "GARDASIL 9",
    productCategory: "Vaccines",
    origin: "Origin",
    pickup: "Origin",
    destination: "Destination",
    destinationHospitalName: "Destination",
    driverId: "driver-aya",
    driverName: "Aya",
    status: "accepted",
    riskLevel: "low",
    lotTripId: "lot-trip-shipment-v2",
    tripId: "trip-shipment-v2",
    tripStatus: "PLANNED",
    productRuleVersion: "catalog-version",
    safeTemperatureMin: 2,
    safeTemperatureMax: 8,
    ...overrides,
  };
}

function organizationState(shipment, tripStatus, liveState = null) {
  return {
    user: {},
    data: { organization: { name: "Organization" }, shipments: [shipment], activeShipments: [], summary: {} },
    organization: { draft: {}, filters: {}, selectedShipmentId: shipment.shipmentId },
    live: { shipments: [] },
    v2Monitoring: shipment.lotTripId ? {
      status: "ready",
      shipmentId: shipment.shipmentId,
      data: {
        tripIdentity: {
          tripId: shipment.tripId,
          lotTripId: shipment.lotTripId,
          productId: "gardasil-9",
          origin: shipment.origin,
          destination: shipment.destinationHospitalName,
          status: tripStatus,
        },
        liveState,
        latestAlert: null,
      },
    } : { status: "not_mapped" },
  };
}

function driverState(shipment, group) {
  const data = {
    driver: { name: "Aya" },
    deliveryRequests: [],
    acceptedDeliveries: [],
    activeDeliveries: [],
    completedDeliveries: [],
  };
  data[group].push(shipment);
  if (group === "activeDeliveries") data.activeDelivery = shipment;
  return { user: { name: "Aya" }, data, driverUi: { deliveryTab: group === "completedDeliveries" ? "history" : "accepted", checks: {}, activeShipmentId: shipment.shipmentId } };
}

async function lifecycleRefreshContract() {
  const shipment = mappedShipment();
  const state = driverState(shipment, "acceptedDeliveries");
  state.driverUi.checks[shipment.shipmentId] = {
    shipmentCollected: true,
    containerClosed: true,
    sensorConnected: true,
    coolingActive: true,
    vehicleReady: true,
  };
  const calls = [];
  let reloads = 0;
  window.VitaeAuth = { api: async (url) => { calls.push(url); return {}; } };
  const actions = { reload: async () => { reloads += 1; }, notify() {}, render() {} };

  await window.VitaeDriver.handleSubmit(
    { target: { dataset: { driverForm: "start", id: shipment.shipmentId } }, preventDefault() {} },
    state,
    actions,
  );
  assert.equal(calls.at(-1), `/api/driver/shipments/${shipment.shipmentId}/start`);
  assert.equal(reloads, 1);

  await window.VitaeDriver.handleSubmit(
    {
      target: {
        dataset: { driverForm: "complete", id: shipment.shipmentId },
        values: { confirmedArrival: "true", receiverSignature: "data:image/png;base64,c2ln" },
      },
      preventDefault() {},
    },
    state,
    actions,
  );
  assert.equal(calls.at(-1), `/api/driver/shipments/${shipment.shipmentId}/complete`);
  assert.equal(reloads, 2);
}

async function main() {
  for (const status of ["PLANNED", "ACTIVE", "COMPLETED"]) {
    const shipment = mappedShipment({ tripStatus: status });
    const orgHtml = window.VitaeOrganization.render(
      organizationState(shipment, status, status === "PLANNED" ? null : { status: "SAFE" }),
      "dashboard",
    );
    assert.match(orgHtml, new RegExp(`Trip lifecycle[\\s\\S]*${status}`));
    assert.match(orgHtml, /Delivery workflow/);
    assert.match(orgHtml, /Current condition/);
    assert.match(orgHtml, /ProductRules/);
    if (status === "COMPLETED") assert.match(orgHtml, /Trip completed/);

    const group = status === "PLANNED" ? "acceptedDeliveries" : status === "ACTIVE" ? "activeDeliveries" : "completedDeliveries";
    const page = status === "ACTIVE" ? "trip" : "deliveries";
    const driverHtml = window.VitaeDriver.render(driverState(shipment, group), page);
    assert.match(driverHtml, /V2 trip/);
    assert.match(driverHtml, new RegExp(status));
  }

  const legacy = mappedShipment({ shipmentId: "legacy-only", lotTripId: null, tripId: null, tripStatus: null });
  const orgLegacyHtml = window.VitaeOrganization.render(organizationState(legacy, null), "shipments");
  const driverLegacyHtml = window.VitaeDriver.render(driverState(legacy, "acceptedDeliveries"), "deliveries");
  assert.doesNotMatch(orgLegacyHtml, /V2 trip lifecycle|V2 monitoring identity/);
  assert.doesNotMatch(driverLegacyHtml, /V2 trip lifecycle|V2 trip/);

  await lifecycleRefreshContract();
  console.log("V2 lifecycle view contract tests passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
