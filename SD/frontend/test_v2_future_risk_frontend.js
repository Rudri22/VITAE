const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

global.window = {
  VitaeUI: {
    badge: (value, tone) => `<b data-tone="${tone || ""}">${String(value)}</b>`,
    empty: (value) => String(value),
    escape: (value) => String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;"),
    humanize: (value) => String(value ?? "").replaceAll("_", " "),
    pageHeader: () => "",
    shell: ({ content }) => content,
  },
};
global.crypto = { randomUUID: () => "test-id" };

require("./roles/organization.js");
require("./services/v2MonitoringApi.js");

function state(futureRisk, status = "SAFE", decisionOverrides = {}, journeyRisk) {
  const shipment = {
    shipmentId: "shipment-v2",
    lotTripId: "lot-trip-v2",
    status: "in_transit",
  };
  const data = {
    tripIdentity: {
      lotTripId: shipment.lotTripId,
      productId: "gardasil-9",
      origin: "Origin",
      destination: "Destination",
      status: "ACTIVE",
    },
    liveState: { status, latestTemperature: 6.1, lastUpdated: "2026-08-21T11:32:00Z" },
    latestAlert: null,
    telemetrySource: "REAL_DEVICE",
  };
  if (futureRisk !== undefined) data.futureRisk = futureRisk;
  if (journeyRisk !== undefined) data.journeyRisk = journeyRisk;
  data.operationalDecision = {
    currentStatus: status,
    futureRiskProbability: futureRisk?.adverseEventProbability ?? null,
    futureRiskCategory: "LOW",
    futureRiskSource: futureRisk?.state === "PREDICTED" ? "FIXED_30_MINUTE_FALLBACK" : null,
    recommendedAction: "CONTINUE",
    urgency: "ROUTINE",
    reason: "Current deterministic controls are safe.",
    journeyContext: {},
    rerouting: {
      status: "INSUFFICIENT_ROUTE_DATA",
      currentDestination: null,
      recommendedCandidate: null,
      alternativesConsidered: 0,
      reason: "The configured destination could not be resolved.",
    },
    ...decisionOverrides,
  };
  return {
    user: {},
    data: {
      organization: { name: "Organization" },
      shipments: [shipment],
      activeShipments: [],
      summary: {},
    },
    v2Monitoring: { status: "ready", shipmentId: shipment.shipmentId, data },
  };
}

function render(futureRisk, status, decisionOverrides, journeyRisk) {
  return window.VitaeOrganization.render(
    state(futureRisk, status, decisionOverrides, journeyRisk), "dashboard",
  );
}

function predicted(overrides = {}) {
  return {
    state: "PREDICTED",
    adverseEventProbability: 0.08421,
    predictionHorizonMinutes: 30,
    cutoffAt: "2026-08-21T11:32:00Z",
    trainingSourceKind: "APPROVED_SIMULATOR",
    limitations: ["Backend-only diagnostic text"],
    ...overrides,
  };
}

