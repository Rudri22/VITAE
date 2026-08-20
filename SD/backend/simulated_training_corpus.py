import json
import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Mapping, Tuple

try:
    from .alerting import InMemoryAlertRepository
    from .completed_trip_dataset import (
        CompletedTripDatasetRecord,
        CompletedTripDatasetService,
        completed_trip_dataset_jsonl,
    )
    from .operational_service import OperationalTelemetryService
    from .product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
    )
    from .shipment_access import InMemoryIdentityAccessRepository
    from .simulator import ScenarioPoint, SimulationScenario, run_scenario
    from .telemetry_processor import TelemetryProcessor
    from .temporal_risk_baseline import (
        TemporalRiskTrainingDataset,
        TrainingSourceKind,
        temporal_risk_dataset_fingerprint,
    )
    from .temporal_risk_examples import (
        build_temporal_risk_dataset,
        temporal_risk_examples_jsonl,
    )
    from .trip_identity import DeviceAssignment, TripIdentity, TripStatus
except ImportError:
    from alerting import InMemoryAlertRepository
    from completed_trip_dataset import (
        CompletedTripDatasetRecord,
        CompletedTripDatasetService,
        completed_trip_dataset_jsonl,
    )
    from operational_service import OperationalTelemetryService
    from product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
    )
    from shipment_access import InMemoryIdentityAccessRepository
    from simulator import ScenarioPoint, SimulationScenario, run_scenario
    from telemetry_processor import TelemetryProcessor
    from temporal_risk_baseline import (
        TemporalRiskTrainingDataset,
        TrainingSourceKind,
        temporal_risk_dataset_fingerprint,
    )
    from temporal_risk_examples import (
        build_temporal_risk_dataset,
        temporal_risk_examples_jsonl,
    )
    from trip_identity import DeviceAssignment, TripIdentity, TripStatus


SIMULATED_CORPUS_SCHEMA = "vitae.approved_simulator_corpus_manifest"
SIMULATED_CORPUS_SCHEMA_VERSION = 1
SIMULATED_CORPUS_SOURCE_ID = "vitae-approved-simulator-corpus-v1"
SIMULATED_CORPUS_DEFAULT_SEED = 20260820
SIMULATED_CORPUS_DEFAULT_TRIP_COUNT = 60
SIMULATED_CORPUS_START = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)

STABLE_SAFE = "stable-safe"
RECOVERABLE_EXCURSION = "recoverable-excursion"
GRADUAL_WARMING_ADVERSE = "gradual-warming-adverse"
SUSTAINED_HIGH_EXCURSION = "sustained-high-excursion"
SUSTAINED_LOW_EXCURSION = "sustained-low-excursion"
SIMULATED_SCENARIO_FAMILIES = (
    STABLE_SAFE,
    RECOVERABLE_EXCURSION,
    GRADUAL_WARMING_ADVERSE,
    SUSTAINED_HIGH_EXCURSION,
    SUSTAINED_LOW_EXCURSION,
)


@dataclass(frozen=True)
class SimulatedCorpusConfig:
    trip_count: int = SIMULATED_CORPUS_DEFAULT_TRIP_COUNT
    master_seed: int = SIMULATED_CORPUS_DEFAULT_SEED
    start_at: datetime = SIMULATED_CORPUS_START
    source_id: str = SIMULATED_CORPUS_SOURCE_ID


@dataclass(frozen=True)
class SimulatedTripSummary:
    lot_trip_id: str
    scenario_family: str
    scenario_seed: int
    nominal_cadence_minutes: int
    telemetry_count: int
    first_sample_at: datetime
    last_sample_at: datetime
    status_counts: Tuple[Tuple[str, int], ...]


@dataclass(frozen=True)
class SimulatedCorpusManifest:
    schema: str
    schema_version: int
    source_id: str
    source_kind: str
    master_seed: int
    requested_trip_count: int
    generated_trip_count: int
    corpus_start_at: datetime
    scenario_family_counts: Tuple[Tuple[str, int], ...]
    trip_summaries: Tuple[SimulatedTripSummary, ...]
    completed_history_sha256: str
    temporal_examples_sha256: str


