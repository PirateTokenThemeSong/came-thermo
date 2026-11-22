# Entities and usage

Every thermostat becomes one Home Assistant device with a `climate` entity and
a set of companion entities. All of them are updated live from the cloud
WebSocket, and every writable one sends the change straight to the device.

## Entities

Entity IDs follow the device name (for example `climate.casa`). Rename the
device once and let Home Assistant migrate the IDs.

### Climate

| Entity | Type |
| --- | --- |
| `climate.<name>` | climate |

Supported features: target temperature, turn on, turn off. The current
temperature is the room temperature measured by the thermostat; the target is
whatever setpoint is currently in force.

### Selects

| Entity | Values |
| --- | --- |
| Operating mode | `off`, `manual`, `auto`, `temp_man`, `jolly`, `boost`, `holiday` |
| Season | `winter`, `summer`, `antifreeze`, `off` |

- **Operating mode** is the raw device mode. `manual` runs the manual setpoint,
  `auto` follows the seasonal schedule, `temp_man` is a timed manual run,
  `jolly` / `boost` / `holiday` are the device's own programmes.
- **Season** selects heating (`winter`), cooling (`summer`), frost protection
  only (`antifreeze`) or everything off (`off`).

### Sensors

| Entity | Unit | Meaning |
| --- | --- | --- |
| Operating mode | — | Same value as the operating mode select |
| Season | — | Same value as the season select |
| Active setpoint | °C | Setpoint the device is working to right now |
| Antifreeze temperature | °C | Frost protection setpoint currently stored |
| Frost-free level | °C | T1 of the **active** season |
| Comfort level | °C | T2 of the active season |
| Economy level | °C | T3 of the active season |

> `Active setpoint` reads back as the antifreeze temperature while the device is
> idle. That is a quirk of the firmware, not a real setpoint; use it for history
> and dashboards, not for control decisions.

### Numbers

| Entity | Range | Meaning |
| --- | --- | --- |
| Manual setpoint | 3–35 °C | Temperature used in manual / timed-manual mode |
| Antifreeze setpoint | 3–35 °C | Frost protection temperature |
| Measured temperature offset | −5–5 °C | Calibration of the on-board sensor |
| Differential | 0.1–3 °C | On/off hysteresis of the relay algorithm |
| Winter frost-free / comfort / economy | 3–35 °C | Winter levels T1 / T2 / T3 |
| Summer frost-free / comfort / economy | 3–35 °C | Summer levels T1 / T2 / T3 |

The device requires `T1 <= T2 <= T3`; if a write would break that order, the
neighbouring level is pushed along with it so the set is accepted instead of
being silently ignored.

### Binary sensors

| Entity | Meaning |
| --- | --- |
| Relay active | The thermostat is demanding heat or cooling right now |
| Online | The thermostat is connected to the CAME cloud |

## How the climate entity behaves

| HVAC mode | What happens on the device |
| --- | --- |
| **HEAT** | `winter` season + `manual` mode, running the manual setpoint |
| **COOL** | `summer` season + `manual` mode, running the manual setpoint |
| **AUTO** | `auto` mode; the seasonal schedule picks the setpoint, `target_temperature_low` / `high` are T1 / T3 |
| **OFF** | `set_mode: off` — the season is left untouched |

- `hvac_action` comes from the relay, so it reports `heating`, `cooling`, `idle`
  or `off` based on real demand.
- `COOL` is only offered when the thermostat reports a summer configuration.
- Setting a target temperature while in **AUTO** (or any device programme)
  switches the device to `manual`, otherwise the new value would be ignored.
- Changing the auto band (low/high) writes the seasonal levels T1/T3 instead of
  the manual setpoint.
- Turning the entity off does not change the season; turning it on goes back to
  **AUTO**. Pick the season explicitly when you want heating or cooling.

## Examples

### Back to the schedule every night

```yaml
automation:
  - alias: Thermostat back to schedule
    triggers:
      - trigger: time
        at: "23:00:00"
    actions:
      - action: climate.set_hvac_mode
        target:
          entity_id: climate.casa
        data:
          hvac_mode: auto
```

### Notify when the relay has been on for too long

```yaml
automation:
  - alias: Thermostat demanding heat for 2 hours
    triggers:
      - trigger: state
        entity_id: binary_sensor.casa_relay_active
        to: "on"
        for: "02:00:00"
    actions:
      - action: notify.persistent_notification
        data:
          title: CAME thermostat
          message: The relay has been active for two hours.
```

### Drop the setpoint when a window opens

```yaml
automation:
  - alias: Thermostat window open
    triggers:
      - trigger: state
        entity_id: binary_sensor.sala_window
        to: "on"
        for: "00:05:00"
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.casa
        data:
          temperature: 12
```

This switches the device to manual. Re-select `auto` when the window closes if
you want the seasonal schedule back.

### Frost protection when away

```yaml
scripts:
  away_mode:
    sequence:
      - action: select.select_option
        target:
          entity_id: select.casa_season
        data:
          option: antifreeze
```

### Dashboard card

```yaml
type: thermostat
entity: climate.casa
name: Casa
```

For history, graph `sensor.casa_active_setpoint` against the climate entity's
`current_temperature` to see the setpoint next to the room temperature, and add
`binary_sensor.casa_relay_active` to see when the device was actually heating.

## Not exposed as entities

The device also stores weekly schedules, holiday ranges and boost windows.
Those live per-group in the CAME app and are normalized by the firmware when
written, so they are read-only from the cloud and not surfaced here.