async function main() {
  const predictedHtml = render(predicted(), "SAFE");
  assert.match(predictedHtml, /Current condition[\s\S]*SAFE[\s\S]*Authoritative ProductRules/i);
  assert.match(predictedHtml, /Future risk[\s\S]*LOW[\s\S]*8%/i);
  assert.match(predictedHtml, /next 30 minutes/i);
  assert.match(predictedHtml, /30-minute fallback/i);
  assert.match(predictedHtml, /Simulator-based engineering evaluation/i);
  assert.match(predictedHtml, /Not real-world performance/i);
  assert.match(predictedHtml, /Not clinical validation/i);
  assert.doesNotMatch(predictedHtml, /Backend-only diagnostic text/);
  assert.match(predictedHtml, /Recommended action[\s\S]*Continue/i);
  assert.match(predictedHtml, /Rerouting unavailable/i);
  assert.match(predictedHtml, /Telemetry[\s\S]*Real device[\s\S]*Delayed/i);

  const journeyHtml = render(predicted(), "SAFE", {
    futureRiskProbability: 0.72,
    futureRiskCategory: "HIGH",
    futureRiskSource: "JOURNEY_AWARE_MODEL",
    futureRiskHorizonMinutes: 97,
    recommendedAction: "INTERVENE",
  }, {
    available: true,
    probability: 0.72,
    horizonMinutes: 97,
    horizon: "UNTIL_DESTINATION",
    target: "DETERIORATION_BEFORE_DESTINATION",
    source: "JOURNEY_AWARE_MODEL",
  });
  assert.match(journeyHtml, /Risk before destination[\s\S]*HIGH[\s\S]*72%/i);
  assert.match(journeyHtml, /Remaining route[\s\S]*1 h 37 min/i);
  assert.match(journeyHtml, /Journey-aware forecast/i);
  assert.match(journeyHtml, /Additional forecast[\s\S]*8% risk in next 30 min/i);
  assert.doesNotMatch(journeyHtml, /<h3[^>]*>30-min future risk/i);

  const missingJourneyHtml = render(predicted(), "SAFE", {
    futureRiskSource: "FIXED_30_MINUTE_FALLBACK",
    futureRiskHorizonMinutes: 30,
  }, { available: false, reason: "REMAINING_JOURNEY_DURATION_UNAVAILABLE" });
  assert.match(missingJourneyHtml, /Future risk[\s\S]*8%/i);
  assert.match(missingJourneyHtml, /30-minute fallback/i);

  const rerouteHtml = render(predicted({ adverseEventProbability: 0.72 }), "SAFE", {
    futureRiskProbability: 0.72,
    futureRiskCategory: "HIGH",
    recommendedAction: "REROUTE",
    urgency: "URGENT",
    reason: "Eligible facility reduces estimated travel time by 42.0 minutes.",
    journeyContext: { estimatedJourneyProgress: 0.64 },
    rerouting: {
      status: "REROUTE_RECOMMENDED",
      currentDestination: { displayName: "Current destination", etaMinutes: 86 },
      recommendedCandidate: { displayName: "Closer Receiving Center", etaMinutes: 44, capabilityBasis: "ENGINEERING_DEMO_METADATA" },
      alternativesConsidered: 3,
      reason: "Eligible facility reduces estimated travel time by 42.0 minutes.",
      routingEvidenceQuality: "ROUTE_DURATION",
    },
  });
  assert.match(rerouteHtml, /Rerouting recommendation/i);
  assert.match(rerouteHtml, /Recommended facility[\s\S]*Closer Receiving Center[\s\S]*44 min/i);
  assert.match(rerouteHtml, /Current destination[\s\S]*1 h 26 min/i);
  assert.match(rerouteHtml, /Estimated time saved[\s\S]*42 min/i);
  assert.match(rerouteHtml, /Estimated journey progress[\s\S]*64%/i);
  assert.match(rerouteHtml, /Compatibility[\s\S]*Compatible with shipment profile/i);
  assert.match(rerouteHtml, /Evidence[\s\S]*Demo capability profile/i);
  assert.doesNotMatch(rerouteHtml, /ENGINEERING_DEMO_METADATA/);
  assert.match(rerouteHtml, /reduces estimated travel time by 42\.0 minutes/i);

  const fallbackHtml = render(predicted({ adverseEventProbability: 0.72 }), "SAFE", {
    recommendedAction: "REROUTE",
    rerouting: {
      status: "REROUTE_RECOMMENDED",
      currentDestination: { displayName: "Current", distanceKm: 12 },
      recommendedCandidate: { displayName: "Fallback", distanceKm: 7, capabilityBasis: "DEMO" },
      routingEvidenceQuality: "STRAIGHT_LINE_DISTANCE",
      reason: "Closer by geometry.",
    },
  });
  assert.match(fallbackHtml, /Road ETA unavailable[\s\S]*distance fallback/i);
  assert.doesNotMatch(fallbackHtml, /7\.0 km[^<]*(min|hour)/i);

  const absentHtml = render(undefined);
  assert.match(absentHtml, /data-future-risk-state="NOT_PREDICTED"/);
  assert.match(absentHtml, /Forecast unavailable/);
  assert.doesNotMatch(absentHtml, /0\.0%/);

  const notConfiguredHtml = render({ state: "NOT_CONFIGURED" });
  assert.match(notConfiguredHtml, /Prediction is not configured/);

  const waitingState = state({ state: "NOT_CONFIGURED" }, "SAFE");
  waitingState.v2Monitoring.data.liveState = null;
  waitingState.v2Monitoring.data.telemetrySource = null;
  waitingState.v2Monitoring.data.operationalDecision.currentStatus = null;
  const waitingHtml = window.VitaeOrganization.render(waitingState, "dashboard");
  assert.match(waitingHtml, /Current condition[\s\S]*No telemetry yet/i);
  assert.match(waitingHtml, /Waiting for the first accepted device reading/i);
  assert.match(waitingHtml, /Telemetry[\s\S]*Unavailable[\s\S]*No accepted reading/i);
  assert.doesNotMatch(waitingHtml, /0%/);

  const completedState = state({ state: "NOT_PREDICTED", reasonCode: "TRIP_NOT_ACTIVE" }, "SAFE");
  completedState.v2Monitoring.data.tripIdentity.status = "COMPLETED";
  completedState.v2Monitoring.data.tripIdentity.completedAt = "2026-08-21T12:05:00Z";
  const completedHtml = window.VitaeOrganization.render(completedState, "dashboard");
  assert.match(completedHtml, /Trip completed[\s\S]*Final accepted condition remains visible/i);
  assert.doesNotMatch(completedHtml, /undefined|NaN/i);

  const reasons = {
    NO_ACCEPTED_TELEMETRY: "Waiting for telemetry",
    CURRENT_STATUS_NOT_ELIGIBLE: "Forecast not applicable for current status",
    TRIP_NOT_ACTIVE: "Forecast unavailable for inactive trip",
    HISTORY_NOT_COHERENT: "Forecast temporarily unavailable",
    CONCURRENT_UPDATE: "Updating telemetry - try again shortly",
    INFERENCE_UNAVAILABLE: "Forecast temporarily unavailable",
  };
  for (const [reasonCode, message] of Object.entries(reasons)) {
    const html = render({ state: "NOT_PREDICTED", reasonCode, detail: "unsafe raw detail" });
    assert.match(html, new RegExp(message.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    assert.doesNotMatch(html, /unsafe raw detail/);
    assert.doesNotMatch(html, /0\.0%/);
  }

  for (const malformed of [
    predicted({ adverseEventProbability: Number.NaN }),
    predicted({ adverseEventProbability: Number.POSITIVE_INFINITY }),
    predicted({ adverseEventProbability: -0.1 }),
    predicted({ adverseEventProbability: 1.1 }),
    predicted({ adverseEventProbability: "0.5" }),
    predicted({ predictionHorizonMinutes: 60 }),
    predicted({ cutoffAt: "not-a-date" }),
  ]) {
    const html = render(malformed, "CRITICAL");
    assert.match(html, /Forecast unavailable/);
    assert.match(html, /Current condition[\s\S]*CRITICAL/i);
    assert.doesNotMatch(html, /NaN%|Infinity%|undefined%/);
  }

  const source = fs.readFileSync(path.join(__dirname, "roles", "organization.js"), "utf8");
  assert.doesNotMatch(source, /adverseEventProbability\s*[><]=?\s*0\./);
  assert.doesNotMatch(source, /fetch\([^)]*future|model.*artifact|joblib/i);

  const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
  assert.match(css, /--vitae-space-1:\s*8px/);
  assert.match(css, /\.org-monitor-primary-grid\s*\{[^}]*display:\s*grid/);
  assert.match(css, /@media \(max-width: 820px\)[\s\S]*\.org-monitor-primary-grid\s*\{\s*grid-template-columns:\s*1fr/);

  const api = fs.readFileSync(path.join(__dirname, "services", "v2MonitoringApi.js"), "utf8");
  assert.equal((api.match(/\/api\/v2\/monitor\/live\//g) || []).length, 1);

  const monitoringResponse = {
    liveState: { status: "SAFE" },
    futureRisk: predicted(),
  };
  const requests = [];
  window.VitaeAuth = {
    api: async (url) => {
      requests.push(url);
      return monitoringResponse;
    },
  };
  const fetched = await window.VitaeV2MonitoringApi.fetchLive("lot-trip-v2");
  assert.equal(fetched, monitoringResponse);
  assert.deepEqual(requests, ["/api/v2/monitor/live/lot-trip-v2"]);
  console.log("V2 future-risk frontend contract tests passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