@dataclass(frozen=True)
class SimulatedTrainingCorpus:
    records: Tuple[CompletedTripDatasetRecord, ...]
    training_dataset: TemporalRiskTrainingDataset
    manifest: SimulatedCorpusManifest


class SimulatedTrainingCorpusError(ValueError):
    pass


def build_approved_simulator_corpus(
    config: SimulatedCorpusConfig = SimulatedCorpusConfig(),
) -> SimulatedTrainingCorpus:
    value = _validate_config(config)
    master = random.Random(value.master_seed)
    families = [
        SIMULATED_SCENARIO_FAMILIES[index % len(SIMULATED_SCENARIO_FAMILIES)]
        for index in range(value.trip_count)
    ]
    master.shuffle(families)

    records = []
    summaries = []
    for trip_index, family in enumerate(families):
        scenario_seed = master.getrandbits(63)
        scenario_random = random.Random(scenario_seed)
        nominal_cadence = scenario_random.choice((10, 15, 20))
        scenario = _build_scenario(
            trip_index,
            family,
            scenario_random,
            nominal_cadence,
        )
        start_at = value.start_at + timedelta(days=trip_index * 5)
        record, summary = _run_completed_trip(
            trip_index,
            family,
            scenario_seed,
            nominal_cadence,
            scenario,
            start_at,
        )
        records.append(record)
        summaries.append(summary)

    records = tuple(sorted(records, key=lambda item: item.lot_trip_id))
    examples = build_temporal_risk_dataset(records)
    training_dataset = TemporalRiskTrainingDataset(
        source_id=value.source_id,
        source_kind=TrainingSourceKind.APPROVED_SIMULATOR,
        examples=examples,
    )
    completed_jsonl = completed_trip_dataset_jsonl(records)
    family_counts = Counter(families)
    manifest = SimulatedCorpusManifest(
        schema=SIMULATED_CORPUS_SCHEMA,
        schema_version=SIMULATED_CORPUS_SCHEMA_VERSION,
        source_id=value.source_id,
        source_kind=TrainingSourceKind.APPROVED_SIMULATOR.value,
        master_seed=value.master_seed,
        requested_trip_count=value.trip_count,
        generated_trip_count=len(records),
        corpus_start_at=value.start_at,
        scenario_family_counts=tuple(sorted(family_counts.items())),
        trip_summaries=tuple(sorted(summaries, key=lambda item: item.lot_trip_id)),
        completed_history_sha256=sha256(
            completed_jsonl.encode("utf-8")
        ).hexdigest(),
        temporal_examples_sha256=temporal_risk_dataset_fingerprint(
            training_dataset
        ),
    )
    return SimulatedTrainingCorpus(
        records=records,
        training_dataset=training_dataset,
        manifest=manifest,
    )


def persist_approved_simulator_corpus(
    corpus: SimulatedTrainingCorpus,
    directory,
) -> Mapping[str, Path]:
    if not isinstance(corpus, SimulatedTrainingCorpus):
        raise SimulatedTrainingCorpusError(
            "corpus must be a SimulatedTrainingCorpus"
        )
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    completed_path = destination / "completed_trip_histories.jsonl"
    examples_path = destination / "temporal_risk_examples.jsonl"
    manifest_path = destination / "manifest.json"
    completed_path.write_text(
        completed_trip_dataset_jsonl(corpus.records), encoding="utf-8"
    )
    examples_path.write_text(
        temporal_risk_examples_jsonl(corpus.training_dataset.examples),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            simulated_corpus_manifest_document(corpus.manifest),
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "completed_trip_histories": completed_path,
        "temporal_risk_examples": examples_path,
        "manifest": manifest_path,
    }


