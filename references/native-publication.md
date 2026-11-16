# Native publication and recovery

The publisher loads `cooperative-answering`, reviews the final rendered prose, and
uses `pulse_runtime.py` for validation and sending.

## Paths and day

Resolve `SKILL_DIR` to the installed skill directory (the `skill_dir` returned by
skill discovery). Keep the following shell state in a private local process;
`show` outputs private resolved paths, identity, and destinations. Export the same
absolute `PULSE_CONFIG` to every runtime process and worker that needs configuration.

```bash
set -euo pipefail
: "${SKILL_DIR:?Set SKILL_DIR from installed skill discovery}"
export PULSE_CONFIG="${PULSE_CONFIG:-$SKILL_DIR/config.local.json}"
# Bootstrap with an available Python; subsequent commands use configured Python.
python3 -B "$SKILL_DIR/scripts/pulse_config.py" --config "$PULSE_CONFIG" check
PULSE_CONFIG_JSON="$(python3 -B "$SKILL_DIR/scripts/pulse_config.py" --config "$PULSE_CONFIG" show)"
cfg_get() {
  printf '%s' "$PULSE_CONFIG_JSON" | python3 -c \
    'import json,sys; v=json.load(sys.stdin); a,b=sys.argv[1].split("."); print(v[a][b])' "$1"
}
PYTHON="$(cfg_get paths.python_executable)"
STATE="$(cfg_get paths.state_file)"
WORKDIR="$(cfg_get paths.workdir)"
IMAGE_EXECUTOR="$(cfg_get paths.image_executor)"
CARDS_TARGET="$(cfg_get delivery.cards_target)"
FEED_TARGET="$(cfg_get delivery.feed_target)"
PULSE_TZ="$(cfg_get user.timezone)"
SCRIPT="$SKILL_DIR/scripts/pulse_runtime.py"
PULSE_DAY="$(TZ="$PULSE_TZ" date +%F)"
DAY_KEY="$(TZ="$PULSE_TZ" date +%Y%m%d)"
JOURNAL="$(cfg_get paths.journal_dir)/pulse_${DAY_KEY}.send.json"
cd "$WORKDIR"
PULSE_RUN_ROOT="$(PYTHONPATH="$SKILL_DIR/scripts" "$PYTHON" -B -c 'import pulse_runtime; print(pulse_runtime.PULSE_RUN_ROOT)')"
```

Use absolute `SKILL_DIR` and `PULSE_CONFIG` paths before changing directories.
The runtime resolves the run root from `paths.scratch_dir` plus `hermes-pulse`.
`PULSE_RUN_ROOT` above holds that result; it is a shell variable for subsequent
commands. Create a unique `$PULSE_RUN_ROOT/$DAY_KEY/<run-id>/` and set `RUN_DIR` to it.

Read the same-day journal and active-run evidence before beginning a new edition.
Inspect recent job/session records and run artifacts when a publisher may already
be working. The durable journal determines publication status.

```bash
"$PYTHON" -B "$SCRIPT" daily-target get-or-create --pulse-day "$PULSE_DAY" --state-file "$STATE"
```

Read the preference state for `last_preference` and topic suppression. Keep target,
preference, and historical delivery state intact across retries.

## Validate and preview

Write the final payload in `RUN_DIR`, then:

```bash
"$PYTHON" -B "$SCRIPT" validate --pretty --strict-count \
  --pulse-day "$PULSE_DAY" --state-file "$STATE" \
  "$RUN_DIR/pulse_${DAY_KEY}.json" > "$RUN_DIR/pulse_${DAY_KEY}.normalized.json"
"$PYTHON" -B "$SCRIPT" render-details "$RUN_DIR/pulse_${DAY_KEY}.normalized.json" \
  --out-dir "$RUN_DIR/details"
"$PYTHON" -B "$SCRIPT" render-feed "$RUN_DIR/pulse_${DAY_KEY}.normalized.json"
```

