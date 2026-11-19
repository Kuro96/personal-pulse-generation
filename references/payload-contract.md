# Payload contract

New editions use `pulse_version: "1.1"`. The runtime's strict validator is the
executable contract; invoke it with the explicit day and state file before send.
Historical 1.0/1.1 records keep their original bindings for duplicate/resume checks.

## Edition

- `pulse_day`: `YYYY-MM-DD` in `user.timezone`; `generated_at`: ISO timestamp; `language`: `user.language`.
- `daily_target`: persisted 6–8 `count`, with `timezone` set to `user.timezone`.
- `scope.user`: `user.name`; `scope.time_window`: inspected period.
- `scope.requested_card_count`: persisted ceiling.
- `scope.selected_card_count`: actual `cards` length, from 1 through ceiling.
- `scope.content_classes_considered`: exactly `C1`, `C2`, `C3`, `C4`.
- `scope.time_horizons_considered`: exactly `today_72h`, `recent_2w`, `long_term`.
- `scope.sources_used`: source names; `scope.sources_unavailable`: `{source, reason}` objects.
- `scope.selection_exceptions`: exact `{type, value, reason}` objects, as below.
- `cards`: selected cards in final feed order.
- `feed_cover_image`: cover metadata for the original uncut collage.
- `omitted_candidates`: concise `{title, reason_omitted}` objects when useful.
- `global_uncertainties`: internal strings including exact exception reasons.
- `follow_up_questions`: optional actual unresolved questions.

`scope.refresh_status`, when supplied, contains exactly `desired_count`,
`selected_count`, and `note`. Counts are integers matching the current preference
and actual refresh-tagged selections. A shortfall requires this object and a
nonempty reader-safe note. Otherwise the object is optional and note may be empty.

For each class with more than `ceil(actual/2)` cards, include one
`class_concentration` exception with that class as `value`. For every absent
horizon, include one `missing_horizon` exception with that horizon as `value`.
Each reason is nonempty and appears verbatim in `global_uncertainties`. Include
exactly the necessary unique exceptions.

## Card

| Field | Contract |
|---|---|
| `id` | Unique nonempty string |
| `type` | `recommendation`, `reminder`, `opportunity`, `reflection`, `risk`, `project_nudge` |
| `priority` | `high`, `medium`, `low` |
| `content_class` | C1 conversation; C2 everyday useful information; C3 goal progress; C4 external |
| `time_horizon` | `today_72h`, `recent_2w`, `long_term` |
| `event_key` | Stable unique underlying event/contribution identity |
| `recent_topic_repeat` | Boolean |
| `novelty_reason` | `new_information`, `new_state`, `current_relevance`; legacy `completed_deliverable`, `real_time_window`, `unresolved_decision` accepted |
| `title`, `subtitle`, `summary` | Reader-safe text in `user.language`; summary adds distinct supporting content |
| `feed_copy` | `card_teaser`, `cover_alt`; concise complete teaser |
| `recommendation`, `why_now` | Useful conclusion and current relevance |
| `delivery` | See below |
| `feedback_context` | See below |
| `suggested_next_action` | `label`, `estimated_effort`, `action_type`; displayed when action required |
| `evidence` | At least one evidence object |
| `uncertainty` | `level`, `reason`, `what_would_reduce_uncertainty` |
| `confidence` | Required finite number from 0 to 1; numeric percent inputs remain readable for historical compatibility |
| `tags`, `related_projects` | String arrays; refresh stays an internal tag |
| `sensitivity` | `contains_private_context`, `safe_to_share_publicly`, `notes` |
| `illustration_brief` | `enabled` boolean, nonempty `prompt` and `style`, string-array `avoid` |
| `cover_image` | See below |

`delivery` contains `state`, boolean `user_action_required`, nonempty
`result_summary`, and `empty_result: false`. States `delivered` and `insight` use
false; `decision_needed` and `action_needed` use true. At most two cards use true.

`feedback_context` contains `mode: "natural_language_reply"`, `card_id` exactly
matching `id`, and nonempty `accepted_intents` drawn from `follow`, `completed`,
`mute_until`, `boost_topic`, `reduce_action_load`.

Action effort values: `5m`, `15m`, `30m`, `1h`, `multi-session`. Action types:
`write`, `review`, `decide`, `message`, `research`, `build`, `schedule`, `rest`, `other`.
Structured action fields can remain empty for directly consumable insight.

`uncertainty.level` is `low`, `medium`, or `high`. Sensitivity defaults to private
context with `safe_to_share_publicly: false`.

## Evidence and public citations

Each evidence object carries `claim`, `source_type`, `source_ref`,
`quote_or_summary`, `recency` (`recent|older|unknown`), and `strength`
(`strong|medium|weak`). Source types are `recent_session`, `obsidian_note`,
`project_state`, `external_source`, `memory`, `user_prompt`. Personal-context notes
can use `obsidian_note` with their exact internal path/provenance.

An external item requires a safe public HTTPS `source_url`; C4 requires at least
one external item. Set `publicly_renderable: true` for a safe public citation;
private evidence uses false. `source_label` is optional internal context. Reader
citation labels derive from the safe URL domain. Credential/signing-like query
parameters fail public citation checks. `source_ref` stays internal.

For inline placement, write the supported sentence in a visible detail field
(e.g. `summary` or `delivery.result_summary`) and use that exact sentence as the
external evidence `claim`. The renderer places the domain link at the end of the
first matching paragraph. Each approved URL appears once; unmatched URLs retain
the source-list layout. Placement uses existing prose and adds no required field.

## Covers and delivery binding

Each cover has `status`, `url`, `file`, and nonempty `alt`. Use `generated` for
verified images, with `url: null` when uploading local files. Statuses `ready`,
`generated`, and `available` require media. Local media must pass the native
preflight described in the publication reference.

Approved all-text editions use `force_majeure_text_fallback` or
`user_opt_out_text_fallback`, null media fields, and a nonempty concrete internal
`reason` on every card. The top-level cover can be omitted for an all-text edition
or carry matching fallback metadata. Image editions require the uncut top-level
collage; a one-card edition shares its exact file/URL with the card.

`detail_message` may carry `target` set to `delivery.cards_target` and initially
null `message_id`/`url`. Runtime send binds each card to the explicit cards target;
a conflicting pre-bound target fails. IDs and links come from journaled results.
