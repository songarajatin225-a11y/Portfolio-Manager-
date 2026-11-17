"""AI research reports and market summaries.

The data bundle is gathered deterministically by the platform (the same tool functions the
assistant uses), and Claude writes the report from that bundle only, returning JSON that matches a
fixed schema. A deterministic data-quality assessment (what is missing or stale) is stored with it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import tools as ai_tools
from app.ai.assistant import ai_config, context_line
from app.ai.client import create_message, text_of, usage_dict
from app.core.errors import AppError
from app.db.models import AIReport
from app.services import instruments as instrument_service

DISCLAIMER = (
    "AI-generated research from the platform's stored data. It may contain errors, is not investment "
    "advice and does not guarantee any outcome."
)

_text = {"type": "string"}
_list = {"type": "array", "items": {"type": "string"}}

REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_overview": _text,
        "financial_performance": _text,
        "technical_indicators": _text,
        "trend_analysis": _text,
        "news_sentiment": _text,
        "industry_outlook": _text,
        "bullish_factors": _list,
        "bearish_factors": _list,
        "risk_factors": _list,
        "valuation_analysis": _text,
        "investment_thesis": _text,
        "confidence": {
            "type": "object",
            "properties": {"level": {"type": "string", "enum": ["low", "medium", "high"]}, "rationale": _text},
            "required": ["level", "rationale"],
            "additionalProperties": False,
        },
        "data_quality": {
            "type": "object",
            "properties": {"assessment": _text, "missing_data": _list},
            "required": ["assessment", "missing_data"],
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}
REPORT_SCHEMA["required"] = list(REPORT_SCHEMA["properties"])

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": _text,
        "summary": _text,
        "key_points": _list,
        "sector_view": _text,
        "risks": _list,
        "data_caveats": _list,
    },
    "required": ["headline", "summary", "key_points", "sector_view", "risks", "data_caveats"],
    "additionalProperties": False,
}

REPORT_SYSTEM = """You write equity research notes for Indian listed companies from a JSON data bundle.
Use only facts present in the bundle; never invent figures, filings, news, guidance or analyst views. When a \
section has no supporting data, say so explicitly in that section and list it under data_quality.missing_data. \
Cite sources and dates inline where useful (e.g. "FY ending 2026-03-31, Q2 filing"). Keep each prose section \
to a few sentences. The investment_thesis must be balanced and conditional - describe what would need to be true, \
not a recommendation to buy or sell. If the bundle says data is SIMULATED, state that it is simulated."""


def _quality(bundle: dict[str, dict[str, Any]]) -> dict[str, Any]:
    checks = {}
    for key, res in bundle.items():
        data = res.get("data", {}) if isinstance(res, dict) else {}
        note = data.get("note") if isinstance(data, dict) else None
        empty = bool(note) or (key == "news" and not data.get("articles"))
        checks[key] = "missing" if empty or res.get("error") else "available"
    available = sum(1 for v in checks.values() if v == "available")
    return {
        "datasets": checks,
        "completeness": round(available / max(len(checks), 1), 2),
        "simulated": any("SIMULATED" in json.dumps(r.get("sources", [])) for r in bundle.values()),
    }


async def _gather(db: AsyncSession, user_id: uuid.UUID, calls: dict[str, tuple[str, dict[str, Any]]]):
    bundle, sources = {}, []
    for key, (tool_name, args) in calls.items():
        out, is_error, srcs = await ai_tools.execute(db, user_id, tool_name, args)
        bundle[key] = json.loads(out) if not is_error else {"error": json.loads(out).get("error")}
        sources.extend(srcs)
    return bundle, sources


async def _structured(
    db: AsyncSession, cfg: dict[str, Any], system: str, prompt: str, schema: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], str]:
    resp = await create_message(
        model=cfg["model"],
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": cfg["report_effort"], "format": {"type": "json_schema", "schema": schema}},
    )
    if resp.stop_reason == "refusal":
        raise AppError("Claude declined to produce this report.", code="ai_refused")
    if resp.stop_reason == "max_tokens":
        raise AppError("The report exceeded the length limit; try again.", code="ai_truncated")
    try:
        content = json.loads(text_of(resp.content))
    except json.JSONDecodeError as exc:
        raise AppError("Claude returned an unreadable report; please retry.", code="ai_bad_output") from exc
    missing = [k for k in schema["required"] if k not in content]
    if missing:
        raise AppError(f"Report is missing sections: {', '.join(missing)}", code="ai_bad_output")
    return content, usage_dict(resp), getattr(resp, "model", cfg["model"])


async def research_report(db: AsyncSession, user_id: uuid.UUID, symbol: str) -> AIReport:
    cfg = await ai_config(db)
    inst = await instrument_service.resolve_symbol(db, symbol)
    sector = inst.sector or ""
    bundle, sources = await _gather(
        db,
        user_id,
        {
            "quote": ("get_quote", {"symbol": inst.symbol}),
            "technicals": ("get_technical_analysis", {"symbol": inst.symbol}),
            "fundamentals": ("get_fundamentals", {"symbol": inst.symbol}),
            "news": ("get_news", {"symbol": inst.symbol, "days": 30, "limit": 15}),
            "ml": ("get_ml_prediction", {"symbol": inst.symbol}),
            "sector_peers": (
                "screen_stocks",
                {
                    "filters": [{"field": "sector", "op": "eq", "number": 0, "number2": 0, "text": sector}]
                    if sector
                    else [],
                    "sort": "ret_3m",
                    "direction": "desc",
                    "limit": 8,
                },
            ),
            "market": ("get_market_overview", {}),
        },
    )
    quality = _quality(bundle)
    prompt = (
        f"{await context_line(db)}\n\nWrite a research report on {inst.symbol} ({inst.name}, sector: "
        f"{sector or 'unclassified'}). Deterministic data-quality check: {json.dumps(quality)}\n\n"
        f"DATA BUNDLE:\n{json.dumps(bundle, default=str)}"
    )
    content, usage, model = await _structured(db, cfg, REPORT_SYSTEM, prompt, REPORT_SCHEMA)
    report = AIReport(
        user_id=user_id,
        instrument_id=inst.id,
        kind="research_report",
        model=model,
        content={**content, "disclaimer": DISCLAIMER, "symbol": inst.symbol, "name": inst.name},
        sources=sources,
        data_quality=quality,
        usage=usage,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


async def market_summary(db: AsyncSession, user_id: uuid.UUID) -> AIReport:
    cfg = await ai_config(db)
    bundle, sources = await _gather(
        db,
        user_id,
        {
            "market": ("get_market_overview", {}),
            "sectors": ("get_sector_strength", {}),
            "news": ("get_news", {"symbol": "", "days": 1, "limit": 25}),
            "unusual": ("get_unusual_activity", {"limit": 8}),
        },
    )
    quality = _quality(bundle)
    prompt = (
        f"{await context_line(db)}\n\nSummarise what is happening in the Indian stock market today for an "
        f"investor, from this data only. Data-quality check: {json.dumps(quality)}\n\n"
        f"DATA BUNDLE:\n{json.dumps(bundle, default=str)}"
    )
    system = REPORT_SYSTEM.replace(
        "equity research notes for Indian listed companies",
        "concise daily market summaries for the Indian equity market",
    )
    content, usage, model = await _structured(db, cfg, system, prompt, SUMMARY_SCHEMA)
    report = AIReport(
        user_id=user_id,
        kind="market_summary",
        model=model,
        content={**content, "disclaimer": DISCLAIMER, "generated_at": datetime.now(UTC).isoformat()},
        sources=sources,
        data_quality=quality,
        usage=usage,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report