Review complete detail text and the feed. `render-feed` outputs the publisher's
native HTML and applies the same media-dependent 1000/3900 UTF-16 budget. Before
publication it reserves link space with provisional message IDs; actual title
links are bound from returned detail IDs during send. For a resume,
`render-feed --detail-refs-json '<journal detail_refs object>'` uses known links.
The provisional links serve layout checking; published links come from receipts.
Send preflights this feed layout before creating a journal or sending details,
then renders the actual linked feed before recording its pending network operation.

## Reader surfaces

Each detail is one authoritative HTML message in `delivery.cards_target`.
Write title and natural paragraphs, with the useful result first, distinct support
and current relevance integrated, safe public citations, and an action only when
required. Internal provenance, uncertainty/confidence, event keys, and candidate
assessments stay in the payload. Recurring `已交付洞察/摘要/为什么现在` labels and
QA lectures are outside the reading format.

Complete details up to 1000 UTF-16 units use a photo caption when a cover exists.
Longer details through 3900 use one text message; the feed collage supplies the
visual. Details over 3900 fail preflight and require editing or a safe reachable
full-text source. Preserve complete sentences and arguments.

After all details, `delivery.feed_target` receives exactly one linked-title summary. Image runs
use the uncut collage with an HTML caption up to 1000 UTF-16 units. Approved
all-text runs use one HTML message up to 3900. Every card appears once with a
compact teaser; the runtime constructs title links from the configured target and
journaled message IDs.
A compact teaser shortened for space ends with a visible ellipsis. Keep the
refresh-shortfall and image-fallback notes short and reader-safe when applicable.
Natural-language replies supply feedback.

## Send

```bash
"$PYTHON" -B "$SCRIPT" send "$RUN_DIR/pulse_${DAY_KEY}.normalized.json" \
  --pulse-day "$PULSE_DAY" --state-file "$STATE" --send-record "$JOURNAL" \
  --cards-target "$CARDS_TARGET" \
  --feed-target "$FEED_TARGET"
```

Runtime preflight checks exact configured targets,
card identity, count, complete rendering, and covers before journal/network work.
Local covers must be non-symlink regular PNG/JPEG files beneath the resolved run
root, with matching extension/signature, positive size, and at most 10 MB.
Public source URLs pass safe HTTPS checks, including credential-query rejection.

The runtime acquires `<send-record>.lock`, atomically writes `in_flight` before
an operation, and atomically persists its positive returned ID/ref before the next
operation. The feed is last. Completion records every actual selected card and the
feed, with desired count stored separately.

Read back the exact journal. Require complete status, expected day/count/digest/
targets, positive detail/feed IDs, exact detail/ref sets and matching official links,
and cleared `in_flight`. Cron success returns exactly `[SILENT]`; failure returns
`PULSE_SEND_FAILED: <day> <stage>: <safe concrete reason>`.

## Recovery

- Complete matching native or historical legacy journal: keep the existing edition;
  scheduled response is `[SILENT]` after verification.
- Valid partial native journal: resume the same normalized payload, day, actual
  count, persisted ceiling, digest, IDs/refs, and exact targets through runtime send.
- Unresolved `in_flight`, conflicting digest/targets, inconsistent status/IDs, or
  missing journal after apparent sends: stop for operator reconciliation. Clearing
  progress or reconstructing a journal risks duplicate Telegram operations.
- Active run with no journal yet: inspect session/log/artifact progress and wait
  for that publisher before starting another.
- Missing optional source: continue collection and record the source limitation.
- Inaccessible working directory: verify `paths.workdir` and report the concrete
  deployment configuration problem for authorized maintenance.

Historical journals, payload bindings, and expiry fields remain unchanged as
history/dedup evidence. Current publication is native Telegram with retained
messages. Historical Telegraph fields are read-only compatibility records.

## Feedback and offline checks

Interpret an actual user reply in its card context, then use the runtime's explicit
preference command when appropriate:

```bash
"$PYTHON" -B "$SCRIPT" preference set "Refresh 多一点" --source telegram --state-file "$STATE"
```

For maintenance, use `send --dry-run` with explicit isolated state and journal paths
under a task directory. It produces synthetic IDs offline; report that as an
offline result. Keep production state/journals separate from the check. Existing
partial journal recovery and a new dry-run are different operations.
