# Getting started

You need a working CAME Connect account (the one you use in the CAME Connect
app), the thermostat paired to it, and Home Assistant with access to the
internet.

Setup is three steps: install the integration, extract the OAuth client
credentials from CAME's web app, then add the integration.

## 1. Install

### HACS

1. HACS → Integrations → ⋮ → **Custom repositories**.
2. Add `https://github.com/PirateTokenThemeSong/came-thermo`, category
   **Integration**.
3. Install **CAME Thermostat** and restart Home Assistant.

### Manual

Copy `custom_components/came_thermo` into your Home Assistant `config` folder
(the result must be `config/custom_components/came_thermo/manifest.json`) and
restart.

## 2. OAuth client ID and secret

CAME has no public developer portal. The integration reuses the OAuth client
that the official web app uses, so those two values have to be taken from the
app itself. They are **not** shipped with this integration.

1. Open [https://app.cameconnect.net](https://app.cameconnect.net) in a desktop
   browser but **don't** log in yet.
2. Open the browser's **Developer Tools → Network** tab.
3. **Log in** to the web application.
4. Look for a request to:

   ```
   https://auth.cameconnect.net/oauth/token
   ```

5. Select it and look at the **Request Headers**.
6. Find the header:

   ```
   Authorization: Basic <long-base64-string>
   ```

7. Decode that Base64 string (for example with
   [base64decode.org](https://www.base64decode.org) or `base64 -d` in a
   terminal). The result has the shape:

   ```
   clientId:clientSecret
   ```

8. Keep both halves — they are the two values the integration asks for.

> ⚠️ These values are not officially documented and may change if CAME rotates
> them. If the integration suddenly stops authenticating, repeat these steps to
> read out the current ones.

## 3. Add the integration

1. **Settings → Devices & Services → Add integration → CAME Thermostat**.
2. Fill in:

   | Field | Value |
   | --- | --- |
   | **Username** | Your CAME Connect account, the same one you use in the app |
   | **Password** | Same account password |
   | **OAuth client ID** | From step 2 |
   | **OAuth client secret** | From step 2 |

3. If the account has more than one thermostat, pick the one to add. A single
   thermostat is added automatically.
4. The thermostat and its entities appear under the integration.

Repeat step 3 for every thermostat on the account — one config entry per device.

> Accounts with MFA enabled cannot be used: the integration logs in with the
> password grant, so use an account without MFA.

## Options

**Configure** on the integration opens the options:

- **Fallback polling interval** — seconds between cloud polls, default `120`,
  allowed `30`–`3600`. Live updates come over the WebSocket; polling only covers
  reconnects and missed pushes.
- **Username / password / client ID / client secret** — change the credentials.
  Leave the password empty to keep the current one. New credentials are verified
  against CAME before anything is saved, and the entry reloads afterwards.

## Next

- [Entities and usage](entities.md)
- [Troubleshooting](troubleshooting.md)
