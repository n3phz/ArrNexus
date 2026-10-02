"""Phase 4C regression tests: stage intelligence and attention classification.

Semantics under test
--------------------
Three distinct concepts, deliberately NOT interchangeable:

  stage age      how long the item has occupied its current pipeline stage
  stage freshness whether that age is within the expected duration for the state
  evidence age   how long since ANY observation was recorded for the item

A stage can be overdue while its evidence is still fresh (polling keeps
reporting the same state), and evidence can be ancient while the item is
trivially complete. Neither fact alone makes an item an incident: attention is
driven by the current state plus the expected duration of that stage.

Historical evidence is reported as CONTEXT. A wanted entry from last month is an
old wanted entry, not an operational incident.
"""
import pytest
from datetime import datetime, timedelta, timezone

from backend.adapters.base import (
    RawEvent, SourceService, MediaType, EventType, EventStatus, EvidenceBoundary,
)
from backend.correlation.engine import (
    CorrelationEngine, CorrelationResult, STAGE_DURATION_SECONDS,
    EVIDENCE_AGE_FRESH_SECONDS, EVIDENCE_AGE_AGED_SECONDS,
)
from backend.services.orchestrator import Orchestrator, AttentionLevel


def ev(etype, ts, svc=SourceService.RADARR, meta=None, boundary=EvidenceBoundary.OBSERVED,
       download_id=None, key="media:1", media_id="1"):
    """Build an observed event."""
    return RawEvent(
        timestamp=ts,
        source_service=svc,
        event_type=etype,
        media_type=MediaType.MOVIE,
        media_identifier=media_id,
        title="Test Movie",
        source_download_id=download_id,
        correlation_key=key,
        status=EventStatus.COMPLETED,
        evidence_boundary=boundary,
        normalized_metadata=meta,
    )


def item_for(events):
    """Build a CorrelationResult exactly as the engine would."""
    engine = CorrelationEngine()
    engine.add_events(events)
    return engine.get_item("media:1")


NOW = datetime.utcnow()


def hours_ago(h):
    return NOW - timedelta(hours=h)


class TestStageTiming:
    """A. Stage timing."""

    def test_stage_start_time_is_first_event_of_current_run(self):
        """Stage starts at the FIRST event of the current contiguous state run."""
        item = item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(3), svc=SourceService.SONARR),
            ev(EventType.RELEASE_GRABBED, hours_ago(2)),
            ev(EventType.RELEASE_GRABBED, hours_ago(1)),
        ])
        assert item.current_state == EventType.RELEASE_GRABBED
        assert item.stage_start_time == hours_ago(3)

    def test_repeated_polling_does_not_collapse_stage_age(self):
        """Regression: re-observed states must not reset the stage clock to ~0.

        Services re-report the same state on every poll. Anchoring the stage on
        the newest matching event would make every duration rule inert.
        """
        item = item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(4), svc=SourceService.SONARR),
            ev(EventType.RELEASE_GRABBED, hours_ago(3)),
            ev(EventType.RELEASE_GRABBED, hours_ago(2)),
            ev(EventType.RELEASE_GRABBED, hours_ago(1)),
        ])
        assert item.stage_duration_seconds == pytest.approx(4 * 3600, abs=60)

    def test_stage_duration_measured_from_stage_start(self):
        item = item_for([ev(EventType.IMPORT_STARTED, hours_ago(2), svc=SourceService.SONARR)])
        assert item.stage_duration_seconds == pytest.approx(2 * 3600, abs=60)

    def test_stage_start_resets_when_state_changes(self):
        """A new state starts a new stage."""
        item = item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(5), svc=SourceService.SONARR),
            ev(EventType.DOWNLOAD_STARTED, hours_ago(1), svc=SourceService.QBITTORRENT),
        ])
        assert item.current_state == EventType.DOWNLOAD_STARTED
        assert item.stage_start_time == hours_ago(1)
        assert item.stage_duration_seconds == pytest.approx(3600, abs=60)

    def test_no_stage_start_when_state_is_derived(self):
        """A derived state (STUCK) has no observable stage start.

        STUCK is inferred by the state machine from qBittorrent stall metadata,
        never emitted as an event. Stage age must be reported as unknown rather
        than fabricated from unrelated event history.
        """
        item = item_for([
            ev(EventType.DOWNLOAD_PROGRESS, hours_ago(5), svc=SourceService.QBITTORRENT,
               meta={"state": "stalledDL", "progress": 42}),
        ])
        assert item.current_state == EventType.STUCK
        assert item.stage_start_time is None
        assert item.stage_duration_seconds is None

    def test_timezone_aware_timestamps_are_normalized(self):
        """Aware payloads are converted to naive-UTC, not compared as-is."""
        aware = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5)))
        item = item_for([ev(EventType.RELEASE_GRABBED, aware, svc=SourceService.SONARR)])
        assert item.stage_start_time.tzinfo is None
        assert 0 < item.stage_duration_seconds < 60

    def test_mixed_aware_and_naive_timestamps_sort_correctly(self):
        item = item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(3), svc=SourceService.SONARR),
            ev(EventType.RELEASE_GRABBED,
               datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=1)))),
        ])
        assert item.stage_start_time == hours_ago(3)
        assert item.stage_duration_seconds == pytest.approx(3 * 3600, abs=120)

    def test_evidence_age_is_independent_of_stage_age(self):
        """Stage age and evidence age answer different questions."""
        # Stage began 4h ago, but we heard something 2 minutes ago.
        item = item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(4), svc=SourceService.SONARR),
            ev(EventType.RELEASE_GRABBED, NOW - timedelta(minutes=2), svc=SourceService.SONARR),
        ])
        assert item.stage_duration_seconds == pytest.approx(4 * 3600, abs=60)
        assert item.evidence_age_seconds == pytest.approx(120, abs=30)
        assert item.stage_freshness == "STALE"
        assert item.evidence_freshness == "CURRENT"


