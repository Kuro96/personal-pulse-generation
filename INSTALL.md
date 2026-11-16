# Agent installation guide

Install Personal Pulse into the recipient's active Hermes profile. This guide
covers source installation, private configuration, offline verification, and a
paused daily job. Live publication begins with the recipient's approval.

## 1. Check prerequisites and existing installation

- Hermes with skill loading, session search, files, terminal, web retrieval,
  vision, and preferably delegation. Scheduling uses `cronjob_manage` and a
  running Hermes scheduler/gateway.
- Linux or macOS; Windows users need WSL. The runtime uses POSIX `fcntl` locks.
- Python 3.10+ with an IANA timezone database. Pulse's Python helpers use the
  standard library; the selected image skill has its own dependencies.
- A Telegram bot already configured for the recipient, added to a supergroup
  with forum topics and permission to post text/photos. Confirm two distinct
  positive topic IDs in that same group: cards and feed.
- Permission to read the intended conversation history, personal context, and
  notes. Keep sensitive sources and generated editions in private storage.

Discover the active profile's actual skills directory. The default is often
`${HERMES_HOME:-$HOME/.hermes}/skills`; use the selected profile's path when it
is different. Check existing skills and cron jobs before installing. Back up an
existing installation and preserve its local config, preference state, and
publication journals. Update an existing matching job rather than creating a
second publisher.

## 2. Clone source and install skill dependencies

Set `SKILLS_DIR` to the verified active-profile directory:

```sh
: "${SKILLS_DIR:?Set the active profile skills directory}"
git clone https://github.com/Kuro96/personal-pulse-generation.git \
  "$SKILLS_DIR/personal-pulse-generation"
export SKILL_DIR="$SKILLS_DIR/personal-pulse-generation"
```

Install the required communication skill using its own instructions:

- [cooperative-answering](https://github.com/Kuro96/cooperative-answering)

For a fresh dependency installation:

```sh
git clone https://github.com/Kuro96/cooperative-answering.git \
  "$SKILLS_DIR/cooperative-answering"
```

For images, install `codex-gpt-image` if available in the recipient's skill
catalog, or choose another image-generation skill. Load its own setup instructions
and authenticate through its supported mechanism. The common requirements are in
[image-production.md](references/image-production.md): a complete privacy-safe
collage prompt, a local PNG, decoded dimensions, visual inspection, and crops from
that same collage. Record its exact installed skill name for step 5.

Verify the skills are discoverable through `skill_view` or `hermes skills list`.

## 3. Create private configuration

For a fresh installation, create the local file without overwriting an existing
configuration:

```sh
python3 - "$SKILL_DIR" <<'PY'
from pathlib import Path
import os
import sys
root = Path(sys.argv[1])
fd = os.open(root / 'config.local.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'wb') as handle:
    handle.write((root / 'config.example.json').read_bytes())
PY
export PULSE_CONFIG="$SKILL_DIR/config.local.json"
```

Edit the local configuration using the field reference in [README.md](README.md):

- Set the reader's name, language, and IANA timezone.
- Replace both null targets with confirmed `telegram:<supergroup-id>:<topic-id>`
  destinations; use distinct topics in the same group.
- Set durable state/journal paths outside the source tree. Preserve existing
  journals when migrating; they prevent duplicate publication.
- Set a private scratch directory, existing working directory, and available Python
  executable. Create the owned storage directories with private permissions.
- For `codex-gpt-image`, set `paths.image_executor` to its actual installed script.
  For a different image skill with its own invocation, set this field to `null`.
- Set authorized source roots; `personal_context_dir: null` and `notes_roots: []`
  are valid when those optional sources are unavailable.
- Set the daily cron expression and a job name unique in the profile.
- `token_env` stores the environment variable name. Supply its secret value through
  the private service environment or an explicitly selected `paths.env_file`.
  Confirm the scheduled process can access that environment/file. Keep credentials
  out of chat, Git, job prompts, and generated installation logs.

```sh
python3 -B "$SKILL_DIR/scripts/pulse_config.py" --config "$PULSE_CONFIG" check
```

`show` prints private identity, paths, and destinations. Consume it locally and
keep any saved output outside the public source tree. Subsequent runtime commands
use `paths.python_executable` and the same `PULSE_CONFIG` for every invocation.

## 4. Run offline checks

```sh
python3 -B -m unittest discover -s "$SKILL_DIR/scripts" -p 'test_*.py' -v
```

Tests create isolated configuration/state, exercise validation and rendering,
and cover offline publication journals and cron rendering. They use the standard
library and leave the recipient's production state untouched.

Follow [native-publication.md](references/native-publication.md) to validate and
render a sample edition in a private run directory. Check full detail/feed text,
links, cover metadata, and the actual selected count. For the chosen image skill,
run its documented configuration or dry-run check. Live image generation and a
Telegram smoke test require approval; read back the publication journal after an
approved send.

## 5. Render and install the cron job

Read the installed `cronjob_manage` schema and the
[Hermes cron documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron).
The source template is [cron_job.example.json](templates/cron_job.example.json);
its values are resolved by `scripts/pulse_cron.py`. The job loads the canonical
[daily prompt](templates/daily_pulse_cron_prompt.md) from the installed checkout
on every run.

Verify the scheduler's effective timezone first. The renderer accepts matching
scheduler/reader timezone names and rejects mismatches. Use the installed
scheduler's supported per-job timezone option when available, or an already
matching profile timezone. A timezone change affecting other jobs requires the
operator's decision. The renderer's `--scheduler-timezone` argument records your
verified input; it does not change or inspect the running scheduler.

Set `SCHEDULER_TIMEZONE` to that verified IANA name and `IMAGE_SKILL` to the chosen
installed skill. Save the rendered arguments in private scratch storage:

```sh
: "${SCHEDULER_TIMEZONE:?Verify the effective scheduler timezone}"
: "${IMAGE_SKILL:?Set the installed image-generation skill name}"
: "${TMPDIR:?Set the private managed scratch directory}"
umask 077
JOB_DIR="$(mktemp -d "$TMPDIR/pulse-install-XXXXXX")"
python3 -B "$SKILL_DIR/scripts/pulse_cron.py" \
  --config "$PULSE_CONFIG" \
  --scheduler-timezone "$SCHEDULER_TIMEZONE" \
  --image-skill "$IMAGE_SKILL" > "$JOB_DIR/cron.local.json"
```

The generated JSON contains private paths and destinations. It is a tool-argument
object, not the scheduler's internal storage format.

Agent deployment procedure:

1. Call `cronjob_manage(action="list")`; locate any existing Pulse job for this
   deployment and inspect its complete configuration.
2. For a new job, load the generated JSON and pass it as the arguments to
   `cronjob_manage`. It creates the job **paused**. The job follows the profile's
   model selection and attaches the chosen image skill.
3. If using a per-job timezone, apply the installed scheduler's supported timezone
   option while the job remains paused. This renderer emits the portable tool
   fields and leaves version-specific timezone options to the installer.
4. For an existing job, use `action="update"` with its discovered `job_id`. Pass
   the rendered name, schedule, prompt, skills, toolsets, workdir, delivery routes,
   `no_agent`, `continuity`, and `attach_to_session`. Omit create-only `paused` and
   `paused_reason`; preserve the operator's intended enabled/paused and model-pin
   state. Pause through the supported action if installation requires it.
5. Read back the exact job. Check attached skills, tool availability, absolute
   skill/config paths, workdir, effective timezone, schedule and next occurrence,
   and both `deliver` and `failure_deliver` against `delivery.feed_target`.
6. Confirm bot permissions, image setup, and offline checks. With the recipient's
   scheduling approval, resume the new job, then read it back again. An immediate
   `run` is a separate live-publication action, including while the job is paused.

The Pulse runtime owns card/feed publication and journals. The scheduler owns the
final run-status response. Completed matching journals produce `[SILENT]`;
failures produce `PULSE_SEND_FAILED` with a concrete reader-safe reason.

## 6. Installation acceptance and updates

Report the installed revision, dependency skill names, config-check result,
offline-test result, exact job ID, enabled/paused state, effective timezone, and
next occurrence. Report a live publication only after its matching journal is
complete. Remove task-owned scratch installation arguments when finished.

Use `git status` before updates and `git pull --ff-only` for a clean checkout.
Run tests again and compare the deployed job with a fresh rendering when scheduler
materials change. Retain ignored local configuration, durable preferences and
journals. Review the staged tree for private material before publishing changes.
