"""Thin production adapters over the existing Codex structured-text gateway."""
from fpl_intelligence.research.two_stage import _json_object
from fpl_intelligence.research.eval2_prompts import evidence_bundle_assessment_prompt, blind_spot_prompt, final_player_synthesis_prompt
from fpl_intelligence.research.quality_execution import RedditQualityOutput, CounterSearchQualityOutput, FreshnessQualityOutput

class CodexBundleAssessmentProvider:
    def __init__(self,codex): self.codex=codex
    def assess(self,*,context,prompt_version): return _json_object(self.codex.execute(prompt=evidence_bundle_assessment_prompt(context=context).prompt).final_text)
class CodexBlindSpotProvider:
    def __init__(self,codex): self.codex=codex
    def find(self,*,context,prompt_version): return _json_object(self.codex.execute(prompt=blind_spot_prompt(context=context).prompt).final_text)
class CodexFinalSynthesisProvider:
    def __init__(self,codex): self.codex=codex
    def synthesize(self,*,context,prompt_version): return _json_object(self.codex.execute(prompt=final_player_synthesis_prompt(context=context).prompt).final_text)

class CodexQualityProvider:
    """Uses the existing gateway; malformed/unavailable output fails safely in quality execution."""
    def __init__(self,codex,kind): self.codex,self.kind=codex,kind
    def research(self,*,prompt,**kwargs):
        data=_json_object(self.codex.execute(prompt=prompt).final_text)
        # Candidate conversion stays at the NR12 boundary; no direct evidence is created here.
        from fpl_intelligence.research.source_discovery import SourceCandidatePayload
        candidates=[SourceCandidatePayload(**item) for item in data.get("candidates",[])]
        if self.kind=="reddit": return RedditQualityOutput(candidates=candidates)
        if self.kind=="counter": return CounterSearchQualityOutput(candidates=candidates,outcome=data.get("outcome","unresolved"))
        return FreshnessQualityOutput(candidates=candidates,outcome=data.get("outcome","unresolved"),superseding_candidate_index=data.get("superseding_candidate_index"),monitoring_condition=data.get("monitoring_condition"))