def simulated_corpus_manifest_document(manifest: SimulatedCorpusManifest) -> dict:
    if not isinstance(manifest, SimulatedCorpusManifest):
        raise SimulatedTrainingCorpusError(
            "manifest must be a SimulatedCorpusManifest"
        )
    return {
        "schema": manifest.schema,
        "schema_version": manifest.schema_version,
        "source_id": manifest.source_id,
        "source_kind": manifest.source_kind,
        "master_seed": manifest.master_seed,
        "requested_trip_count": manifest.requested_trip_count,
        "generated_trip_count": manifest.generated_trip_count,
        "corpus_start_at": _utc_text(manifest.corpus_start_at),
        "scenario_family_counts": dict(manifest.scenario_family_counts),
        "trip_summaries": [
            {
                "lot_trip_id": item.lot_trip_id,
                "scenario_family": item.scenario_family,
                "scenario_seed": item.scenario_seed,
                "nominal_cadence_minutes": item.nominal_cadence_minutes,
                "telemetry_count": item.telemetry_count,
                "first_sample_at": _utc_text(item.first_sample_at),
                "last_sample_at": _utc_text(item.last_sample_at),
                "status_counts": dict(item.status_counts),
            }
            for item in manifest.trip_summaries
        ],
        "completed_history_sha256": manifest.completed_history_sha256,
        "temporal_examples_sha256": manifest.temporal_examples_sha256,
        "provenance": {
            "statuses": "persisted deterministic StatusDecisionRecord values",
            "labels": "derived from future persisted decisions within the v1 horizon",
            "product_rules": "canonical verified backend ProductRules",
            "performance_scope": "simulator engineering validation only",
        },
    }


def _run_completed_trip(
    trip_index,
    family,
    scenario_seed,
    nominal_cadence,
    scenario,
    start_at,
):
    repository = InMemoryIdentityAccessRepository()
    trip_id = f"sim-corpus-trip-{trip_index:04d}"
    lot_trip_id = f"sim-corpus-lot-trip-{trip_index:04d}"
    device_id = f"sim-corpus-device-{trip_index:04d}"
    assignment_id = f"sim-corpus-assignment-{trip_index:04d}"
    trip = TripIdentity(
        trip_id=trip_id,
        lot_trip_id=lot_trip_id,
        lot_id=f"sim-corpus-lot-{trip_index:04d}",
        device_id=device_id,
        product_id=GARDASIL_9_PRODUCT_ID,
        presentation=GARDASIL_9_PRESENTATION,
        state=GARDASIL_9_STATE,
        product_rule_version=GARDASIL_9_SOURCE_VERSION,
        origin="Approved Simulator Origin",
        destination="Approved Simulator Destination",
        start_time=start_at - timedelta(minutes=1),
        status=TripStatus.ACTIVE,
    )
    assignment = DeviceAssignment(
        assignment_id=assignment_id,
        device_id=device_id,
        trip_id=trip_id,
        lot_trip_id=lot_trip_id,
        assigned_at=start_at - timedelta(minutes=1),
        active=True,
    )
    repository.register_trip_and_assignment(trip, assignment)
    service = OperationalTelemetryService(
        TelemetryProcessor(repository, repository),
        InMemoryAlertRepository(),
    )
    steps = run_scenario(
        service,
        scenario,
        device_id=device_id,
        start_time=start_at,
    )
    if not steps:
        raise SimulatedTrainingCorpusError("A corpus trip produced no telemetry")
    completed_at = steps[-1].result.telemetry_record.timestamp + timedelta(minutes=1)
    repository.complete_trip(
        trip_id,
        assignment_id,
        completed_at=completed_at,
    )
    record = CompletedTripDatasetService(repository, repository).get_record(
        lot_trip_id
    )
    status_counts = Counter(
        decision.status.value for decision in record.decision_records
    )
    summary = SimulatedTripSummary(
        lot_trip_id=lot_trip_id,
        scenario_family=family,
        scenario_seed=scenario_seed,
        nominal_cadence_minutes=nominal_cadence,
        telemetry_count=len(record.telemetry_records),
        first_sample_at=record.telemetry_records[0].timestamp,
        last_sample_at=record.telemetry_records[-1].timestamp,
        status_counts=tuple(sorted(status_counts.items())),
    )
    return record, summary


