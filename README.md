# CAME Thermostat — Home Assistant custom integration

Unofficial Home Assistant integration for CAME Wi-Fi chronothermostats (TH/550,
TH/500, and siblings) through the CAME Connect cloud.

It reverse engineers the API used by the official CAME Connect web app
(`https://app.cameconnect.net`) and its thermostat backend
(`https://thermo.cameconnect.net`), then exposes the thermostat as a proper
`climate` entity plus the extra controls the device actually supports.

> Not affiliated with or endorsed by CAME. The API is private and can change at
> any time. Credentials are only sent to official `*.cameconnect.net` hosts.

## What you get

| Entity | Type | What it does |
| --- | --- | --- |
| `climate.<name>` | climate | HVAC mode, current temperature, target temperature, HVAC action, auto band (T1–T3) |
| Operating mode | select | Raw device mode: off, manual, auto, temp_man (timed manual), jolly, boost, holiday |
| Season | select | winter (heating), summer (cooling), antifreeze, off |
| Operating mode / Season | sensors | Same values as readable state |
| Active setpoint | sensor | Setpoint the device is currently working to |
| Antifreeze temperature | sensor + number | Frost protection setpoint |
| Manual setpoint | number | Temperature used in manual mode |
| Measured temperature offset | number | Calibration of the on-board sensor |
| Differential | number | On/off hysteresis of the relay algorithm |
| Winter / Summer levels | numbers | The three comfort levels (T1 frost-free, T2 comfort, T3 economy) per season |
| Relay active | binary sensor | Whether the relay is demanding heat/cool right now |
| Online | binary sensor | Cloud connectivity of the thermostat |

Live updates arrive over the thermostat WebSocket
(`wss://thermo.cameconnect.net:8443`), so temperature changes show up in Home
Assistant within a second or two; polling is only a safety net.

## Installation

### HACS (custom repository)

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add this repository, category **Integration**
3. Install **CAME Thermostat**, restart Home Assistant
4. **Settings → Devices & Services → Add integration → CAME Thermostat**

### Manual

Copy `custom_components/came_thermo` into your Home Assistant `config` folder
and restart.

## Configuration

The config flow asks for four values:

| Field | Where it comes from |
| --- | --- |
| **Username** | Your CAME Connect account, the same one you use in the app |
| **Password** | Same account password |
| **OAuth client ID** | Read from the official CAME Connect web app (see below) |
| **OAuth client secret** | Read from the same place |

The OAuth client ID and secret are **not** shipped with this integration — you
extract them from the official web app yourself, as described below. If the
account has more than one thermostat you pick which one to add.

Use **Configure** on the integration to change the fallback polling interval
(default 120 s) or update any of the credentials.

## 🔑 How to Obtain Client ID and Client Secret

CAME does not provide a public developer portal. Instead, we reuse the same
OAuth client credentials that the web app uses. You can extract them yourself:

