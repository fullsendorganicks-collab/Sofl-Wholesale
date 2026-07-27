"""
Uses Claude to score raw leads by how promising they are, given distress
signals and equity position. This is pure research/analysis -- no outbound
contact happens here, so there's no TCPA exposure at this stage.
"""

import os
import json
from anthropic import Anthropic
from app.db import get_conn

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SCORING_PROMPT = """You are scoring a real estate wholesale lead for how promising it is.

Property data:
{lead_json}

Score this lead from 0-100 on likelihood of being a genuinely motivated seller
willing to sell below market for a fast, as-is cash sale. Consider:
- Distress signals present (tax delinquent, absentee owner, pre-foreclosure, probate)
- Equity position (high equity = more room to negotiate a discount; low/negative
  equity means the seller may not be able to accept a below-market offer at all)
- Any red flags suggesting this isn't a real opportunity (e.g. recently sold,
  already listed with an agent, owner-occupied with no distress signal)

Respond ONLY with valid JSON in this exact format, nothing else:
{{"score": <0-100 integer>, "reasoning": "<2-3 sentence explanation>"}}
"""


def score_lead(lead: dict) -> dict:
    lead_json = json.dumps({
        "address": lead.get("address"),
        "distress_signals": lead.get("distress_signals"),
        "estimated_value": lead.get("estimated_value"),
        "equity_estimate": lead.get("equity_estimate"),
    })

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": SCORING_PROMPT.format(lead_json=lead_json)}],
    )

    raw_text = response.content[0].text.strip()
    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        # Fallback if Claude wraps the JSON in explanation text despite instructions
        result = {"score": 0, "reasoning": f"Could not parse model response: {raw_text[:200]}"}

    return result


def score_all_new_leads() -> int:
    """Scores every lead with status='new' and writes to lead_scores table."""
    scored_count = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM leads WHERE status = 'new'")
            leads = cur.fetchall()

        for lead in leads:
            result = score_lead(lead)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO lead_scores (lead_id, score, reasoning)
                    VALUES (%s, %s, %s)
                """, (lead["id"], result["score"], result["reasoning"]))
                cur.execute("UPDATE leads SET status = 'scored', updated_at = now() WHERE id = %s",
                            (lead["id"],))
            scored_count += 1

    return scored_count
