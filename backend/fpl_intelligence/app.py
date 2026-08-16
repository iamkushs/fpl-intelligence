"""FastAPI application entry point."""

import logging

from fastapi import FastAPI

from fpl_intelligence.api.fpl import router as fpl_router
from fpl_intelligence.api.research import router as research_router
from fpl_intelligence.api.watchlist import router as watchlist_router
from fpl_intelligence.api.triggers import router as triggers_router
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.config import Settings, get_settings
from fpl_intelligence.db.session import Database
from fpl_intelligence.integrations.fpl.adapter import OfficialFPLAdapter
from fpl_intelligence.integrations.fpl.snapshot import FPLSnapshotService
from fpl_intelligence.research.two_stage import (
    CodexResearchExtractor,
    CodexSearchProvider,
    HTTPPageRetriever,
    TwoStageResearchService,
)
from fpl_intelligence.research.source_discovery import (
    CodexEval2AtomicEvidenceProvider,
    CodexEval2DiscoveryProvider,
    CodexEval2PageResearchProvider,
    Eval2SourceDiscoveryService,
)
from fpl_intelligence.research.quality import ResearchQualityService
from fpl_intelligence.watchlist.discovery import CodexDiscoveryAnalyzer, DiscoveryService

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    codex_service: CodexService | None = None,
    fpl_adapter: OfficialFPLAdapter | None = None,
    fpl_snapshot_service: FPLSnapshotService | None = None,
    two_stage_research_service: TwoStageResearchService | None = None,
    eval2_source_discovery_service: Eval2SourceDiscoveryService | None = None,
    research_quality_service: ResearchQualityService | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="FPL Intelligence System", version="0.1.0")
    app.state.logger = logger
    app.state.settings = settings
    app.state.database = database
    if app.state.database is None and settings.database_url:
        app.state.database = Database(settings)
    app.state.codex_service = codex_service or CodexService(settings=settings)
    app.state.fpl_adapter = fpl_adapter or OfficialFPLAdapter(
        base_url=settings.official_fpl_base_url,
        timeout_seconds=settings.official_fpl_timeout_seconds,
    )
    app.state.fpl_snapshot_service = fpl_snapshot_service or FPLSnapshotService(
        app.state.fpl_adapter,
        season_id=settings.official_fpl_season_id,
    )
    app.state.two_stage_research_service = two_stage_research_service or TwoStageResearchService(
        search_provider=CodexSearchProvider(app.state.codex_service),
        retriever=HTTPPageRetriever(),
        extractor=CodexResearchExtractor(app.state.codex_service),
    )
    app.state.eval2_source_discovery_service = eval2_source_discovery_service or Eval2SourceDiscoveryService(
        discovery_provider=CodexEval2DiscoveryProvider(app.state.codex_service),
        retriever=HTTPPageRetriever(),
        page_research_provider=CodexEval2PageResearchProvider(app.state.codex_service),
        atomic_provider=CodexEval2AtomicEvidenceProvider(app.state.codex_service),
    )
    app.state.research_quality_service = research_quality_service or ResearchQualityService()
    app.state.discovery_service = DiscoveryService(CodexDiscoveryAnalyzer(app.state.codex_service))
    app.include_router(research_router)
    app.include_router(fpl_router)
    app.include_router(watchlist_router)
    app.include_router(triggers_router)

    @app.get("/health", tags=["system"])
    def health():
        return {"status": "ok"}

    return app


app = create_app()
