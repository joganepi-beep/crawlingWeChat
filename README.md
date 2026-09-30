# wx-context

`wx-context` is a personal-use, local-only command-line tool for collecting a
bounded date range from explicitly allowlisted WeChat group sessions. It is a
small, read-only adapter around a separately installed `wechat-cli` executable;
it is not a WeChat client, a message sender, or a synchronization service.

The same workflow is available as one reusable Python facade,
`wx_context.WxContextTool`. The CLI is only a JSON wrapper around that class,
so Windows, macOS, and direct Python callers share the same allowlist, date
validation, storage, search, and media-selection behavior.

## Platform status

The `wx_context` package is portable Python and its local storage, collection,
search, and V2 media-decoding code support Windows, macOS, and Linux. End-to-end
history collection still requires a separately installed and configured
`wechat-cli` compatible with the locally installed WeChat desktop client.

macOS support is therefore **dependency- and environment-dependent**, not
verified by this repository's automated tests. A Mac setup must complete that
dependency's own key-extraction and Full Disk Access prerequisites before
running `collect`. Windows-specific local scripts, exported chats, downloaded
images, local keys, and virtual environments are deliberately excluded from
source control.

## Safety boundaries

- **Local-only and privacy-first:** this package does not upload collected
  messages or provide telemetry. The separately installed dependency is its own
  trust boundary and must be reviewed independently.
- **Deny by default:** no group can be collected or searched until its exact
  session ID is added to the local allowlist with `allow`.
- **Read-only dependency access:** the only dependency commands used are
  `wechat-cli sessions` and the bounded `wechat-cli history` command. This
  project does not send messages, monitor new messages, access contacts, open a
  database, inspect a WeChat process, or perform global search.
- **No automatic WeChat re-signing:** macOS signing, re-signing, entitlements,
  login, and dependency initialization are outside this project. If the
  separately installed dependency has a manual platform prerequisite, complete
  it yourself using its own trusted instructions; `wx-context` never modifies
  or re-signs the WeChat application.
- **No chat content in source control:** keep the data directory outside this
  repository and never commit `config.json`, collection directories, JSONL
  chunks, or copied chat content. The default data directory is per-user and
  is intentionally not the source tree.

The test suite uses fake subprocess runners and does not require a WeChat login,
an installed `wechat-cli`, or access to WeChat.

## Requirements and platform setup

Python 3.10 or newer is required. The `wechat-cli` dependency is not bundled,
installed, or initialized by this package; `WechatCli()` expects an executable
named `wechat-cli` on `PATH` unless the adapter is configured with another
executable by a caller.

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e .
```

macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

When `--data-dir` is omitted, the CLI uses `%LOCALAPPDATA%\wx-context` (or
`%APPDATA%\wx-context`) on Windows. On macOS and other Unix-like systems it
uses `$XDG_DATA_HOME/wx-context` when that variable is set, otherwise
`~/.local/share/wx-context`. Pass `--data-dir <DATA_DIR>` to choose another
location; quote paths containing spaces. Configuration writes are atomic and
use restrictive owner/account permissions where the platform supports them.

## Supported commands

All commands emit JSON. Put global options, such as `--data-dir`, before the
subcommand.

```text
wx-context --data-dir <DATA_DIR> groups
wx-context --data-dir <DATA_DIR> allow <SESSION_ID> --name "<DISPLAY_NAME>"
wx-context --data-dir <DATA_DIR> collect <SESSION_ID> --start <YYYY-MM-DD> --end <YYYY-MM-DD>
wx-context --data-dir <DATA_DIR> search "<QUERY>" --session-id <SESSION_ID> --limit <N>
wx-context --data-dir <DATA_DIR> context <COLLECTION_ID> --query "<QUERY>" --max-chunks <N>
```

Library usage:

```python
from wx_context import WxContextTool

tool = WxContextTool()
groups = tool.groups()
tool.allow_group("<SESSION_ID>", "<GROUP_NAME>")
manifest = tool.collect("<SESSION_ID>", "2026-09-01", "2026-09-29")
matches = tool.search("关键词", session_id="<SESSION_ID>")
context = tool.context(manifest.collection_id, "关键词")
```

For a local image path, `tool.preferred_media_path(path)` chooses a cached
original before falling back to a thumbnail: `_h.dat`, then the unsuffixed
`.dat`, then `_o.dat`, and finally `_t.dat`. It returns `None` when no variant
is present.

When `wechat-cli` is configured locally, media-enabled history reads also
cross-check each image against WeChat's local `message_resource.db`. This avoids
the dependency's month-wide sample-path behavior and maps each image message to
its own resource before original-first selection.

WeChat 4.x stores many images in V2 `.dat` containers. Use
`MediaDecoder(aes_key=..., xor_key=...).decrypt_v2(path)` to recover the local
JPEG/PNG/WXGF payload without modifying the source cache. The decoder handles
the required AES block alignment and PKCS#7 removal; WXGF payloads are HEVC and
need an installed `ffmpeg` for rendering. The decoder is pure Python and works
on Windows and macOS when the same local key material is available.

Typical flow:

1. `groups` lists group sessions returned by the dependency.
2. `allow` records one exact session ID and display name in the local,
   versioned allowlist. The display name can be omitted to resolve it from
   `groups`.
3. `collect` retrieves the inclusive date range for that allowlisted group and
   stores normalized, deduplicated JSONL chunks under the data directory.
4. `search` searches stored records only, and only while their collection's
   session remains allowlisted.
5. `context` returns bounded metadata pointers for matching chunks. It does not
   print the full collection text.

`send`, `watch`, contacts, process/database access, and unbounded history or
global-search commands are intentionally unsupported.

## Token-control and bounded storage

The package keeps context transfer and local reads bounded:

- each collection reads and stores at most the latest 100 records for its
  requested date range, keeping local storage bounded;
- published storage uses 200-message JSONL chunks;
- `search` defaults to 50 results and caps a request at 200;
- `context` defaults to two matching chunk pointers and caps a request at 200;
- `context` returns paths, counts, and timestamp spans rather than message
  bodies, so a caller can choose exactly what to read next.

Collections are written to a temporary directory, validated, and published by
one rename. A dependency, validation, or storage failure therefore leaves no
published collection (and temporary work is cleaned up).

## Verification without WeChat

From the repository root:

```text
py -m pytest -q
py -m compileall -q wx_context
git diff --check
```

These checks exercise the allowlist, redacted error handling, bounded adapter,
atomic collection publication, search/context limits, and platform path logic
without installing or contacting a real WeChat dependency.
