# Personal Pulse

A personal briefing workflow for Hermes: collect permitted conversations, notes,
and existing project state; add verified external discovery; publish useful cards
and a linked feed with cover images. A persisted random 6–8 ceiling applies per
configured local day. Actual editions contain 1..ceiling qualified cards, with at
most two requiring user action.

## Installation

Agents: follow [INSTALL.md](INSTALL.md) for installation, verification, and cron setup.

1. Install this directory as `personal-pulse-generation` in your active Hermes
   profile. Back up an existing installation before replacing it. Resolve the
   absolute `skill_dir` through skill discovery and set `SKILL_DIR` to that path.
2. Install [cooperative-answering](https://github.com/Kuro96/cooperative-answering).
   For images, install `codex-gpt-image` or another image-generation skill that
   meets [the image contract](references/image-production.md). Make Python and
   the chosen skill's dependencies available in the selected environment.
3. Copy `config.example.json` to `config.local.json` beside `SKILL.md`. Keep the
   example generic and place deployment values in the local copy. Set private
   file permissions appropriate to your operating system and scheduler account.
4. Complete the configuration below, validate it, and review local rendering before
   authorizing live images, delivery, or scheduling. The example's null targets are
   intentional setup placeholders; configuration validation requires real targets.

```bash
: "${SKILL_DIR:?Set SKILL_DIR from installed skill discovery}"
export PULSE_CONFIG="${PULSE_CONFIG:-$SKILL_DIR/config.local.json}"
python3 -B "$SKILL_DIR/scripts/pulse_config.py" --config "$PULSE_CONFIG" check
```

`python3` bootstraps configuration discovery. Subsequent commands use
`paths.python_executable`; `references/native-publication.md` provides a complete
configuration-derived shell setup. Use absolute `SKILL_DIR` and `PULSE_CONFIG`
paths before switching working directories.

## Configuration

Configuration resolution is explicit `--config PATH` for the config CLI, then
`PULSE_CONFIG`, then the installed skill's `config.local.json`. Runtime processes
use the same `PULSE_CONFIG` or default file. The CLI is:

```text
scripts/pulse_config.py [--config PATH] check
scripts/pulse_config.py [--config PATH] show
```

`check` validates the configuration. `show` emits resolved configuration for local
agent consumption: its output contains private identity, paths, and destinations.
Keep that output in private task storage and give workers the minimum context
needed for their phase.

| Field | Set to |
|---|---|
| `version` | Configuration schema version `1` |
| `user.name` | Reader name used for personalization |
| `user.language` | Reader language, for example `zh-CN` |
| `user.timezone` | IANA timezone for day boundaries and scheduling |
| `delivery.cards_target` | Your confirmed card destination |
| `delivery.feed_target` | Your confirmed feed destination |
| `paths.state_file` | Durable preference and daily-ceiling state file |
| `paths.journal_dir` | Durable publication journals directory |
| `paths.scratch_dir` | Private scratch directory; runtime adds `hermes-pulse` |
| `paths.workdir` | Existing working directory for the scheduled process |
| `paths.image_executor` | Codex executor script, or `null` for another image skill |
| `paths.python_executable` | Python executable or executable path |
| `paths.env_file` | `null`, or the path to an explicitly selected private environment file |
| `sources.personal_context_dir` | `null`, or an authorized personal-context directory |
| `sources.notes_roots` | Array of authorized notes directories; empty is valid |
| `schedule.cron` | Five-field cron expression |
| `schedule.job_name` | Your scheduler job name |
| `token_env` | Name of the environment variable holding the Telegram bot token |

Targets follow `telegram:<supergroup-chat-id>:<topic-id>`; replace the entire
placeholder with your own confirmed destination. Cards and feed use distinct
positive topic IDs in the same supergroup. Empty example targets deliberately
require setup before sending. Discover and confirm identifiers in your own private
Telegram environment; keep them in the local configuration.

Paths support home/environment expansion. Prefer absolute or `~`-based paths so
processes resolve the same storage. Keep notes and personal context outside the
source tree; collectors read within the permissions granted for the run.

Authentication comes from the environment variable named by `token_env`. The JSON
stores the variable's name, not the token. By default `paths.env_file` is `null`
and the runtime uses the process environment. An explicitly selected private env
file is optional; configuration stores only its path. Supply secrets through your
private service environment or secret-management mechanism.

## Run and schedule

Read `SKILL.md` and the phase references. Every producer and publisher loads
`cooperative-answering`. Personal conversations and notes are regular primary
sources; external discovery supplements them. Selection compares previously
delivered information with new information or change and its present value to the
reader. A new synthesis or worked example can qualify even after work is done.

Use `references/native-publication.md` to load configured values, obtain the day's
persisted ceiling, validate schema 1.1, render details/feed, and send with a durable
journal. Publication defaults to actual verified cover images. Text fallback is
reserved for explicit opt-out or documented force-majeure conditions. Ambiguous
in-flight operations require operator reconciliation before another send.

Install `templates/daily_pulse_cron_prompt.md` as the job's execution instructions
using your scheduler's supported workflow. Configure the job from
`schedule.job_name`, `schedule.cron`, `user.timezone`, and `paths.workdir`. Ensure
its process receives the same `PULSE_CONFIG` and authentication environment. Both
normal and failure result routes use `delivery.feed_target`. Check the scheduler's
effective timezone and next occurrence; host cron may need explicit timezone
support or a converted schedule. Read back the exact job after deployment.

Configuration validation checks values, not live bot permissions or scheduler
activation. Verify those in the intended environment before enabling scheduled
publication. A completed matching journal produces `[SILENT]`; unresolved outcomes
produce a reader-safe `PULSE_SEND_FAILED` report.

## Source privacy and Git maintenance

`config.example.json` is the shareable template. `config.local.json`, configuration
output, personal context, real payloads, journals, generated images, and review
reports are private deployment data.

**Git ignore rules protect untracked files only.** If a private file is already
tracked, remove it from the index with a reviewed command such as
`git rm --cached -- config.local.json`, then commit the index change. Existing
history still contains earlier versions. Historical exposure needs separate
history cleanup and credential rotation, plus coordination for existing clones.
Current-tree review and archive checks do not establish remote-history audit status.

Maintain and distribute the source through this Git repository. Review the staged
file list and diff before each commit; keep deployment values in ignored files.
Install dependencies from their own repositories. Update an installed checkout
with `git pull --ff-only` after reviewing local changes, then run the offline tests
and compare the deployed job against the current cron template. See [INSTALL.md](INSTALL.md).

```bash
git -C "$SKILL_DIR" status --short
git -C "$SKILL_DIR" diff --cached
git -C "$SKILL_DIR" ls-files -- config.local.json .env reports state journals
```

The last command should list no private deployment files.

## Maintenance

Keep `SKILL.md`, references, canonical prompt, runtime contract, and authorized job
configuration aligned. Preserve historical journals and payload bindings for
resumption and deduplication. Offline checks use isolated state and journals;
live publication requires its own authorized run. See
`references/maintenance-and-portability.md` for verification and maintenance details.