class TestStageFreshness:
    """B. Stage freshness: RECENT / STALE / UNKNOWN."""

    def test_recent_within_expected_duration(self):
        item = item_for([ev(EventType.RELEASE_GRABBED, hours_ago(0.5), svc=SourceService.SONARR)])
        assert item.current_state == EventType.RELEASE_GRABBED
        assert item.stage_freshness == "RECENT"

    def test_stale_beyond_expected_duration(self):
        item = item_for([ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR)])
        assert item.stage_freshness == "STALE"

    @pytest.mark.parametrize("state,svc", [
        (EventType.RELEASE_GRABBED, SourceService.SONARR),
        (EventType.DOWNLOAD_STARTED, SourceService.QBITTORRENT),
        (EventType.DOWNLOAD_PROGRESS, SourceService.QBITTORRENT),
        (EventType.DOWNLOAD_COMPLETED, SourceService.QBITTORRENT),
        (EventType.IMPORT_STARTED, SourceService.SONARR),
    ], ids=["release_grabbed", "download_started", "download_progress",
            "download_completed", "import_started"])
    def test_every_state_with_threshold_recent_and_stale(self, state, svc):
        """Each state in the shared threshold table flips RECENT -> STALE."""
        expected = STAGE_DURATION_SECONDS[state]
        recent = item_for([ev(state, NOW - timedelta(seconds=expected * 0.5), svc=svc)])
        stale = item_for([ev(state, NOW - timedelta(seconds=expected * 1.5), svc=svc)])
        assert recent.current_state == state
        assert stale.current_state == state
        assert recent.stage_freshness == "RECENT", state
        assert stale.stage_freshness == "STALE", state

    @pytest.mark.parametrize("state,svc", [
        (EventType.WANTED, SourceService.SONARR),
        (EventType.AVAILABLE, SourceService.SONARR),
        (EventType.DOWNLOAD_FAILED, SourceService.QBITTORRENT),
    ])
    def test_states_without_threshold_are_unknown_not_stale(self, state, svc):
        """No expected duration means no opinion, not "stale".

        This is what keeps a long-wanted item from being reported as overdue.
        """
        item = item_for([ev(state, hours_ago(24 * 30), svc=svc)])
        assert item.current_state == state
        assert item.stage_freshness == "UNKNOWN"

    def test_unknown_when_stage_age_unknown(self):
        item = item_for([
            ev(EventType.DOWNLOAD_PROGRESS, hours_ago(5), svc=SourceService.QBITTORRENT,
               meta={"state": "stalledDL", "progress": 42}),
        ])
        assert item.stage_duration_seconds is None
        assert item.stage_freshness == "UNKNOWN"

    def test_orchestrator_shares_the_engine_threshold_table(self):
        """The attention layer must not carry its own divergent thresholds."""
        orch = Orchestrator()
        for state, seconds in STAGE_DURATION_SECONDS.items():
            assert orch.STAGE_DURATION_THRESHOLDS[state] == timedelta(seconds=seconds)


