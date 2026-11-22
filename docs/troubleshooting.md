# Troubleshooting

## First: turn on debug logging

```yaml
logger:
  logs:
    custom_components.came_thermo: debug
```

Restart or reload the integration, then check **Settings → System → Logs**. The
log shows the login exchanges, every poll, WebSocket connects and drops, and the
exact payload sent to the device.

## Setup errors

| Error | Meaning and fix |
| --- | --- |
| **Invalid username or password** | The CAME Connect account rejected the login. Check the account credentials. Accounts with MFA cannot be used — the integration logs in with the password grant, so use an account without MFA. |
| **Cannot reach CAME Connect** | Home Assistant could not reach `app.cameconnect.net` or `thermo.cameconnect.net`. Check DNS, outbound HTTPS and any firewall or Pi-hole in the path. Port **8443** is needed for live updates. |
| **No thermostat found on this account** | The account has no thermostat paired in the CAME app. Pair it there first, then retry. |
| **Enter the CAME Connect client ID and client secret** | The OAuth client values are missing. They have to be extracted from the official web app — see [getting started](getting-started.md#2-oauth-client-id-and-secret). |
| **This thermostat is already configured** | That keycode is already a config entry. Remove the old entry if you want to set it up again. |

## The integration stops authenticating after a while

CAME rotated the web app's OAuth client credentials (or your account password
changed). Extract the client ID and secret again from the web app and update
them under **Configure** — see
[getting started](getting-started.md#2-oauth-client-id-and-secret).

## The temperature does not update

- Live pushes use `wss://thermo.cameconnect.net:8443`. If that port is blocked,
  the integration still polls the cloud: every 120 s by default, adjustable
  under **Configure** (30–3600 s).
- `Online` turning off means the thermostat itself lost its cloud connection;
  check the device's Wi-Fi.
- With debug logging on, look for `WebSocket connected` / `WebSocket for … dropped`.
  A reconnect uses exponential backoff from 5 s up to 5 minutes, so it can take a
  moment after the network comes back.

## A change is ignored by the device

- `T1 <= T2 <= T3` is mandatory. Set the levels in order (or one step at a time)
  when the write does not stick.
- In `auto` mode the seasonal schedule owns the setpoint. The climate entity
  switches to `manual` when you set a temperature, but a direct write to the
  `Manual setpoint` number does not switch the mode — change the operating mode
  select too.
- Changing the season while a device programme (holiday, boost, jolly) is active
  may be overridden by the programme; select `manual` or `auto` afterwards.

## Entities are missing or duplicated

- Entity IDs are derived from the device name. Rename the device **once** in the
  integration and let Home Assistant migrate the IDs; renaming repeatedly leaves
  stale entities behind.
- If entities belong to a removed entry, delete the orphaned entities from the
  device page (**⋮ → Delete** on the entity, or remove the device).
- `COOL` is hidden when the thermostat has no summer configuration; that is
  expected on heating-only units.

## Start over

1. **Settings → Devices & Services → CAME Thermostat → Delete**.
2. Remove the device if it is still listed.
3. Add the integration again and re-enter the credentials and OAuth client.

Nothing is stored on CAME's side by this integration; removing the entry only
deletes the local config entry.
