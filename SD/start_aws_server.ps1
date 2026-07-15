$env:AWS_REGION = "us-east-1"
$env:SHIPMENTS_TABLE = "VitaeShipments"

python "$PSScriptRoot/backend/app.py"