class TestEvidenceRecency:
    """Evidence age is a property of the observation, reported separately."""

    def test_current_within_one_hour(self):
        item = item_for([ev(EventType.RELEASE_GRABBED, NOW - timedelta(minutes=30), svc=SourceService.SONARR)])
        assert item.evidence_freshness == "CURRENT"

    def test_aged_between_one_and_twentyfour_hours(self):
        item = item_for([ev(EventType.RELEASE_GRABBED, hours_ago(5), svc=SourceService.SONARR)])
        assert item.evidence_freshness == "AGED"

    def test_stale_beyond_twentyfour_hours(self):
        item = item_for([ev(EventType.RELEASE_GRABBED, hours_ago(48), svc=SourceService.SONARR)])
        assert item.evidence_freshness == "STALE"

    def test_thresholds_are_the_documented_values(self):
        assert EVIDENCE_AGE_FRESH_SECONDS == 3600.0
        assert EVIDENCE_AGE_AGED_SECONDS == 86400.0


class TestMissingTransition:
    """Missing transition and its evidence."""

    def test_reports_expected_transition_when_absent(self):
        item = item_for([ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR)])
        assert item.missing_transition == EventType.DOWNLOAD_STARTED

    def test_no_missing_transition_for_terminal_state(self):
        item = item_for([ev(EventType.AVAILABLE, hours_ago(2), svc=SourceService.SONARR)])
        assert item.current_state == EventType.AVAILABLE
        assert item.missing_transition is None
        assert "No further transition expected" in item.transition_evidence[0]

    def test_historical_transition_does_not_satisfy_current_wait(self):
        """A past occurrence of the expected event is not evidence of progress.

        Regression guard: scanning the whole timeline would let an old grab
        satisfy a wait that began after a later failure.
        """
        item = item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(10), svc=SourceService.SONARR),
            ev(EventType.DOWNLOAD_STARTED, hours_ago(9), svc=SourceService.QBITTORRENT),
            ev(EventType.DOWNLOAD_FAILED, hours_ago(8), svc=SourceService.QBITTORRENT),
            ev(EventType.WANTED, hours_ago(7), svc=SourceService.SONARR),
            ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR),
        ])
        assert item.current_state == EventType.RELEASE_GRABBED
        assert item.missing_transition == EventType.DOWNLOAD_STARTED
        assert any("has not been observed since the stage began"
                   in line for line in item.transition_evidence)

    def test_reentry_starts_a_new_stage(self):
        """A re-entry begins a new stage; the earlier run does not extend it.

        wanted(-5h) -> grabbed(-4h) -> wanted(-3h): the current wanted stage began
        3h ago, not 5h ago. The grab predates the current stage, so it does not
        satisfy the wait started by that stage.
        """
        item = item_for([
            ev(EventType.WANTED, hours_ago(5), svc=SourceService.SONARR),
            ev(EventType.RELEASE_GRABBED, hours_ago(4), svc=SourceService.SONARR),
            ev(EventType.WANTED, hours_ago(3), svc=SourceService.SONARR),
        ])
        assert item.current_state == EventType.WANTED
        assert item.stage_start_time == hours_ago(3)
        assert item.stage_duration_seconds == pytest.approx(3 * 3600, abs=60)
        # The grab happened before this stage began, so the wait still stands.
        assert item.missing_transition == EventType.RELEASE_GRABBED
        assert any("has not been observed since the stage began"
                   in line for line in item.transition_evidence)

    def test_advancing_state_clears_the_previous_gap(self):
        """Normal forward progress: the expected transition landed, nothing missing."""
        item = item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(4), svc=SourceService.SONARR),
            ev(EventType.DOWNLOAD_STARTED, NOW - timedelta(minutes=10), svc=SourceService.QBITTORRENT),
        ])
        # The item advanced to download_started; its own expected transition
        # (download_progress) has not happened, so that is the live gap.
        assert item.current_state == EventType.DOWNLOAD_STARTED
        assert item.missing_transition == EventType.DOWNLOAD_PROGRESS
        assert any("has not been observed since the stage began"
                   in line for line in item.transition_evidence)