def _build_scenario(trip_index, family, rng, nominal_cadence):
    if family == STABLE_SAFE:
        duration = rng.randint(24, 40) * nominal_cadence
        temperature = lambda elapsed: 5.5 + rng.uniform(-1.0, 1.0)
    elif family == RECOVERABLE_EXCURSION:
        duration = rng.randint(40, 56) * nominal_cadence
        excursion_start = duration * rng.uniform(0.20, 0.30)
        excursion_end = duration * rng.uniform(0.60, 0.72)

        def temperature(elapsed):
            if excursion_start <= elapsed <= excursion_end:
                return 10.5 + rng.uniform(-1.0, 1.0)
            return 5.5 + rng.uniform(-0.8, 0.8)

    elif family == GRADUAL_WARMING_ADVERSE:
        duration = rng.randint(2850, 3100)
        warming_start = rng.randint(120, 300)
        warming_end = warming_start + rng.randint(120, 240)

        def temperature(elapsed):
            if elapsed < warming_start:
                return 5.8 + rng.uniform(-0.7, 0.7)
            if elapsed < warming_end:
                progress = (elapsed - warming_start) / (
                    warming_end - warming_start
                )
                return 6.5 + 5.0 * progress + rng.uniform(-0.2, 0.2)
            return 11.5 + rng.uniform(-1.5, 1.5)

    elif family in {SUSTAINED_HIGH_EXCURSION, SUSTAINED_LOW_EXCURSION}:
        duration = rng.randint(4380, 4500)
        excursion_start = rng.randint(60, 150)

        def temperature(elapsed):
            if elapsed < excursion_start:
                return 5.5 + rng.uniform(-0.7, 0.7)
            if family == SUSTAINED_HIGH_EXCURSION:
                return 11.5 + rng.uniform(-1.5, 1.5)
            return 1.0 + rng.uniform(-0.5, 0.5)

    else:
        raise SimulatedTrainingCorpusError(
            f"Unsupported simulator scenario family: {family}"
        )

    points = []
    elapsed = 0
    while elapsed <= duration:
        battery = None
        if rng.random() < 0.72:
            battery = max(5.0, 98.0 - elapsed / max(duration, 1) * 55.0)
        health_draw = rng.random()
        device_health = (
            None
            if health_draw < 0.15
            else ("DEGRADED" if health_draw < 0.30 else "OK")
        )
        observed_temperature = round(temperature(elapsed), 3)
        points.append(
            ScenarioPoint(
                elapsed_minutes=float(elapsed),
                temperature=observed_temperature,
                battery_level=(None if battery is None else round(battery, 2)),
                device_health=device_health,
            )
        )
        elapsed += max(1, nominal_cadence + rng.choice((-1, 0, 1)))
    return SimulationScenario(
        scenario_id=f"corpus-{trip_index:04d}-{family}",
        name=f"CORPUS {family.upper()}",
        points=tuple(points),
    )


def _validate_config(config):
    if not isinstance(config, SimulatedCorpusConfig):
        raise SimulatedTrainingCorpusError("config must be SimulatedCorpusConfig")
    if (
        isinstance(config.trip_count, bool)
        or not isinstance(config.trip_count, int)
        or config.trip_count < len(SIMULATED_SCENARIO_FAMILIES)
    ):
        raise SimulatedTrainingCorpusError(
            "trip_count must include at least one trip per scenario family"
        )
    if isinstance(config.master_seed, bool) or not isinstance(config.master_seed, int):
        raise SimulatedTrainingCorpusError("master_seed must be an integer")
    if (
        not isinstance(config.start_at, datetime)
        or config.start_at.tzinfo is None
        or config.start_at.utcoffset() is None
    ):
        raise SimulatedTrainingCorpusError("start_at must be timezone-aware")
    if not isinstance(config.source_id, str) or not config.source_id.strip():
        raise SimulatedTrainingCorpusError("source_id must be non-empty")
    return config


def _utc_text(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