1. Open [https://app.cameconnect.net](https://app.cameconnect.net) in a desktop
   browser but **don't** log in yet.
2. Open your browser's **Developer Tools → Network** tab.
3. **Log in** to the web application.
4. Look for a request to an endpoint like:

   ```
   https://auth.cameconnect.net/oauth/token
   ```

5. Select that request and look at the **Request Headers**.
6. Find the header:

   ```
   Authorization: Basic
   ```

7. Copy the `<long-base64-string>` part and decode it from Base64 (many online
   tools e.g. [https://www.base64decode.org](https://www.base64decode.org) or
   `base64 -d` in a terminal).
   The result will be:

   ```
   clientId:clientSecret
   ```

8. Use those two values when configuring the integration.

> ⚠️ **Note:** These values are not officially documented and may change if CAME
> rotates or replaces them. If the integration suddenly fails, repeat the steps
> above to extract new values.

## How the API works

Everything below was verified against a live TH/550.

### Authentication

Two calls against `https://app.cameconnect.net/api`, both with the OAuth client
credentials as HTTP Basic auth:

```
POST /oauth/auth-code?client_id=…&response_type=code&redirect_uri=https://app.cameconnect.net/role
                      &state=…&nonce=…&code_challenge=<S256>&code_challenge_method=S256
form: grant_type=authorization_code&username=…&password=…&client_id=…
-> {"code": "…", "scope": "", "state": "…"}

POST /oauth/token
form: grant_type=authorization_code&code=…&redirect_uri=…&client_id=…&code_verifier=…
-> {"access_token": "…", "expires_in": 7199, "scope": "", "token_type": "…"}
```

The PKCE verifier must match the challenge sent with the first call. The access
token is a JWT (`iss: Came_Connect`, `permissions: USER`) and is accepted by both
the main API and the thermostat API.

### Thermostat endpoints

Base: `https://thermo.cameconnect.net`, `Authorization: Bearer <token>`.

| Call | Purpose |
| --- | --- |
| `GET /v2/devices` | Groups with their thermostat items (keycode, name, model, device id) |
| `GET /v2/groups` | Same shape under `content.ownedGroups` / `content.sharedGroups` |
| `GET /v2/devices/{keycode}` / `GET /devices/{keycode}` | Full thermostat state |
| `POST /control/{keycode}/{keycode}` | State patch (see below) |
| `GET /api/v1/statistics/{keycode}?from=…&to=…&intervaltype=…` | Consumption log |
| `wss://thermo.cameconnect.net:8443/WebSocket/{keycode}/{keycode}` | Live pushes, subprotocols `["bearer", <token>]` |

The `POST /control/…` response is always `{"ok": true}`, even when the device
ignores part of the payload, so the integration re-reads the state instead of
trusting it.

### State fields

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
```

### Control payloads (all verified)

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

* `set_mode: "manual"` puts the device on the manual setpoint and makes
  `set_point_temp` track `manual_temp`; `auto` follows the season schedule.
* Changing `set_manual_temp` alone does **not** switch the device out of `auto`.
  The integration sends both when you change the target temperature in auto.
* `set_point_temp` reads back as the antifreeze temperature while nothing is
  demanded — that value is not a real setpoint, and the integration masks it.
* Seasonal schedule slots (`winter.day1..day7`, the 48-char strings) are accepted
  by the API but normalized back by the device firmware, so they are not exposed
  as entities.
* Weekly schedules, holidays and boost windows are configured per-group in the
  app (`/v2/groups`), outside the per-device state.

## Behaviour of the climate entity

* **HEAT** = winter season, running the manual setpoint
* **COOL** = summer season, running the manual setpoint
* **AUTO** = seasonal schedule; `target_temperature_low`/`high` map to T1/T3
* **OFF** = `set_mode: off` (season left untouched)
* `hvac_action` comes from `relay_status`, so you can automate on real demand
* When you set a target temperature while in AUTO, the entity switches to manual
  so the value actually takes effect

## Troubleshooting

Enable debug logging:

```yaml
logger:
  logs:
    custom_components.came_thermo: debug
```

* **invalid_auth** — check the account credentials; if the account uses MFA the
  password grant will not work, use an account without MFA.
* **cannot_connect** — Home Assistant must reach `app.cameconnect.net` and
  `thermo.cameconnect.net` (including port 8443 for live updates).
* **Temperature does not update** — the WebSocket may be blocked; polling still
  refreshes every 2 minutes.
* **Entities duplicated after renaming** — entity IDs follow the device name,
  rename the device once and let Home Assistant migrate the IDs.

## Development

```powershell
python -m venv .venv-ha
.\.venv-ha\Scripts\python -m pip install homeassistant pytest pytest-asyncio pytest-homeassistant-custom-component ruff

# unit + integration tests (cloud is stubbed)
.\.venv-ha\Scripts\python -m pytest tests -q
.\.venv-ha\Scripts\python -m ruff check custom_components tests tools --select F,E9

# live checks against the real account
$env:CAME_USERNAME = "..."; $env:CAME_PASSWORD = "..."
python tools\live_smoke.py          # login, devices, state, a write round-trip
python tools\ws_live.py             # WebSocket frames + merge logic
```

`tools/came_probe.py` is a small CLI for poking the API by hand (`devices`,
`state <keycode>`, `send <keycode> '{...}'`, `watch <keycode>`).

## License

MIT