class TestGrabNoDownload:
    """C. GRAB_NO_DOWNLOAD."""

    def classify(self, **kw):
        return Orchestrator().classify_item(item_for(kw["events"]))

    def test_below_timeout_no_attention(self):
        r = self.classify(events=[ev(EventType.RELEASE_GRABBED, hours_ago(0.1), svc=SourceService.SONARR)])
        assert r["attention_level"] == AttentionLevel.NONE
        assert r["requires_attention"] is False

    def test_above_timeout_possible(self):
        r = self.classify(events=[ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR)])
        assert r["attention_class"] == "GRAB_NO_DOWNLOAD"
        assert r["attention_level"] == AttentionLevel.POSSIBLE
        assert r["severity"] == "medium"

    def test_above_24h_required(self):
        r = self.classify(events=[ev(EventType.RELEASE_GRABBED, hours_ago(30), svc=SourceService.SONARR)])
        assert r["attention_class"] == "GRAB_NO_DOWNLOAD"
        assert r["attention_level"] == AttentionLevel.REQUIRED
        assert r["severity"] == "high"

    def test_subsequent_download_started_clears_condition(self):
        """Self-clearing: the condition only applies while still grabbed."""
        r = self.classify(events=[
            ev(EventType.RELEASE_GRABBED, hours_ago(3), svc=SourceService.SONARR),
            ev(EventType.DOWNLOAD_STARTED, NOW - timedelta(minutes=5), svc=SourceService.QBITTORRENT),
        ])
        assert r["attention_class"] != "GRAB_NO_DOWNLOAD"
        assert r["attention_level"] == AttentionLevel.NONE

    def test_evidence_names_the_missing_transition(self):
        r = self.classify(events=[ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR)])
        assert r["missing_transition"] == "download_started"
        assert any("No download_started event observed" in line for line in r["evidence"])


class TestDownloadNoImport:
    """D. DOWNLOAD_NO_IMPORT."""

    def classify(self, events):
        return Orchestrator().classify_item(item_for(events))

    def test_below_timeout_no_attention(self):
        r = self.classify([ev(EventType.DOWNLOAD_COMPLETED, hours_ago(0.5), svc=SourceService.QBITTORRENT)])
        assert r["attention_level"] == AttentionLevel.NONE

    def test_above_timeout_possible(self):
        r = self.classify([ev(EventType.DOWNLOAD_COMPLETED, hours_ago(5), svc=SourceService.QBITTORRENT)])
        assert r["attention_class"] == "DOWNLOAD_NO_IMPORT"
        assert r["attention_level"] == AttentionLevel.POSSIBLE

    def test_above_24h_required(self):
        r = self.classify([ev(EventType.DOWNLOAD_COMPLETED, hours_ago(25), svc=SourceService.QBITTORRENT)])
        assert r["attention_level"] == AttentionLevel.REQUIRED

    def test_subsequent_import_started_clears_condition(self):
        r = self.classify([
            ev(EventType.DOWNLOAD_COMPLETED, hours_ago(5), svc=SourceService.QBITTORRENT),
            ev(EventType.IMPORT_STARTED, NOW - timedelta(minutes=5), svc=SourceService.SONARR),
        ])
        assert r["attention_class"] != "DOWNLOAD_NO_IMPORT"
        assert r["attention_level"] == AttentionLevel.NONE

    def test_reachable_and_not_preempted_by_generic_incomplete_rule(self):
        """Regression: the generic incomplete-state rule used to shadow this."""
        r = self.classify([ev(EventType.DOWNLOAD_COMPLETED, hours_ago(5), svc=SourceService.QBITTORRENT)])
        assert r["attention_class"] == "DOWNLOAD_NO_IMPORT"


