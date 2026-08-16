"""Versioned Eval 2 prompt contracts for NR11 research execution."""

from __future__ import annotations

import json
from dataclasses import dataclass


EVAL2_SOURCE_DISCOVERY_PROMPT_VERSION = "eval2_source_discovery_v1"
EVAL2_PAGE_RESEARCH_PROMPT_VERSION = "eval2_page_research_v1"
EVAL2_ATOMIC_EXTRACTION_PROMPT_VERSION = "eval2_atomic_evidence_extraction_v1"

EVAL2_DISCOVERY_DIMENSIONS = (
    "availability",
    "injury / fitness",
    "suspension",
    "training status",
    "minutes expectation",
    "recent starts",
    "starting likelihood / expected XI",
    "tactical role",
    "positional usage",
    "formation / team shape",
    "attacking involvement",
    "team attack context",
    "set pieces",
    "penalties",
    "corners",
    "direct free kicks",
    "indirect free kicks",
    "competition for place",
    "goalkeeper hierarchy where relevant",
    "manager comments / manager intent",
    "recent performance context",
    "underlying data",
    "team tactical changes",
    "transfer / contract context where FPL-relevant",
    "fixture context where materially relevant",
    "credible information contrary to the obvious/current interpretation",
)

EVAL2_SOURCE_HIERARCHY = (
    "official / primary sources",
    "specialist / direct football reporting",
    "reputable football media",
    "supporter / Reddit material",
    "credible general web sources",
)


@dataclass(frozen=True)
class PromptEnvelope:
    version: str
    prompt: str
    structured_output_contract: dict


def discovery_prompt(
    *,
    phase: str,
    player_payload: dict,
    research_cutoff: str,
    situation_payload: dict | None = None,
    trigger_payload: dict | None = None,
    gameweek_id: int | None = None,
    target_gameweek_id: int | None = None,
    known_missing_dimensions: list[str] | None = None,
    durable_context: dict | None = None,
    existing_candidates: list[dict] | None = None,
) -> PromptEnvelope:
    contract = {
        "candidates": [
            {
                "url": "absolute http(s) URL",
                "source": "source or publisher when known, nullable",
                "publisher": "publisher when known, nullable",
                "title": "title when known, nullable",
                "target_dimensions": ["one or more Eval 2 dimensions"],
                "usefulness": "why this source is useful for FPL decision research",
                "source_category": "official_primary|specialist_direct|reputable_media|supporter_reddit|credible_general",
                "expected_relevance": "high|medium|low",
                "published_at": "ISO datetime when known, nullable",
                "recency": "human recency label when known, nullable",
                "lineage_type": "original|independent|derivative|unclear",
                "lineage_notes": "lineage hint without invented clustering, nullable",
                "query": "query or discovery route that found it, nullable",
            }
        ],
        "known_gaps": ["dimension still missing or stale after this phase"],
    }
    phase_instruction = (
        "Phase A broad discovery: cover the major FPL-relevant dimensions."
        if phase == "broad"
        else "Phase B targeted gap discovery: fill missing, contradictory, stale-looking, or decision-relevant gaps from Phase A without doing a formal adversarial counter-search."
    )
    prompt = "\n".join(
        [
            f"Prompt version: {EVAL2_SOURCE_DISCOVERY_PROMPT_VERSION}",
            phase_instruction,
            "Research the Player as the primary subject. ResearchSituation and Trigger are optional context only.",
            "Prioritize FPL decision relevance over biography, personality, generic career history, unrelated transfer gossip, or low-value narrative.",
            "Do not use source popularity as reliability. Supporter and Reddit material can be qualitative but is not equivalent to primary factual sourcing.",
            f"Research cutoff: {research_cutoff}. Do not use post-cutoff evidence.",
            f"Player: {json.dumps(player_payload, sort_keys=True)}",
            f"Situation: {json.dumps(situation_payload, sort_keys=True) if situation_payload else 'none'}",
            f"Trigger: {json.dumps(trigger_payload, sort_keys=True) if trigger_payload else 'none'}",
            f"Current gameweek: {gameweek_id if gameweek_id is not None else 'unknown'}",
            f"Target gameweek: {target_gameweek_id if target_gameweek_id is not None else 'unknown'}",
            f"Eval 2 dimensions: {json.dumps(EVAL2_DISCOVERY_DIMENSIONS)}",
            f"Source hierarchy: {json.dumps(EVAL2_SOURCE_HIERARCHY)}",
            f"Known missing dimensions: {json.dumps(known_missing_dimensions or [])}",
            f"Existing durable context: {json.dumps(durable_context or {}, sort_keys=True)}",
            f"Existing candidates: {json.dumps(existing_candidates or [], sort_keys=True, default=str)}",
            "Return JSON only matching this contract:",
            json.dumps(contract, sort_keys=True),
        ]
    )
    return PromptEnvelope(EVAL2_SOURCE_DISCOVERY_PROMPT_VERSION, prompt, contract)


