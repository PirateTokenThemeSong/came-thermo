# Internal: architecture

## Shape

```
came-thermo-ha/
  custom_components/came_thermo/
    __init__.py        config entry setup/unload, platform forwarding
    api.py             async cloud client: OAuth + thermostat REST + WS URL
    coordinator.py     polling, WebSocket listeners, push merging
    config_flow.py     user + device steps, options flow
    const.py           domain, platforms, modes, seasons, temperature limits
    helpers.py         as_float(), level normalization
    climate.py         the climate entity
    select.py          operating mode, season
    sensor.py          readable state + active-season levels
    number.py          setpoints, calibration, hysteresis, season levels
    binary_sensor.py   relay demand, cloud connectivity
    strings.json       source strings for the UI
    translations/      generated JSON translations
    manifest.json      domain, version, codeowners
  tests/               pytest + pytest-homeassistant-custom-component
  tools/               reverse-engineering and live-check scripts
  assets/              integration icon
  docs/                user docs; docs/internal/ for this
```

One config entry per thermostat. Everything hangs off the entry id in
`hass.data[DOMAIN][entry.entry_id]`.

## Runtime flow

1. `async_setup_entry` builds a `CameThermoCoordinator` from the entry
   credentials and the options' poll interval.
2. `async_config_entry_first_refresh()` logs in, lists the devices and reads the
   state of every thermostat on the account.
3. The platforms are forwarded (`binary_sensor`, `climate`, `number`, `select`,
   `sensor`). Each platform adds one entity set per keycode in
   `coordinator.data`.
4. `coordinator.async_start_streams()` opens one WebSocket listener per online
   thermostat.
5. Every entity subscribes to `device_signal(keycode)` and re-writes its state
   when a push arrives.
6. Unloading cancels the listeners, unloads the platforms and drops the
   coordinator.

Options changes trigger `async_reload` through the entry update listener.

## Coordinator

`CameThermoCoordinator` is a `DataUpdateCoordinator[dict[str, dict]]` keyed by
keycode.

- **Poll** (`_async_update_data`): `GET /v2/devices`, then `GET /devices/{k}`
  for each device, all inside a 30 s timeout. A device that fails to read is
  skipped, not fatal. After a successful poll the listeners are reconciled, so a
  thermostat that came back online gets a stream and one that went offline loses
  it.
- **Command** (`async_send_command`): `POST /control/…`, wait 2 s, then re-read
  that one device and publish it. The cloud acknowledges patches before they are
  applied, so the read-back is the actual source of truth.
- **WebSocket** (`_async_ws_loop`): one background task per keycode, guarded by
  `_ws_tasks`. Reconnects with exponential backoff (5 s → 300 s, reset to 5 s on
  success), 30 s heartbeat. Cancellation is cooperative through `_closing` and
  `task.cancel()`.
- **Push merge** (`_async_apply_push`): dotted keys (`winter.T2`) are merged into
  the matching sub-dict, flat keys replace the top-level value. A changed push
  stamps `updatedAt`, calls `async_set_updated_data` and fires the dispatcher
  signal. Unchanged frames are dropped.

State is mutated in place inside the cached dict and the dispatcher is the
notification path — entities never await the WebSocket.

## API client

`CameThermoAuth` owns the OAuth dance and the token cache (see
[the API notes](api.md#authentication)). `CameThermoApi._request` resolves a
token, sends the request, and retries once with a forced re-login on `401`.
Errors are split in two types: `CameAuthError` (bad credentials → config flow
shows `invalid_auth`) and `CameApiError` (everything else → `cannot_connect` /
`UpdateFailed`).

The OAuth client ID and secret come from the entry data first, then options.
They are never defaulted in code.

## Entity patterns

Every platform uses the same base shape:

- `_attr_has_entity_name = True`, so the entity ID is `<device>_<entity>` and
  the device is the thermostat.
- `unique_id = f"{DOMAIN}_{keycode}[_suffix]"`.
- `device_info` = `{(DOMAIN, keycode), MANUFACTURER, model, name, sw_version,
  hw_version}`; model/name come from the `_metadata` injected by discovery.
- `available` is `coordinator.last_update_success and bool(state)`.
- Writes go through `coordinator.async_send_command` and then write the HA state.

Only the climate entity interprets the device; the companion entities expose raw
fields. That keeps mapping quirks (`set_point_temp` masking, seasonal levels,
mode → HVAC) in one file.

### Climate mapping

- Season `off` / `antifreeze` → `HVACMode.OFF`, regardless of `mode`.
- `mode == off` → `HVACMode.OFF`; `mode == auto` → `HVACMode.AUTO`.
- Any other active mode → `HEAT`, or `COOL` when `current_season == summer`.
- `COOL` is only advertised when a `summer` config object exists.
- `hvac_action` is derived from `relay_status`.
- Setting a temperature in a schedule/programme mode also sends
  `set_mode: manual`; setting the auto band writes T1/T3 of the active season.
- Target temperature prefers `manual_temp` in manual modes, otherwise
  `set_point_temp` when it is above the minimum temperature.

## Config flow

- `async_step_user`: username, password, client ID, client secret. A real login
  and device listing is required before continuing, so errors surface in the
  form (`invalid_auth`, `cannot_connect`, `missing_client`, `no_devices`).
- `async_step_device`: shown only when the account has more than one thermostat.
- Unique ID is the keycode, so the same device cannot be added twice.
- `CameThermoOptionsFlow`: poll interval and credential changes. New credentials
  are validated against the cloud before anything is written to the entry; the
  password field is optional and keeps the stored value when empty.
- UI text lives in `strings.json` and `translations/en.json`. The config flow
  description points users at `docs/getting-started.md` for the OAuth values.

## Conventions

- Standard library + Home Assistant helpers only; no extra `requirements` in
  `manifest.json`.
- Celsius everywhere; values round to one decimal (`as_float`) and levels are
  validated by `normalize_levels`.
- `iot_class: cloud_polling` in the manifest even though live updates come over
  a WebSocket.
- Debug logs are meant to be usable: login, poll, WS lifecycle and outgoing
  payloads are all logged at debug level.