class TestUnusuallyLongStage:
    """E. UNUSUALLY_LONG_STAGE, using import_started (1h expectation)."""

    def classify(self, h):
        return Orchestrator().classify_item(
            item_for([ev(EventType.IMPORT_STARTED, hours_ago(h), svc=SourceService.SONARR)])
        )

    def test_below_threshold_no_attention(self):
        r = self.classify(0.5)
        assert r["attention_level"] == AttentionLevel.NONE
        assert r["attention_class"] == "IMPORT_IN_PROGRESS"

    def test_just_over_threshold_possible(self):
        r = self.classify(1.2)
        assert r["attention_class"] == "UNUSUALLY_LONG_STAGE"
        assert r["attention_level"] == AttentionLevel.POSSIBLE
        assert r["severity"] == "medium"

    def test_over_1_5x_threshold_required(self):
        r = self.classify(2.0)
        assert r["attention_class"] == "UNUSUALLY_LONG_STAGE"
        assert r["attention_level"] == AttentionLevel.REQUIRED
        assert r["severity"] == "medium"

    def test_over_3x_threshold_required_high(self):
        r = self.classify(4.0)
        assert r["attention_class"] == "UNUSUALLY_LONG_STAGE"
        assert r["attention_level"] == AttentionLevel.REQUIRED
        assert r["severity"] == "high"

    def test_reachable_for_import_started(self):
        """Regression: import_started was previously unreachable here."""
        assert self.classify(2.0)["attention_class"] == "UNUSUALLY_LONG_STAGE"

    def test_never_fires_for_state_without_threshold(self):
        r = Orchestrator().classify_item(
            item_for([ev(EventType.WANTED, hours_ago(24 * 7), svc=SourceService.SONARR)])
        )
        assert r["attention_class"] != "UNUSUALLY_LONG_STAGE"
        assert r["attention_level"] == AttentionLevel.NONE


class TestHistoricalFalsePositiveProtection:
    """G. Old evidence must not become a current incident on its own."""

    @pytest.mark.parametrize("state,svc", [
        (EventType.AVAILABLE, SourceService.SONARR),
        (EventType.IMPORT_COMPLETED, SourceService.SONARR),
        (EventType.WANTED, SourceService.SONARR),
        (EventType.SEARCH_STARTED, SourceService.SONARR),
        (EventType.SEARCH_FAILED, SourceService.SONARR),
    ])
    def test_ancient_settled_item_is_never_an_incident(self, state, svc):
        r = Orchestrator().classify_item(item_for([ev(state, hours_ago(24 * 30), svc=svc)]))
        assert r["attention_level"] == AttentionLevel.NONE, state
        assert r["requires_attention"] is False, state

    def test_ancient_completed_pipeline_is_not_an_incident(self):
        r = Orchestrator().classify_item(item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(24 * 30), svc=SourceService.SONARR),
            ev(EventType.DOWNLOAD_STARTED, hours_ago(24 * 30 - 1), svc=SourceService.QBITTORRENT),
            ev(EventType.DOWNLOAD_COMPLETED, hours_ago(24 * 30 - 2), svc=SourceService.QBITTORRENT),
            ev(EventType.IMPORT_STARTED, hours_ago(24 * 30 - 3), svc=SourceService.SONARR),
            ev(EventType.IMPORT_COMPLETED, hours_ago(24 * 30 - 4), svc=SourceService.SONARR),
            ev(EventType.AVAILABLE, hours_ago(24 * 29), svc=SourceService.SONARR),
        ]))
        assert r["attention_level"] == AttentionLevel.NONE

    def test_stale_evidence_is_still_reported_as_context(self):
        """Removed as an attention class, but the fact is not thrown away."""
        r = Orchestrator().classify_item(
            item_for([ev(EventType.WANTED, hours_ago(24 * 30), svc=SourceService.SONARR)])
        )
        assert r["evidence_freshness"] == "STALE"
        assert any("days old" in line and "historical evidence" in line
                   for line in r["evidence"])

    def test_absence_of_stale_event_attention_class(self):
        """The old 7-day STALE_EVENT rule is gone as an incident generator."""
        r = Orchestrator().classify_item(
            item_for([ev(EventType.WANTED, hours_ago(24 * 30), svc=SourceService.SONARR)])
        )
        assert r["attention_class"] != "STALE_EVENT"
        assert not hasattr(Orchestrator, "_stale_event_classification")


