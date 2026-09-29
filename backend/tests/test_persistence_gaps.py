"""Regression tests for Phase 4B/UI integration gaps.

These guard the persistence layer: every event type an adapter can emit must be
storable, otherwise events are silently dropped and the UI evidence chain can
never show them.
"""
import pytest
from datetime import datetime

from backend.models import EventType as DBEventType, SourceService as DBSourceService
from backend.adapters.base import EventType, SourceService, MediaType, EventStatus, RawEvent


class TestAdapterEventTypesArePersistable:
    """Every adapter-level EventType must have a database counterpart."""

    @pytest.mark.parametrize(
        "event_type",
        list(EventType),
        ids=[e.name for e in EventType],
    )
    def test_every_adapter_event_type_persists(self, event_type):
        assert event_type.value in {e.value for e in DBEventType}, (
            f"{event_type.name} is emitted by an adapter but cannot be persisted: "
            f"backend.models.EventType has no '{event_type.value}' member. "
            f"Events of this type are silently dropped during ingestion."
        )

    @pytest.mark.parametrize(
        "source",
        list(SourceService),
        ids=[s.name for s in SourceService],
    )
    def test_every_adapter_source_persists(self, source):
        assert source.value in {s.value for s in DBSourceService}, (
            f"{source.name} is a configured adapter but backend.models.SourceService "
            f"has no '{source.value}' member, so its events cannot be persisted."
        )


class TestProwlarrAndGuardarrPersistence:
    """Regression: Prowlarr/Guardarr events were dropped at ingest."""

    @pytest.mark.parametrize(
        "source,event_type,correlation_key",
        [
            (SourceService.PROWLARR, EventType.INDEXER_SEARCH, "prowlarr:persist-test"),
            (SourceService.GUARDARR, EventType.STORAGE_ADMIT, "guardarr:persist-test"),
        ],
        ids=["prowlarr-indexer-search", "guardarr-storage-admit"],
    )
    def test_event_is_not_silently_dropped(self, db_session, source, event_type, correlation_key):
        from backend.services.events import EventService
        from backend.models import RawEventModel

        event = RawEvent(
            timestamp=datetime.utcnow(),
            source_service=source,
            event_type=event_type,
            media_type=MediaType.MOVIE,
            media_identifier="persist-probe",
            title=f"{source.value} persistence probe",
            correlation_key=correlation_key,
            status=EventStatus.COMPLETED,
        )

        EventService(db_session).ingest_events([event])
        db_session.commit()

        stored = (
            db_session.query(RawEventModel)
            .filter(RawEventModel.correlation_key == correlation_key)
            .first()
        )
        assert stored is not None, (
            f"{event_type.value} from {source.value} was silently dropped during ingestion"
        )
        assert stored.source_service.value == source.value
        assert stored.event_type.value == event_type.value
