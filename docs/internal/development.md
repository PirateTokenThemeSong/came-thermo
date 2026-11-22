# Internal: development

## Environment

```powershell
python -m venv .venv-ha
.\.venv-ha\Scripts\python -m pip install homeassistant pytest pytest-asyncio `
    pytest-homeassistant-custom-component ruff
```

`pytest.ini` puts `tests/` on the testpath and adds both `.` and
`custom_components` to `pythonpath`, so tests import the integration as
`custom_components.came_thermo`.

## Tests

```powershell
.\.venv-ha\Scripts\python -m pytest tests -q
```

38 tests, all offline: the cloud API and the WebSocket are stubbed.

- `tests/conftest.py` loads the `pytest_homeassistant_custom_component` plugin
  and works around two Windows issues:
  - the plugin's socket guard needs a socket pair even for network-free tests,
    so `pytest_socket.disable_socket` is neutralised for this suite;
  - HA's shared `aiohttp` session uses `AsyncResolver`, which refuses to run on
    the Windows proactor loop, so `async_get_clientsession` is monkeypatched to
    an inert session in the config flow and coordinator modules.
  - WebSocket stream start/stop is neutralised in the HA glue tests so no
    background task outlives a test. Merging itself is covered by
    `test_ws_push_merging`.
- `test_integration.py` drives the config flow, options flow and setup against
  the stubbed cloud.
- `test_climate.py` covers mode/season mapping, temperature writes and the
  auto-band path.
- `test_coordinator.py` covers polling, command read-back and push merging.

## Lint

```powershell
.\.venv-ha\Scripts\python -m ruff check custom_components tests tools --select F,E9
```

## Live checks

These hit the real account and are not part of CI. Set the credentials first
(PowerShell):

```powershell
$env:CAME_USERNAME = "you@example.com"
$env:CAME_PASSWORD = "..."
$env:CAME_CLIENT_ID = "..."
$env:CAME_CLIENT_SECRET = "..."
```

| Script | What it does |
| --- | --- |
| `tools/live_smoke.py [keycode]` | Login, device listing, state read, a write round-trip |
| `tools/ws_live.py [keycode]` | Connects the WebSocket and prints frames plus the merge result |
| `tools/came_probe.py …` | Manual CLI: `devices`, `state <k>`, `send <k> '{…}'`, `watch <k>` |

`live_smoke.py` and `ws_live.py` import `api.py` directly by file path so they
run without Home Assistant. `came_probe.py` adds `custom_components` to
`sys.path` and imports the package. Scratch dumps (`tools/.token.json`,
`tools/device.json`) are gitignored.

## Adding an entity

1. Read the field in `coordinator.state_for(keycode)`; add it to
   `docs/internal/api.md` → *State fields* if it is new.
2. Add the entity class to the matching platform, reuse the base class
   (`device_info`, dispatcher subscription, availability), and set a
   `unique_id` ending in the field name.
3. Writes go through `coordinator.async_send_command(keycode, payload)`, then
   `async_write_ha_state()`.
4. Add `translation_key` + strings to `strings.json` **and**
   `translations/en.json`.
5. Extend the entity table in `docs/entities.md`.

## Release / HACS

- `hacs.json`: `render_readme: true` (so the README is the HACS page — keep it
  short and user-facing), `zip_release: false`, minimum HA `2024.1.0`.
- Bump `version` in `custom_components/came_thermo/manifest.json` and tag the
  release. HACS shows the README and links to
  `manifest.json → documentation`.
- `manifest.json` declares `iot_class: cloud_polling`,
  `integration_type: hub`, and no `requirements` (stdlib + HA helpers only).
- Keep `docs/internal/api.md` in sync with any wire change: it is the reference
  the code comments point at.
