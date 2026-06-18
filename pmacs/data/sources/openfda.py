"""OpenFDA drug/device data source (IMPORTANT)."""
from __future__ import annotations
from datetime import datetime, timezone
from pmacs.data.gateway import DataGateway
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


def fetch_drug_events(drug_name: str, gateway: DataGateway, cycle_id: str = "") -> EvidencePacket:
    """Fetch adverse event data from openFDA."""
    url = f"https://api.fda.gov/drug/event.json"
    try:
        response = gateway.fetch("openfda", url, params={"search": f"patient.drug.medicinalproduct:{drug_name}", "limit": 3})
        data = response.json()
    except Exception:
        data = {"results": []}
    evidence = [Evidence(
        id=f"openfda_{drug_name}_events",
        source=DataSource.OPENFDA,
        type=EvidenceType.REGULATORY,
        ticker="",  # Will be mapped by caller
        fetched_at=datetime.now(timezone.utc),
        content_hash=str(hash(str(data))),
        data=data,
    )]
    return EvidencePacket(ticker="", cycle_id=cycle_id, evidence=evidence, fetched_at=datetime.now(timezone.utc), source_count=1)
