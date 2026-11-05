# Home Assistant automation

The optional companion integration adds native Home Assistant actions for the
Chromium desktop. It is separate from the add-on: installing an add-on does not
install code into Home Assistant Core.

## Setup

1. Use **Home Assistant 2026.10.4 or newer**, and update the **Voxl Chromium**
   add-on to version **0.2.0** or newer.
2. Generate a unique random token (for example with a password manager). Set
   `automation_token` in the add-on configuration to at least 32 characters, save,
   and restart the add-on. Leaving it empty disables the control API entirely.
3. Copy this repository's `custom_components/voxl_chromium` directory to
   `/config/custom_components/voxl_chromium` in Home Assistant, then restart
   Home Assistant. This is a custom integration, not an official Core integration.
4. In **Settings → Devices & services → Add integration**, search for
   **Voxl Chromium**. Enter the add-on's internal hostname, port **8100**, and
   the same token. Use the hostname shown in the add-on's information page,
   not the Home Assistant hostname, a LAN IP, or an Ingress URL.

The control connection stays inside Supervisor's private container network.
There is no host port to enable, no router forwarding, no Supervisor API grant,
and no dependency on a person's Ingress session. Do not expose port 8100 to the
LAN or internet. This integration expects Core and the add-on to be able to reach
one another on that private network.

Treat the token as a password with control of the shared signed-in browser.
The connection uses internal HTTP, not transport encryption. Rotate the token in
both the add-on and integration if it is disclosed. Do not paste it into scripts,
URLs, screenshots, issues or logs.

## Actions

Find these under **Developer tools → Actions** or in an automation/script's
**Perform action** picker. Select the Voxl Chromium configuration entry; scripts
use its `config_entry_id`.

| Action | Purpose |
| --- | --- |
| `voxl_chromium.open_url` | Open an HTTP(S) URL. `new_tab: true` is the default; false navigates the active tab. |
| `voxl_chromium.restore_window` | Restore and focus a minimized Chromium window. |
| `voxl_chromium.screenshot` | Capture the visible desktop and update the last-screenshot image entity; optionally save a PNG. |
| `voxl_chromium.record` | Record a short desktop clip to an approved MP4 filename. `seconds` is between 1 and 60. |
| `voxl_chromium.wait_for_screen` | Wait for visual idle, with `idle_ms`, `timeout_ms` and optional `[x, y, width, height]` region. Returns change/timeout information. |

URL opening does not evaluate JavaScript, run arbitrary shell commands or expose
Chromium's debugging port. URLs with embedded credentials and non-HTTP(S) schemes
are rejected. These actions share the same browser profile and desktop as every
Ingress viewer; they are not an isolated automation browser.

Screen waits observe pixels, not the page's DOM or network activity. An animated
page may never settle; a static error page can settle immediately. Use bounded
timeouts and inspect the result instead of assuming that visual idle means a
successful login or page load. Fixed desktop dimensions (`resize: false`) make
captures more predictable when viewers connect from different-sized screens.

## Example script

Replace `YOUR_CONFIG_ENTRY_ID` using the configuration-entry selector in the
Action editor. No token belongs in this script.

```yaml
alias: Capture browser dashboard
sequence:
  - action: voxl_chromium.open_url
    data:
      config_entry_id: YOUR_CONFIG_ENTRY_ID
      url: https://example.com/
      new_tab: false
  - action: voxl_chromium.wait_for_screen
    data:
      config_entry_id: YOUR_CONFIG_ENTRY_ID
      idle_ms: 300
      timeout_ms: 10000
    response_variable: screen
  - condition: template
    value_template: "{{ not screen.timed_out }}"
  - action: voxl_chromium.screenshot
    data:
      config_entry_id: YOUR_CONFIG_ENTRY_ID
```

The screenshot action updates an image entity without writing a public file.
Add that image entity to a dashboard using the entity picker. It represents the
most recently requested capture, not an automatically refreshing camera stream.

## Saving captures

Use a dedicated existing directory in Home Assistant's
`allowlist_external_dirs` for saved PNG/MP4 captures. Files are written by Core,
not into the add-on's private `/data/Downloads` directory. The integration refuses
all destinations inside `/config`, symlinks, and existing filenames. For example,
after creating `/media/voxl_captures`:

```yaml
homeassistant:
  allowlist_external_dirs:
    - /media/voxl_captures
```

Merge this into an existing `homeassistant:` section instead of duplicating it,
and apply the configuration as required by Home Assistant. Then a recording
action can use:

```yaml
action: voxl_chromium.record
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  seconds: 5
  filename: /media/voxl_captures/browser.mp4
```

Do not save private captures under `/config/www`: those files can be reachable
through `/local/` without Home Assistant authentication. Captures may contain
signed-in pages or personal information. Protect them, set a retention policy,
and remove them when no longer needed. They are not automatically deleted.

## Limits and troubleshooting

- If setup cannot connect, confirm the add-on is running, the token is configured,
  the internal hostname is correct, and both sides use port 8100. Never fix this
  by exposing Voxl's unauthenticated port or weakening the Ingress allowlist.
- After changing the token, update the integration credentials as well as the
  add-on configuration and restart the add-on.
- A recording saves Voxl's desktop video; it is not a full-page browser capture
  and does not include the locally drawn mouse cursor. Resizing can end a clip.
- Actions can interfere with a person using the same desktop. Serialize your
  browser scripts and avoid driving the browser while someone is entering data.
- Browser control and captures are privileged operations. Review which scripts,
  automations and other integrations can invoke them before exposing them to
  conversational assistants.
- Development tests and container smoke checks are not evidence of successful
  installation, discovery, backup/restore or upgrade on a live Supervisor host.
