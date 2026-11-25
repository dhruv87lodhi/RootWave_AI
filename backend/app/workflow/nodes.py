# NODE 1 receive_incident

def receive_incident (state:dict)-> dict:
    incident=state.get("incident")

    if not isinstance(incident , dict) or not incident:
        return{
            "status":"failed",
            "errors":["Incident data is missing or invalid !"]
        }


    incident_id=incident.get("incident_id")

    if not isinstance(incident_id,str) or not incident_id.strip():
        return{
            "status" : "failed",
            "errors":["Incident ID is missing"]
        }

    return{
        "incident_id":incident_id,
        "incident":incident,
        "evidences":[],
        "status":"received",
        "errors":[]
    }


# NODE 2 

from app.services.incident_validator import validate_incident_data
def validate_incident(state:dict)->dict:

    # get incident from the workflow
    incident=state.get("incident")

    # handle missing incident data
    if not isinstance(incident , dict) or not incident:
        return{
            "status":"failed",
            "errors": ["Incident data is missing or invalid"]
        }

    # validate incident with the validator function
    errors=validate_incident_data(incident)

    # when validation fails

    if errors:
        return{
            "status" : "failed",
            "errors" : errors
        }

    # if validation succeeds preserve the current state

    return{
        "incident_id" : incident["incident_id"],
        "incident": incident,
        "evidences" : state.get("evidences" , []),
        "status": "validated",
        "errors" :[]
    }


# NODE 3
from uuid import UUID
from app.db.database import SessionLocal
from app.services.evidence_prep_service import prepare_evidence_for_incident


def prepare_investigation(state: dict) -> dict:

    # check whether previous node has failed or not
    if state.get("status") == "failed":
        return {
            "status": "failed",
            "errors": state.get(
                "errors",
                ["Incident Validation Failed"],
            ),
        }

    # get incident
    incident = state.get("incident")

    # handle missing or invalid data
    if not isinstance(incident, dict) or not incident:
        return {
            "status": "failed",
            "errors": ["cannot prepare investigation: incident data is missing or invalid"],
        }

    # ensure the incident ID exists
    incident_id = state.get("incident_id")
    if not isinstance(incident_id, str) or not incident_id.strip():
        return {
            "status": "failed",
            "errors": ["Cannot prepare investigation: incident ID is missing"],
        }

    # ensure the incident has passed the validation
    if state.get("status") != "validated":
        return {
            "status": "failed",
            "errors": [
                "Cannot prepare investigation: incident has not been validated"
            ],
        }

    # Execute evidence preparation pipeline (clustering, anomalies, embeddings, chunks & inventory)
    inventory_summary = {}
    chunks_count = 0
    try:
        with SessionLocal() as db:
            prep_summary = prepare_evidence_for_incident(
                db=db,
                incident_id=UUID(incident_id),
                generate_embeddings=True,
            )
            db.commit()
            inventory_summary = prep_summary.get("inventory", {})
            chunks_count = prep_summary.get("total_chunks", 0)
    except Exception as exc:
        # If DB connection fails or table doesn't exist, we log and proceed gracefully
        import logging
        logging.getLogger(__name__).warning("Evidence prep encountered non-fatal error: %s", exc)

    # prepare the incident for the next node
    return {
        "incident_id": incident_id,
        "incident": incident,
        "evidences": state.get("evidences", []),
        "inventory": inventory_summary,
        "chunks_count": chunks_count,
        "status": "prepared",
        "errors": [],
    }


# NODE 4
from datetime import datetime, timezone
def mark_ready(state: dict)->dict:

    # check the status of previous node
    if state.get("status")=="failed":
        return{
            "status":"failed",
            "errors":state.get("errors" , ["previous node failed"])
        }

    # ensure the investigation was prepared or not
    if state.get("status") != "prepared":
        return{
            "status":"failed",
            "errors":[
                "Cannot make ready : investigation has not been prepared "
            ]
        }

    # record completion time in utc
    completed_at = datetime.now(timezone.utc).isoformat()

    # update the workflow status
    return{
        "status":"READY",
        "completed_at":completed_at,
        "errors":[]
    }