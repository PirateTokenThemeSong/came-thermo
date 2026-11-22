# Internal: CAME Connect API

Everything here was verified against a live TH/550 through the web app's own OAuth
client. This is the source of truth for the reverse engineered cloud API; the
implementation lives in [`custom_components/came_thermo/api.py`](../../custom_components/came_thermo/api.py).

> The API is private and undocumented. CAME can change any of it without notice.

## Authentication

Two calls against `https://app.cameconnect.net/api`, both with the OAuth client
credentials as HTTP Basic auth.

### 1. Auth code

```
POST /oauth/auth-code
  ?client_id=…
  &response_type=code
  &redirect_uri=https://app.cameconnect.net/role
  &state=…
  &nonce=…
  &code_challenge=<S256(verifier)>
  &code_challenge_method=S256

form: grant_type=authorization_code
      username=…
      password=…
      client_id=…

-> {"code": "…", "scope": "", "state": "…"}
```

### 2. Token

```
POST /oauth/token

form: grant_type=authorization_code
      code=…
      redirect_uri=https://app.cameconnect.net/role
      client_id=…
      code_verifier=…

-> {"access_token": "…", "expires_in": 7199, "scope": "", "token_type": "…"}
```

Notes:

- The client is registered for exactly `https://app.cameconnect.net/role` and
  performs a real PKCE exchange, so the verifier from step 1 must be redeemed in
  step 2. The implementation generates a fresh 48-byte verifier per login
  (`base64url(secrets.token_bytes(48))`, no padding) and
  `challenge = base64url(sha256(verifier))`.
- The access token is a JWT (`iss: Came_Connect`, `permissions: USER`) and is
  accepted by both the main API and the thermostat API.
- Tokens last ~2 hours. The implementation caches one and refreshes it 120 s
  before expiry; a `401` from the thermostat API forces one re-login and retries
  the request once.
- There is no hard-coded OAuth client. CAME publishes no developer portal, so the
  identity of their own web app has to be supplied by each user through the
  config flow. `DEFAULT_CLIENT_ID` / `DEFAULT_CLIENT_SECRET` in `api.py` are
  deliberately empty.

## Thermostat endpoints

Base: `https://thermo.cameconnect.net`, header `Authorization: Bearer <token>`.

| Call | Purpose |
| --- | --- |
| `GET /v2/devices` | Groups with their thermostat items (keycode, name, model, device id) |
| `GET /v2/groups` | Same shape under `content.ownedGroups` / `content.sharedGroups` |
| `GET /v2/devices/{keycode}` / `GET /devices/{keycode}` | Full thermostat state |
| `POST /control/{keycode}/{keycode}` | State patch (see below) |
| `GET /api/v1/statistics/{keycode}?from=…&to=…&intervaltype=…` | Consumption log |
| `wss://thermo.cameconnect.net:8443/WebSocket/{keycode}/{keycode}` | Live pushes |

### Device discovery

`GET /v2/devices` returns either a list of groups or, on older shapes, a
`{"content": {"ownedGroups": [...], "sharedGroups": [...]}}` object. Both are
handled: groups are flattened, items deduplicated by
`keycode` (falling back to `devcode`), and the display name/model/id are read
from the `cameconnect` sub-object with a fallback to the flat item fields.

### WebSocket

```
wss://thermo.cameconnect.net:8443/WebSocket/{keycode}/{keycode}
Sec-WebSocket-Protocol: bearer, <access_token>
```

- `aiohttp`'s `protocols=[...]` produces a handshake the server rejects with
  HTTP 502. Setting the `Sec-WebSocket-Protocol` header manually works; the
  server echoes the first token (`bearer`) back.
- Text frames carry partial state, for example `{"temp_loc": 20.8}` or
  `{"winter.T2": 21}`. The coordinator merges them into the cached state.
- Keepalive is 30 s, connect timeout 15 s, reconnect backoff 5 s → 300 s.

## State fields

```
temp_loc          20.8    measured room temperature (°C)
manual_temp       20.0    manual setpoint
set_point_temp    21.5    setpoint currently in force (equals antifreeze_temp when idle)
mode              auto    off | manual | auto | temp_man | jolly | boost | holiday
set_mode                  write-only alias used to change `mode`
current_season    winter  winter | summer | antifreeze | off
relay_status      0/1     relay demanding heat/cool
antifreeze_temp   3       frost protection setpoint
offset_temp       0       measured temperature calibration
algo              {t_diff: 0.2, type: "diff"}   hysteresis
winter / summer   {T1,T2,T3, day1..day7, jolly}  seasonal levels + schedule
on_line           0/1     cloud connectivity
SW_vers / HW_vers         firmware / hardware revision (also shown in the UI)
```

`POST /control/…` answers `{"ok": true}` even when the device ignores part of
the payload, so the integration re-reads the state instead of trusting the
response. It waits ~2 s before reading, which is what the cloud needs to publish
the new value.

## Control payloads (all verified)

| Goal | Payload to `POST /control/{keycode}/{keycode}` |
| --- | --- |
| Manual setpoint | `{"keycode": k, "set_manual_temp": 21.5}` |
| Mode | `{"keycode": k, "set_mode": "auto"}` |
| Season | `{"keycode": k, "current_season": "summer"}` |
| Frost protection | `{"keycode": k, "antifreeze_temp": 5}` |
| Sensor offset | `{"keycode": k, "offset_temp": -0.5}` |
| Hysteresis | `{"keycode": k, "set_algo": {"t_diff": 0.2, "type": "diff"}}` |
| Seasonal levels | `{"keycode": k, "winter": {"T1": 20, "T2": 21, "T3": 22}}` |

Notes:

- `set_mode: "manual"` puts the device on the manual setpoint and makes
  `set_point_temp` track `manual_temp`; `auto` follows the season schedule.
- Changing `set_manual_temp` alone does **not** switch the device out of `auto`.
  The integration sends both when the target temperature is changed in auto.
- `set_point_temp` reads back as the antifreeze temperature while nothing is
  demanded — that value is not a real setpoint, and the integration masks it.
- The device silently ignores comfort levels that are out of order, so writes
  are normalized to `T1 <= T2 <= T3` before sending.
- Seasonal schedule slots (`winter.day1..day7`, 48-char strings) are accepted by
  the API but normalized back by the firmware, so they are not exposed.
- Weekly schedules, holidays and boost windows are configured per group in the
  app (`/v2/groups`), outside the per-device state.
- The statistics endpoint is documented from probing but not used by the
  integration yet.

## Probing by hand

`tools/came_probe.py` wraps all of the above:

```powershell
$env:CAME_USERNAME = "you@example.com"
$env:CAME_PASSWORD = "..."
$env:CAME_CLIENT_ID = "..."
$env:CAME_CLIENT_SECRET = "..."

python tools\came_probe.py devices
python tools\came_probe.py state  <keycode>
python tools\came_probe.py send   <keycode> '{"set_mode": "manual"}'
python tools\came_probe.py watch  <keycode>
```