class TestEvidenceBoundaries:
    """H. All six boundaries still propagate."""

    def test_observed_single_source(self):
        item = item_for([ev(EventType.RELEASE_GRABBED, hours_ago(1), svc=SourceService.SONARR)])
        assert item.evidence_boundary == EvidenceBoundary.OBSERVED

    def test_correlated_exact_hash_match(self):
        item = item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR, download_id="abc123"),
            ev(EventType.DOWNLOAD_PROGRESS, hours_ago(1), svc=SourceService.QBITTORRENT,
               download_id="abc123", meta={"progress": 50}),
        ])
        assert item.evidence_boundary == EvidenceBoundary.CORRELATED
        assert item.confidence == "HIGH"

    def test_inferred_without_hash_match(self):
        item = item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR, download_id="hashA"),
            ev(EventType.DOWNLOAD_PROGRESS, hours_ago(1), svc=SourceService.QBITTORRENT,
               download_id="hashB", meta={"progress": 50, "category": "radarr"}),
        ])
        assert item.evidence_boundary == EvidenceBoundary.INFERRED

    def test_blocked_propagates_and_stays_blocked(self):
        item = item_for([
            ev(EventType.DOWNLOAD_FAILED, hours_ago(1), svc=SourceService.QBITTORRENT,
               boundary=EvidenceBoundary.BLOCKED),
        ])
        assert item.evidence_boundary == EvidenceBoundary.BLOCKED
        r = Orchestrator().classify_item(item)
        assert r["attention_level"] == AttentionLevel.BLOCKED
        assert r["evidence_boundary"] == "blocked"
        assert "unavailable" in r["reason"].lower()

    def test_synthetic_is_excluded_from_attention(self):
        item = item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(1), svc=SourceService.SONARR,
               boundary=EvidenceBoundary.SYNTHETIC),
        ])
        assert item.evidence_boundary == EvidenceBoundary.SYNTHETIC
        r = Orchestrator().classify_item(item)
        assert r["attention_level"] == AttentionLevel.SYNTHETIC
        assert r["requires_attention"] is False

    def test_unknown_propagates(self):
        item = item_for([
            ev(EventType.UNKNOWN, hours_ago(1), svc=SourceService.SONARR,
               boundary=EvidenceBoundary.UNKNOWN),
        ])
        assert item.evidence_boundary == EvidenceBoundary.UNKNOWN

    def test_search_started_has_no_duration_threshold(self):
        """Regression: search_started was judged against a 1h expectation.

        A search runs until a release exists. An unaired episode legitimately
        stays in search_started for months, so this state must be unbounded and
        must never produce attention on elapsed time alone.
        """
        assert EventType.SEARCH_STARTED not in STAGE_DURATION_SECONDS
        item = item_for([ev(EventType.SEARCH_STARTED, hours_ago(24 * 30), svc=SourceService.SONARR)])
        assert item.current_state == EventType.SEARCH_STARTED
        assert item.stage_freshness == "UNKNOWN"
        r = Orchestrator().classify_item(item)
        assert r["attention_level"] == AttentionLevel.NONE

    def test_boundaries_propagate_through_classification(self):
        """Observed and Unknown survive the classification envelope intact."""
        for boundary in (EvidenceBoundary.OBSERVED, EvidenceBoundary.UNKNOWN):
            r = Orchestrator().classify_item(
                item_for([ev(EventType.RELEASE_GRABBED, hours_ago(1), svc=SourceService.SONARR,
                             boundary=boundary)])
            )
            assert r["evidence_boundary"] == boundary.value, boundary

    def test_inferred_boundary_needs_a_cross_source_join(self):
        """A single-source item is directly OBSERVED, not INFERRED.

        Inference is only meaningful when several sources had to be joined
        without an exact identifier match.
        """
        single = item_for([ev(EventType.RELEASE_GRABBED, hours_ago(1), svc=SourceService.SONARR,
                              boundary=EvidenceBoundary.INFERRED)])
        assert single.evidence_boundary == EvidenceBoundary.OBSERVED

        joined = item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR, download_id="hashA"),
            ev(EventType.DOWNLOAD_PROGRESS, hours_ago(1), svc=SourceService.QBITTORRENT,
               download_id="hashB", meta={"progress": 50}),
        ])
        assert joined.evidence_boundary == EvidenceBoundary.INFERRED
        r = Orchestrator().classify_item(joined)
        assert r["evidence_boundary"] == "inferred"
        assert any("inferred correlation" in line for line in r["evidence"])


