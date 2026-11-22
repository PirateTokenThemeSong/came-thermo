# AGENTS.md

## What this is

`came-thermo` — unofficial Home Assistant integration for CAME Wi-Fi
chronothermostats (TH/550, TH/500 and siblings) through the CAME Connect cloud.
Pure Python on top of Home Assistant helpers (no extra `requirements`), speaking
the private API of the official web app, reverse engineered by hand.

One config entry per thermostat. Live state arrives over the thermostat
WebSocket; polling is only a safety net.

## Layout

- `custom_components/came_thermo/api.py` — OAuth (PKCE) + thermostat REST client
  and the WebSocket URL. All wire knowledge lives here or in
  [`docs/internal/api.md`](docs/internal/api.md).
- `custom_components/came_thermo/coordinator.py` — polling, one WS listener per
  online device, push merging, dispatcher signal `came_thermo_update_<keycode>`.
- `custom_components/came_thermo/{climate,select,sensor,number,binary_sensor}.py`
  — entities. Only `climate.py` interprets the device; the rest expose raw
  fields. Shared base pattern: `has_entity_name`, `unique_id =
  came_thermo_<keycode>[_suffix]`, dispatcher subscription, writes through the
  coordinator.
- `custom_components/came_thermo/config_flow.py` — user/device steps and the
  options flow (poll interval + credentials).
- `custom_components/came_thermo/const.py` — domain, platforms, modes, seasons,
  temperature limits.
- `tests/` — offline pytest suite (`pytest-homeassistant-custom-component`).
- `tools/` — reverse-engineering CLI and live checks; `make_logo.py` generates
  the brand assets from the CAME app icon.
- `assets/` + `custom_components/came_thermo/brand/` — logo exports.
- `docs/` — user manual; `docs/internal/` — contributor docs.

## Build & test

```powershell
python -m venv .venv-ha
.\.venv-ha\Scripts\python -m pip install homeassistant pytest pytest-asyncio `
    pytest-homeassistant-custom-component ruff

.\.venv-ha\Scripts\python -m pytest tests -q
.\.venv-ha\Scripts\python -m ruff check custom_components tests tools --select F,E9
```

Tests never touch the network; the cloud and the WebSocket are stubbed.
`tests/conftest.py` works around two Windows quirks (pytest-socket guard, HA's
aiodns session on the proactor loop) — do not remove those patches.

Live checks against the real account are manual and need `CAME_USERNAME`,
`CAME_PASSWORD`, `CAME_CLIENT_ID`, `CAME_CLIENT_SECRET`:

- `python tools/live_smoke.py` — login, devices, state, write round-trip.
- `python tools/ws_live.py <keycode>` — WS frames + merge logic.
- `python tools/came_probe.py devices|state|send|watch`.

`tools/make_logo.py` needs `opencv-python`, `numpy` and `Pillow` (not in the dev
venv above unless you regenerate the logo).

## Conventions

- **Credentials are user-supplied.** The OAuth client ID/secret are never
  hard-coded and never shipped; `DEFAULT_CLIENT_*` in `api.py` stay empty. The
  config flow must fail with `missing_client` rather than guess.
- **Never commit secrets or dumps:** CAME credentials, `tools/.token.json`,
  `tools/device.json`, `original.webp` (logo source cache). Keep the repo
  buildable from a clean checkout.
- **Trust the read-back.** `POST /control/…` returns `{"ok": true}` even when the
  device ignores the payload, so after a write the device is re-read.
- **Device quirks to preserve:** `set_point_temp` mirrors `antifreeze_temp` when
  idle (mask it); `set_manual_temp` alone does not leave `auto` (send
  `set_mode: manual` too); levels must satisfy `T1 <= T2 <= T3`
  (`normalize_levels`); `aiohttp` `protocols=` breaks the WS handshake (set the
  `Sec-WebSocket-Protocol` header manually).
- **Manifest:** `iot_class: cloud_polling`, `integration_type: hub`, no
  `requirements`. Bump `version` on release.
- **i18n:** any new entity or config error needs a `translation_key` plus entries
  in both `strings.json` and `translations/en.json`.
- **API changes:** update `docs/internal/api.md` in the same change; it is the
  reference the code comments point at.

## Documentation (keep it split)

- `README.md` is a landing page only: what it is, what you get, a three-step
  pointer to getting started, the docs index, status/disclaimer. No
  instructions, no build commands, no API details, no entity tables.
- `docs/` is the user manual — everything a user might want:
  - `getting-started.md` — install, OAuth client extraction, first setup, options
  - `entities.md` — entity reference, climate behaviour, automation examples
  - `troubleshooting.md` — setup errors, auth rotation, updates, ghost entities
- `docs/internal/` is for contributors:
  - `architecture.md` — layout, runtime flow, coordinator, entity patterns
  - `api.md` — the reverse engineered cloud API (wire source of truth)
  - `development.md` — env, tests, tools, adding entities, release
- Update the matching doc whenever behaviour changes; do not grow the README.
  New pages go into the README index and the lists above.

## Workflow (required)

After **every** change:

1. run tests and lint,
2. commit with a short imperative message,
3. push to `origin master`.

Do not batch unrelated work; do not leave the tree dirty when you stop.

This repo is configured locally for the `PirateTokenThemeSong` account:
`user.name`, `user.email`, the token store for `origin`, and
`commit.gpgsign=false`. Signing is off on purpose — that account has no signing
key registered and the global GPG key belongs to a different identity, so do not
re-enable global signing here.
