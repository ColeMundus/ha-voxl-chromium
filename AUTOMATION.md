# Home Assistant automation

The optional companion integration adds native Home Assistant actions for the
Chromium desktop. It is separate from the add-on: installing an add-on does not
install code into Home Assistant Core.

## Setup with Marketplace or HACS

Requires **Home Assistant 2026.10.0 or newer** and the **Voxl Chromium add-on
0.3.0 or newer** for discovery-based setup.

1. Install the companion from `https://github.com/ColeMundus/ha-voxl-chromium`
   as an **Integration**, then restart Home Assistant:
   - **HA 2026.11 or newer:** use **Settings → Marketplace → Custom repositories**.
     Marketplace is built in; connect GitHub if prompted to add a custom repository.
   - **HA 2026.10:** [open this repository in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=ColeMundus&repository=ha-voxl-chromium&category=integration)
     or use **HACS → ⋮ → Custom repositories**.
2. In the add-on's **Configuration** tab, enable **Home Assistant actions**
   (`automation_enabled`), save, and restart the add-on.
3. Open **Settings → Devices & services** and confirm the discovered **Voxl
   Chromium** integration. The add-on supplies the hostname, port and a generated
   credential over Supervisor discovery; there is no token to copy.

The add-on and integration use the **same repository**; there is no second
GitHub repo to register. HACS has been replaced by native Marketplace on
HA 2026.11+, so do not install legacy HACS on those versions.

An add-on runs in its own container. A custom integration runs inside Home
Assistant Core. Installing an add-on cannot install a custom integration into
Core, so Marketplace/HACS installation is a separate one-time step. Discovery makes pairing
simple after the integration code is installed. No configuration-folder mount
or extra Supervisor/Core API permission is needed.

The control connection stays inside Supervisor's private container network.
There is no host port to enable, no router forwarding, and no dependency on a
person's Ingress session. Do not expose port 8100 to the LAN or internet.
The generated credential is persistent private add-on data; protect its backups.

### Existing manual installations

Existing configuration entries and nonempty `automation_token` options remain
supported. Installing through HACS updates the component files; it does not
require deleting your integration or image entity. Existing scripts continue
using the same configuration-entry ID.

### Manual fallback

Without HACS, copy `custom_components/voxl_chromium` from this repository into
`/config/custom_components/voxl_chromium`, then restart Core. You can still use
discovery afterward. For explicit manual pairing, set a unique random
`automation_token` of at least 32 non-space ASCII characters in the add-on,
restart it, and add **Voxl Chromium** through **Devices & services → Add
integration**. Enter the add-on's internal hostname, port **8100**, and that token.
Use the hostname on the add-on information page, not a LAN address or Ingress URL.
Keep credentials out of scripts, URLs, screenshots and logs.

## Actions

Find these under **Developer tools → Actions** or in an automation/script's
**Perform action** picker. Select the Voxl Chromium configuration entry; scripts
use its `config_entry_id`.

| Action | Purpose |
| --- | --- |
| `voxl_chromium.open_url` | Open an HTTP(S) URL. `new_tab: true` is the default; false navigates the active tab. |
| `voxl_chromium.restore_window` | Restore/focus Chromium, or reopen it with the existing profile if closed. |
| `voxl_chromium.click` | Click screenshot-pixel `x`, `y`; `button` is `left` (default), `right` or `middle`, and `click_count` is 1 (default) or 2. |
| `voxl_chromium.type_text` | Type literal Unicode `text` into the focused field, up to 1024 characters. Control characters and newlines are rejected; submit separately with `press_key`. |
| `voxl_chromium.press_key` | Send a named key or combination, such as `Enter`, `Escape`, `Tab`, `Ctrl+A` or `Ctrl+Shift+Tab`. |
| `voxl_chromium.scroll` | Scroll `direction` (`up`, `down`, `left`, `right`), with `steps` from 1 to 20 (default 1). Optional `x`, `y` must be supplied together; otherwise use the current pointer. |
| `voxl_chromium.screenshot` | Capture the visible desktop and update the last-screenshot image entity; optionally save a PNG. |
| `voxl_chromium.record` | Record a short desktop clip to an approved MP4 filename. `seconds` is between 1 and 60. |
| `voxl_chromium.wait_for_screen` | Wait for visual idle, with `idle_ms`, `timeout_ms` and optional `[x, y, width, height]` region. Returns change/timeout information. |

URL opening does not evaluate JavaScript, run arbitrary shell commands or expose
Chromium's debugging port. URLs with embedded credentials and non-HTTP(S) schemes
are rejected. These actions share the same browser profile and desktop as every
Ingress viewer; they are not an isolated automation browser.

Input actions require **both add-on and companion version 0.4.0 or newer**.
They focus Chromium and serialize with browser open/restore operations. Each
action is atomic relative to other API input actions, but an entire multi-action
script is not: avoid overlapping scripts or simultaneous human input.

Coordinates are absolute pixels in the **desktop screenshot**, including browser
chrome, not the scaled Ingress viewer or CSS page coordinates. Out-of-bounds
points are rejected against the current desktop dimensions. Resizing or restoring
a window may move targets; take another screenshot when the layout changes.

Key combinations accept unique `Ctrl`, `Alt`, and `Shift` modifiers plus one
letter, digit or named key: `Enter`, `Escape`, `Tab`, `Space`, `Backspace`,
`Delete`, `Home`, `End`, `PageUp`, `PageDown`, arrow keys, and `F1`–`F12`.
They do not leave keys held down or accept arbitrary xdotool commands.

Text goes to the subprocess on private stdin, not process arguments, and the
add-on does not log it. Home Assistant automation traces and screenshots can
still contain entered text; protect those records and avoid embedding secrets
in scripts. Input has the same powers as an interactive browser user, including
submitting forms in signed-in sessions. The calling automation/agent must enforce
confirmation for consequential actions; coordinate validation is not a sandbox.

Screen waits observe pixels, not the page's DOM or network activity. An animated
page may never settle; a static error page can settle immediately. Use bounded
timeouts and inspect the result instead of assuming that visual idle means a
successful login or page load. Fixed desktop dimensions (`resize: false`) make
captures more predictable when viewers connect from different-sized screens.

For a visual agent, use screenshot → input → bounded wait → screenshot → verify.
The screenshot action response contains byte counts, not an embedded model image:
the caller must retrieve the image entity's authenticated image bytes (or the
private API PNG) and attach them to its vision model. An image entity state alone
does not provide visual feedback. No agent/model is bundled with the add-on.

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

- If discovery is missing, complete HACS installation and the Core restart, then
  enable **Home Assistant actions** and restart the add-on. For manual setup,
  check the internal hostname, token and port 8100. Never fix this
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
- See [verification status](TESTING.md#verification-status) for the distinction
  between container/harness tests and checks on a live Supervisor host.