class TestProwlarrStaysContextual:
    """I. Prowlarr remains contextual evidence only."""

    def test_prowlarr_event_is_observed_without_causal_claim(self):
        from backend.adapters.prowlarr import ProwlarrAdapter

        event = ProwlarrAdapter()._history_to_event({
            "eventType": "indexersearch",
            "movieId": 1,
            "releaseTitle": "Test.Release.2026",
            "date": "2026-09-27T10:00:00Z",
        })
        assert event is not None
        assert event.source_service == SourceService.PROWLARR
        assert event.event_type == EventType.INDEXER_SEARCH
        assert event.evidence_boundary == EvidenceBoundary.OBSERVED
        assert event.normalized_metadata["causal_boundary"] == "no_causal_link_implied"

    def test_prowlarr_search_does_not_create_a_causal_chain(self):
        """A Prowlarr search must not become evidence that a download followed.

        The two events belong to different correlation groups, so the search
        cannot be read as part of the grab item's pipeline.
        """
        engine = CorrelationEngine()
        engine.add_events([
            ev(EventType.INDEXER_SEARCH, hours_ago(3), svc=SourceService.PROWLARR,
               key="media:9", media_id="9"),
            ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR, key="media:1"),
        ])
        grabbed = engine.get_item("media:1")
        search = engine.get_item("media:9")
        assert grabbed is not None and search is not None
        assert all(e.source_service != SourceService.PROWLARR for e in grabbed.events)
        # The search itself stays OBSERVED and self-identifies as non-causal.
        assert search.evidence_boundary == EvidenceBoundary.OBSERVED
        assert search.current_state == EventType.INDEXER_SEARCH

    def test_prowlarr_stage_is_not_judged_as_a_pipeline_stage(self):
        r = Orchestrator().classify_item(
            item_for([ev(EventType.INDEXER_SEARCH, hours_ago(24 * 3), svc=SourceService.PROWLARR)])
        )
        assert r["attention_level"] == AttentionLevel.NONE


