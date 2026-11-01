"""Administrator endpoints for official advisories and market prices.

These are the only places where a human-entered value enters the app. Nothing is
scraped or invented: every record requires a source and an observation date.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Response

from ..config import settings
from ..db import get_conn
from ..schemas import AdvisoryCreate, MarketPriceCreate

router = APIRouter()


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not settings.admin_token:
        # Local-first deployment without a token: read/write is allowed but this
        # is recorded in the audit metadata returned by /admin/info.
        return
    if not x_admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="A valid X-Admin-Token header is required.")


@router.get("/admin/info")
def admin_info() -> dict[str, Any]:
    return {
        "admin_token_required": bool(settings.admin_token),
        "message": (
            "Set GFI_ADMIN_TOKEN to require the X-Admin-Token header. Without a token this deployment accepts "
            "administrator writes from anyone who can reach the API."
        ),
        "crops": "Use /api/catalog/crops for the crop ids accepted by market prices.",
    }


@router.get("/admin/advisories")
def list_advisories(region: str | None = None) -> dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM advisories ORDER BY published_at DESC, created_at DESC").fetchall()
    output = []
    for row in rows:
        payload = dict(row)
        payload["regions"] = json.loads(row["regions"]) if row["regions"] else None
        payload["crop_ids"] = json.loads(row["crop_ids"]) if row["crop_ids"] else None
        if region and payload["regions"] and region not in payload["regions"]:
            continue
        output.append(payload)
    return {"advisories": output}


@router.post("/admin/advisories", status_code=201)
def create_advisory(body: AdvisoryCreate, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    advisory_id = uuid.uuid4().hex
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO advisories (id,title,body,agency,source_url,published_at,valid_from,valid_to,
            regions,crop_ids,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                advisory_id, body.title, body.body, body.agency, body.source_url, body.published_at,
                body.valid_from, body.valid_to,
                json.dumps(body.regions) if body.regions else None,
                json.dumps(body.crop_ids) if body.crop_ids else None,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    return {"id": advisory_id}


@router.delete("/admin/advisories/{advisory_id}", status_code=204)
def delete_advisory(advisory_id: str, x_admin_token: str | None = Header(default=None)) -> Response:
    require_admin(x_admin_token)
    with get_conn() as conn:
        conn.execute("DELETE FROM advisories WHERE id=?", (advisory_id,))
    return Response(status_code=204)


@router.get("/market-prices")
def list_prices(crop_id: str | None = Query(default=None)) -> dict[str, Any]:
    with get_conn() as conn:
        if crop_id:
            rows = conn.execute(
                "SELECT * FROM market_prices WHERE crop_id=? ORDER BY observed_on DESC LIMIT 200", (crop_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM market_prices ORDER BY observed_on DESC LIMIT 200").fetchall()
    prices = [dict(row) for row in rows]
    return {
        "prices": prices,
        "source_kind": "administrator_entered",
        "note": (
            "Prices are entered by an administrator with an observation date and a source note. No live price "
            "feed is connected, and prices are not used to fabricate economic suitability scores."
        ),
    }


@router.post("/admin/market-prices", status_code=201)
def create_price(body: MarketPriceCreate, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    price_id = uuid.uuid4().hex
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO market_prices (id,district,market,crop_id,price_ghs,unit,observed_on,source_note,
            created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                price_id, body.district, body.market, body.crop_id, body.price_ghs, body.unit,
                body.observed_on, body.source_note,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    return {"id": price_id}


@router.get("/admin/stats")
def stats() -> dict[str, Any]:
    with get_conn() as conn:
        farms = conn.execute("SELECT COUNT(*) FROM farms").fetchone()[0]
        fields = conn.execute("SELECT COUNT(*) FROM farm_fields").fetchone()[0]
        jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        cache = conn.execute("SELECT COUNT(*) FROM provider_cache").fetchone()[0]
        analysis = conn.execute("SELECT COUNT(*) FROM analysis_cache").fetchone()[0]
        advisories = conn.execute("SELECT COUNT(*) FROM advisories").fetchone()[0]
        prices = conn.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0]
    return {
        "farms": farms,
        "fields": fields,
        "jobs": jobs,
        "provider_cache_entries": cache,
        "analysis_cache_entries": analysis,
        "advisories": advisories,
        "market_prices": prices,
    }
