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
	mkdir -p "$(ARTIFACTS_DIR)"
	cp $(WORKER_MODULES) "$(ARTIFACTS_DIR)"
	python -m pip install --requirement requirements-worker.txt --target "$(ARTIFACTS_DIR)"