def page_research_prompt(
    *,
    player_payload: dict,
    link_payload: dict,
    page_content: str,
    research_cutoff: str,
    situation_payload: dict | None = None,
    trigger_payload: dict | None = None,
    target_dimensions: list[str] | None = None,
    durable_context: dict | None = None,
) -> PromptEnvelope:
    contract = {
        "summary": "short FPL-relevant page summary",
        "findings": "supported FPL facts and observations only",
        "evidence": "specific cited claims from the page",
        "uncertainties": "limitations or inaccessible/insufficient content, nullable",
        "referenced_players": ["canonical player names mentioned"],
    }
    prompt = "\n".join(
        [
            f"Prompt version: {EVAL2_PAGE_RESEARCH_PROMPT_VERSION}",
            "Research this selected source for FPL-relevant facts and observations. Do not produce a generic webpage summary.",
            "Do not infer unsupported facts from headlines or snippets. If content is insufficient, say so.",
            f"Research cutoff: {research_cutoff}. Ignore post-cutoff material.",
            f"Player: {json.dumps(player_payload, sort_keys=True)}",
            f"Situation: {json.dumps(situation_payload, sort_keys=True) if situation_payload else 'none'}",
            f"Trigger: {json.dumps(trigger_payload, sort_keys=True) if trigger_payload else 'none'}",
            f"Target dimensions: {json.dumps(target_dimensions or [])}",
            f"Source metadata: {json.dumps(link_payload, sort_keys=True)}",
            f"Existing durable context: {json.dumps(durable_context or {}, sort_keys=True)}",
            "Return JSON only matching this contract:",
            json.dumps(contract, sort_keys=True),
            "Page content:",
            page_content,
        ]
    )
    return PromptEnvelope(EVAL2_PAGE_RESEARCH_PROMPT_VERSION, prompt, contract)


def atomic_extraction_prompt(
    *,
    player_payload: dict,
    result_payload: dict,
    research_cutoff: str,
    situation_payload: dict | None = None,
    trigger_payload: dict | None = None,
    durable_context: dict | None = None,
) -> PromptEnvelope:
    contract = {
        "evidence": [
            {
                "claim": "one materially coherent claim",
                "claim_type": "NR10 claim type",
                "evidence_type": "fact|statistic|report|supporter_observation|speculation|inference",
                "player_ids": ["canonical integer player IDs explicitly supported"],
                "published_at": "ISO datetime or null",
                "observed_at": "ISO datetime or null; do not invent",
                "retrieved_at": "ISO datetime or null",
                "season": "explicit season or null",
                "reliability": "high|medium|low",
                "relevance": "high|medium|low",
                "is_volatile": "boolean or null",
                "source_cluster_id": "existing cluster id only when known, nullable",
                "notes": "extraction context, nullable",
            }
        ],
        "relationships": [
            {
                "from_index": "new evidence index, nullable",
                "from_evidence_id": "existing evidence id, nullable",
                "to_index": "new evidence index, nullable",
                "to_evidence_id": "existing evidence id, nullable",
                "relation_type": "supports|contradicts|supersedes",
                "rationale": "explicit rationale",
            }
        ],
        "hypothesis_relationships": [
            {
                "evidence_index": "new evidence index",
                "hypothesis_id": "existing hypothesis id",
                "relationship_type": "supports|contradicts",
                "rationale": "explicit rationale",
            }
        ],
    }
    prompt = "\n".join(
        [
            f"Prompt version: {EVAL2_ATOMIC_EXTRACTION_PROMPT_VERSION}",
            "Extract atomic evidence for Eval 2. Each unit must contain one materially coherent claim.",
            "Good: Player A started at left wing against Team B. Good: Player A took the first penalty. Good: Manager said Player A is fully fit.",
            "Bad: Player A started on the left, took penalties, looked dangerous and is probably nailed.",
            "Distinguish evidence_type from claim_type. Do not expose model inference as sourced fact.",
            "Allowed evidence types: fact, statistic, report, supporter_observation, speculation, inference.",
            "Preserve contradictions and supersession history; do not collapse disagreement into one best claim.",
            "Create evidence relationships only when explicit in the extracted material or durable context; do not automatically label newer claims as superseding older claims.",
            "Do not infer hypothesis support or contradiction merely from topical similarity. If the relationship is unclear, leave it unresolved.",
            f"Research cutoff: {research_cutoff}. Reject post-cutoff evidence.",
            f"Player: {json.dumps(player_payload, sort_keys=True)}",
            f"Situation: {json.dumps(situation_payload, sort_keys=True) if situation_payload else 'none'}",
            f"Trigger: {json.dumps(trigger_payload, sort_keys=True) if trigger_payload else 'none'}",
            f"ResearchResult: {json.dumps(result_payload, sort_keys=True)}",
            f"Existing durable context: {json.dumps(durable_context or {}, sort_keys=True)}",
            "Return JSON only matching this contract:",
            json.dumps(contract, sort_keys=True),
        ]
    )
    return PromptEnvelope(EVAL2_ATOMIC_EXTRACTION_PROMPT_VERSION, prompt, contract)
