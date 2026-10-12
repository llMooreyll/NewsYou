# Content model

The normalized model is deliberately platform-neutral. Platform-specific fields remain in `platform_data`; important evidence is normalized into common fields.

## `SourceConfig`

```yaml
id: x-open-source-list
platform: x
mode: api              # feed | api | browser | manual
locator: list:123456
language: [en]
topics: [technology]
interval_minutes: 60
max_items_per_run: 50
media_policy: metadata_only
credentials_ref: env:X_BEARER_TOKEN
retention_days: 90
enabled: true
```

### Fields

| Field | Meaning |
|---|---|
| `id` | Stable local identifier |
| `platform` | `web`, `wechat`, `x`, `bilibili`, `weibo`, `reddit`, `podcast`, etc. |
| `mode` | Capture mechanism selected by policy |
| `locator` | Feed URL, account/list ID, subreddit, or source URL |
| `language` | Hint only; actual language is detected later |
| `topics` | Optional source-level hints |
| `interval_minutes` | Poll interval subject to platform limits |
| `max_items_per_run` | Backpressure and cost control |
| `media_policy` | `metadata_only`, `captions`, `permitted_download`, `manual_only` |
| `credentials_ref` | Name of a secret reference, never a secret value |
| `retention_days` | Raw artifact retention policy |

## `Candidate`

A lightweight item found during discovery.

```json
{
  "candidate_id": "reddit:t3_abc123",
  "source_id": "reddit-technology",
  "platform": "reddit",
  "external_id": "t3_abc123",
  "url": "https://www.reddit.com/r/technology/comments/abc123/example/",
  "title": "Example",
  "published_at": "2026-01-01T12:00:00Z",
  "discovered_at": "2026-01-01T12:05:00Z",
  "capture_plan": "official_api",
  "dedupe_key": "reddit:t3_abc123"
}
```

Candidates are cheap to create and can be discarded before full capture.

## `RawCapture`

```json
{
  "capture_id": "cap_01J...",
  "candidate_id": "reddit:t3_abc123",
  "captured_at": "2026-01-01T12:06:00Z",
  "method": "official_api",
  "adapter": "reddit.oauth.v1",
  "adapter_version": "0.1.0",
  "requested_url": "https://www.reddit.com/r/technology/comments/abc123/example/",
  "final_url": "https://www.reddit.com/r/technology/comments/abc123/example/",
  "status": "ok",
  "http_status": 200,
  "content_type": "application/json",
  "raw_path": "data/raw/sha256/abc.json",
  "content_sha256": "sha256:...",
  "expires_at": "2026-04-01T12:06:00Z",
  "observed_fields": ["title", "body", "author", "created_utc"]
}
```

A browser capture additionally records `browser_context: authorized_session` but never stores cookies in the capture record.

## `ContentItem`

```json
{
  "id": "item_01J...",
  "identity": {
    "platform": "podcast",
    "external_id": "feed-guid-or-episode-guid",
    "canonical_url": "https://example.org/episode/1",
    "content_sha256": "sha256:..."
  },
  "source": {
    "source_id": "podcast-example",
    "publisher": "Example Show",
    "author": ["Guest Name"],
    "platform": "podcast"
  },
  "content": {
    "title": "Original title",
    "body_original": "Original text or show notes",
    "language": "en",
    "content_type": "audio",
    "published_at": "2026-01-01T12:00:00Z",
    "duration_seconds": 3600,
    "segments": [
      {
        "start": 123.4,
        "end": 145.8,
        "text": "Timestamped evidence",
        "kind": "publisher_transcript",
        "confidence": 1.0
      }
    ]
  },
  "derived": {
    "title_translation": null,
    "summary_original": null,
    "summary_translation": null,
    "topics": [],
    "entities": [],
    "claims": [],
    "embedding_ref": null
  },
  "provenance": [
    {
      "field": "content.segments[0]",
      "kind": "observed_transcript",
      "capture_id": "cap_01J...",
      "locator": "00:02:03-00:02:25"
    }
  ],
  "quality": {
    "capture_confidence": 0.98,
    "transcript_confidence": 1.0,
    "evidence_complete": true,
    "needs_review": false
  },
  "workflow": {
    "state": "enriched",
    "created_at": "2026-01-01T12:06:00Z",
    "updated_at": "2026-01-01T12:10:00Z"
  },
  "platform_data": {}
}
```

## State machine

```text
discovered
  -> captured
  -> extracted
  -> normalized
  -> [transcribed]
  -> enriched
  -> deduplicated
  -> ranked
  -> review
  -> accepted | rejected | deferred
  -> published
```

Failures are explicit:

```text
captured -> capture_failed
transcribed -> transcription_failed
any state -> needs_manual_input
```

A retry must be idempotent. It should create a new capture attempt, not mutate the original raw artifact.

## Evidence rules

1. A summary may not cite a field whose provenance is missing.
2. Claims from social posts are labeled as claims or opinions unless independently verified.
3. ASR-derived evidence must include a timestamp and model metadata.
4. Translations never replace the original text.
5. If a page is inaccessible after capture, the archive keeps the source URL and local evidence only for the configured retention period.
6. Deleted or edited platform content keeps its last observed state with a status flag; it is not silently rewritten.
