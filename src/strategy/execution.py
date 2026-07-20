"""Dispatches an approved proposal to the right platform's order-placement
call. This is the only place that actually sends a trade - everything
upstream (signals, order_spec, proposals) only produces data.
"""
import json

from src.ingest import deriv, metatrader
from src.store.db import get_proposal, update_proposal_status


def execute_proposal(conn, proposal_id: int, require_demo: bool = True) -> dict:
    proposal = get_proposal(conn, proposal_id)
    if proposal is None:
        raise ValueError(f"No proposal with id {proposal_id}")
    if proposal["status"] != "pending":
        raise ValueError(f"Proposal {proposal_id} is not pending (status={proposal['status']})")

    spec = json.loads(proposal["order_spec_json"])
    platform = spec["platform"]

    if platform == "mt4_mt5":
        result = metatrader.place_trade(
            spec["symbol"], spec["direction"], spec["volume"],
            stop=spec["stop"], target=spec["target"], require_demo=require_demo,
        )
        order_id = str(result.get("orderId") or result.get("positionId") or result)

    elif platform == "deriv_multipliers":
        result = deriv.place_multiplier_trade(
            spec["symbol"], spec["direction"], spec["stake"], spec["multiplier"],
            spec["stop_loss_amount"], spec["take_profit_amount"], require_demo=require_demo,
        )
        order_id = str(result.get("contract_id"))

    elif platform == "deriv_options":
        result = deriv.place_option_trade(
            spec["symbol"], spec["contract_type"], spec["stake"],
            spec["duration"], spec["duration_unit"], require_demo=require_demo,
        )
        order_id = str(result.get("contract_id"))

    else:
        raise ValueError(f"Unknown platform: {platform}")

    update_proposal_status(conn, proposal_id, "executed", executed_order_id=order_id)
    return result


def reject_proposal(conn, proposal_id: int, note: str | None = None) -> None:
    proposal = get_proposal(conn, proposal_id)
    if proposal is None:
        raise ValueError(f"No proposal with id {proposal_id}")
    if proposal["status"] != "pending":
        raise ValueError(f"Proposal {proposal_id} is not pending (status={proposal['status']})")
    update_proposal_status(conn, proposal_id, "rejected", note=note)