class TestCompletedPipeline:
    """J. A fully completed item generates no Phase 4C attention."""

    def test_complete_pipeline_is_clean(self):
        r = Orchestrator().classify_item(item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(4), svc=SourceService.SONARR),
            ev(EventType.DOWNLOAD_STARTED, hours_ago(3.8), svc=SourceService.QBITTORRENT),
            ev(EventType.DOWNLOAD_COMPLETED, hours_ago(2), svc=SourceService.QBITTORRENT),
            ev(EventType.IMPORT_STARTED, hours_ago(1.8), svc=SourceService.SONARR),
            ev(EventType.IMPORT_COMPLETED, hours_ago(0.8), svc=SourceService.SONARR),
            ev(EventType.AVAILABLE, hours_ago(0.6), svc=SourceService.SONARR),
        ]))
        assert r["state"] == "available"
        assert r["attention_class"] == "COMPLETED"
        assert r["attention_level"] == AttentionLevel.NONE
        assert r["requires_attention"] is False
        # A completed item has nothing further to wait for.
        assert r["missing_transition"] is None

    def test_completed_item_absent_from_attention_list(self):
        orch = Orchestrator()
        items = [item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(4), svc=SourceService.SONARR),
            ev(EventType.DOWNLOAD_STARTED, hours_ago(3.8), svc=SourceService.QBITTORRENT),
            ev(EventType.DOWNLOAD_COMPLETED, hours_ago(2), svc=SourceService.QBITTORRENT),
            ev(EventType.IMPORT_STARTED, hours_ago(1.8), svc=SourceService.SONARR),
            ev(EventType.IMPORT_COMPLETED, hours_ago(0.8), svc=SourceService.SONARR),
            ev(EventType.AVAILABLE, hours_ago(0.6), svc=SourceService.SONARR),
        ])]
        assert orch.get_attention_items(items) == []


class TestClassificationEnvelope:
    """Every classification path returns the same shape."""

    REQUIRED_KEYS = {
        "attention_level", "attention_class", "reason", "evidence",
        "evidence_boundary", "confidence_basis", "requires_attention",
        "severity", "state", "stage_freshness", "evidence_freshness",
        "stage_duration_seconds", "evidence_age_seconds", "missing_transition",
    }

    @pytest.mark.parametrize("events", [
        [ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR)],
        [ev(EventType.DOWNLOAD_COMPLETED, hours_ago(5), svc=SourceService.QBITTORRENT)],
        [ev(EventType.IMPORT_STARTED, hours_ago(2), svc=SourceService.SONARR)],
        [ev(EventType.AVAILABLE, hours_ago(2), svc=SourceService.SONARR)],
        [ev(EventType.WANTED, hours_ago(2), svc=SourceService.SONARR)],
        [ev(EventType.DOWNLOAD_FAILED, hours_ago(2), svc=SourceService.QBITTORRENT)],
    ], ids=["grab-gap", "import-gap", "long-stage", "completed", "waiting", "failed"])
    def test_envelope_is_uniform(self, events):
        r = Orchestrator().classify_item(item_for(events))
        assert self.REQUIRED_KEYS.issubset(r.keys()), sorted(self.REQUIRED_KEYS - set(r))
        assert r["requires_attention"] == (r["attention_level"] != AttentionLevel.NONE)

    def test_attention_item_metadata_carries_both_recency_axes(self):
        item = item_for([
            ev(EventType.RELEASE_GRABBED, hours_ago(3), svc=SourceService.SONARR),
            ev(EventType.RELEASE_GRABBED, NOW - timedelta(minutes=2), svc=SourceService.SONARR),
        ])
        results = Orchestrator().get_attention_items([item])
        assert len(results) == 1
        meta = results[0]["item"]
        assert meta["stage_freshness"] == "STALE"
        assert meta["evidence_freshness"] == "CURRENT"
        assert meta["stage_duration_seconds"] > 10000
        assert meta["evidence_age_seconds"] < 300


class TestToDictContract:
    """Serialization must not drop the Phase 4C fields."""

    def test_to_dict_exposes_stage_and_evidence_fields(self):
        d = CorrelationResult("media:1", [
            ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR),
        ]).to_dict()
        for key in ("stage_start_time", "stage_duration_seconds", "stage_freshness",
                    "missing_transition", "transition_evidence",
                    "evidence_age_seconds", "evidence_freshness",
                    "confidence_basis", "evidence_boundary", "first_seen_at"):
            assert key in d, key

    def test_single_to_dict_definition(self):
        """Regression: a shadowed duplicate to_dict silently dropped fields."""
        import inspect
        source = inspect.getsource(CorrelationResult)
        assert source.count("def to_dict(") == 1
