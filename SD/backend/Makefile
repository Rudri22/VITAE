WORKER_MODULES = \
	alerting.py \
	decision_outbox.py \
	dynamo_alert_repository.py \
	dynamo_telemetry_repository.py \
	outbox_dispatcher.py \
	outbox_worker.py \
	product_rules.py \
	repository_serialization.py \
	risk_rules.py \
	shipment_access.py \
	state_repository.py \
	telemetry.py \
	telemetry_processor.py \
	trip_identity.py

build-OutboxWorkerFunction:
	python -c "import pathlib, shutil, sys; target = pathlib.Path(sys.argv[1]); target.mkdir(parents=True, exist_ok=True); [shutil.copy2(source, target / pathlib.Path(source).name) for source in sys.argv[2:]]" "$(ARTIFACTS_DIR)" $(WORKER_MODULES)
	python -m pip install --requirement requirements-worker.txt --target "$(ARTIFACTS_DIR)"
