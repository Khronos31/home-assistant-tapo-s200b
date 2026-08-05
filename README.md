# Home Assistant Tapo S200B/S200D

A local-polling Home Assistant custom integration for TP-Link Tapo S200B smart
buttons and S200D smart remote dimmers connected to an H110/H110C hub.

The integration creates one Home Assistant **Event entity** for each supported
child control. Event collection and passive diagnostics are read-only. Two
maintenance buttons, reboot and unpair, are available only after an operator
explicitly enables their disabled-by-default entities.

> [!IMPORTANT]
> Standalone mode currently depends on the pre-release
> `plugp100==6.0.0.dev2` API. Treat version `0.1.x` as an early release and
> review the limitations below before using events for important automations.

## Supported hardware and events

| Hardware | Support |
| --- | --- |
| Tapo H110 / H110C | Hub connection |
| Tapo S200B | Single click, double click |
| Tapo S200D | Single click, double click, left/right rotation |

Event types are:

- `single_click`
- `double_click`
- `rotate_left`
- `rotate_right`

Rotation is normalized into 30-degree steps. For example, a raw `+120°` log
record produces four `rotate_right` Event-entity updates; `-60°` produces two
`rotate_left` updates. Rotation attributes include `degrees`, `source_degrees`,
`step_index`, and `step_count`.

## Diagnostics and maintenance

The following official-style entities are registered for each child when the
device reports support for them:

| Entity | Platform | Behavior |
| --- | --- | --- |
| Cloud connection | Binary sensor | Passive status |
| Signal level | Sensor | Passive status |
| Battery | Binary sensor | Passive low-battery status |
| Signal strength (RSSI) | Sensor | Passive status |
| Reboot | Button | Reboots the child when pressed |
| Unpair device | Button | Removes the child from the hub when pressed |

Every entity in this table is in the **Diagnostic** category and disabled by
default. No extra diagnostic polling occurs while they all remain disabled.
Enabling any one starts a separate low-frequency refresh; it does not change
the 1-second event polling interval.

> [!CAUTION]
> Pressing **Unpair device** removes the physical button from its hub. Recovery
> requires pairing it again through the Tapo app. Merely enabling the entity
> does not perform the action. After the disabled-by-default entity has been
> enabled, a `button.press` call performs the removal without a second
> confirmation; leave the entity disabled unless it is actively needed.

## Installation

### HACS custom repository

After this repository is published:

1. Open HACS and add
   `https://github.com/Khronos31/home-assistant-tapo-s200b` as a custom
   **Integration** repository.
2. Install **Tapo S200B/S200D**.
3. Restart Home Assistant.

### Manual

Copy `custom_components/tapo_s200b` to the same path under your Home Assistant
configuration directory, then restart Home Assistant.

## Configuration

1. Give the H110/H110C a stable DHCP lease.
2. In Home Assistant, open **Settings → Devices & services → Add integration**.
3. Search for **Tapo S200B/S200D**.
4. Enter the hub's RFC 1918 IPv4 address and the email/password for the Tapo
   account that owns it.

Each hub is a separate config entry, so multiple hubs are supported. If the
official **TP-Link Smart Home** integration already has a loaded config entry
for the same IP address, this integration reuses that exact python-kasa device
and KLAP session. Its raw event-log requests are serialized by python-kasa's
per-device lock, so existing temperature, humidity, motion, siren, and config
entities keep working. If no matching official entry exists, the integration
opens its own standalone plugp100 connection.

Other Tapo integrations managing different device addresses can remain
installed. Home Assistant normally merges the hub device card with other
integrations by MAC address, while this integration's button child cards remain
domain-specific. Only the official TP-Link integration is explicitly supported
for sharing the same H110 address; another independent KLAP client for that
exact hub may invalidate sessions.

You may disable the duplicate official **child device** after confirming that
the custom device has the entities you need. Keep the official hub config entry
loaded if this integration is sharing its session. Disabling the entire
official integration is different: reload this integration so it can reconnect
in standalone mode.

Credentials are stored in Home Assistant's config-entry storage, like other
UI-configured integrations. Standalone mode authenticates with them directly.
Shared mode uses the official integration's active authentication and retains
these credentials only as a fallback if standalone mode is used later. To avoid
invalidating the official integration's KLAP session, shared mode does not open
a second connection merely to validate the entered credentials; verify them
carefully before saving.
Diagnostics redact all config-entry data and hash device identifiers.

### Options

The integration defaults to a 1-second polling interval and 50 records per
page. Both can be changed from the integration's **Configure** dialog:

- Poll interval: 0.5–10 seconds
- Page size: 10–100 records

The default is recommended. A shorter interval increases traffic to the hub.
For low latency, the normal newest-page request is limited to 10 records. If
the saved cursor is not in those records, catch-up requests use the configured
page size and retain the previous recovery capacity.

## Automation example

An Event entity's state changes whenever a supported input is read. Filter on
its `event_type` attribute:

```yaml
automation:
  - alias: Tapo button single click
    triggers:
      - trigger: state
        entity_id: event.tapo_button_event
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.event_type == 'single_click' }}"
    actions:
      - action: light.toggle
        target:
          entity_id: light.example
```

For an S200D rotation automation, use `rotate_left` or `rotate_right`. One large
turn deliberately runs the automation once per 30-degree step. On tested H110
firmware, those steps become readable only after a continuous turn ends, so
they are then delivered as a batch.

## Delivery semantics and safety bounds

- The first poll records the newest position without replaying old history.
- Each child has its own persisted record cursor and recent event-ID cache.
- The cursor is saved **before** Event entities are updated. This is an
  at-most-once policy: a crash can lose an event, but a restart will not
  intentionally replay it.
- A persisted child event is published immediately; it does not wait for log
  reads from later children in the same hub poll.
- Once Event entities are registered, only enabled Event entities are polled.
  Disabling an unused Event entity reduces hub traffic without affecting the
  disabled-by-default diagnostic entities.
- Records are emitted oldest-first when several arrive between polls.
- Unknown or malformed records are advanced past and counted, but not guessed.
- A single raw rotation is limited to 720 degrees (24 steps).
- More than 64 generated events in one poll suppresses the complete batch while
  advancing the cursor, preventing accidental event storms.
- Polling is serialized per hub. Shared mode also uses the official TP-Link
  integration's own protocol lock, preventing multiple KLAP sessions from
  invalidating each other.

## Limitations

- This is polling, not a push protocol. Click latency is normally the configured
  poll interval plus the hub response time.
- On tested H110 firmware, the local `get_trigger_logs` API does not expose
  S200D rotation records while the dial is still turning. The hub timestamps
  individual steps throughout the gesture, but makes the records readable only
  after the turn ends. Reducing the poll interval does not provide live rotation;
  the resulting 30-degree events arrive together after release.
- Delivery is at-most-once, not guaranteed. Home Assistant shutdown, storage
  failure, hub history retention, network loss, or an event-storm safety bound
  can cause event loss.
- Only RFC 1918 IPv4 targets are accepted. Hostnames, public addresses, IPv6,
  loopback, and link-local targets are rejected.
- Only S200B/S200D event logs, passive diagnostics, and the two opt-in
  maintenance actions documented above are in scope. Other sensors and
  actuators are not exposed.
- Automatic network discovery is not implemented.
- Shared mode deliberately follows Home Assistant 2026.7.4's TP-Link runtime
  structure and python-kasa 0.10.2 raw-query API. A future core refactor may
  require a compatibility update.

## Troubleshooting

- Confirm that the IP belongs to an H110/H110C and is reachable from Home
  Assistant.
- Confirm the same credentials work in the Tapo app. Use the integration's
  reauthentication flow after changing the account password.
- If events are delayed or missing, keep the default polling options first and
  inspect the integration diagnostics and Home Assistant logs.
- Do not disable an official H110 entry without checking its child entities;
  temperature/humidity and motion automations may depend on it. This
  integration is designed to share that entry instead.
- If the official entry is disabled or enabled while this integration is
  already loaded, reload this integration once so it can select standalone or
  shared mode again.

## Development

The test environment targets Home Assistant 2026.7.4 and Python 3.14.2. CI also
upgrades a second test lane to the latest Home Assistant release so scheduled
runs expose compatibility regressions after new core releases.

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt -r requirements-test-plugin.txt
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/pytest -q
```

The `tools/tapo_readonly_probe.py` feasibility probe only permits RFC 1918
targets, requires a mode-`0600` credential file, blocks requests to other
hosts, and redacts raw event IDs. Do not commit credential files or probe
output. Redirect any retained output under the ignored `artifacts/` or
`tapo-probe-output/` directory rather than the repository root.

## License

The source code is licensed under GNU General Public License v3.0 only. See
[LICENSE](LICENSE). The PNG brand assets are not offered under that software
license; their source and trademark notice are documented in
[custom_components/tapo_s200b/brand/README.md](custom_components/tapo_s200b/brand/README.md).

---

## 日本語の要約

H110/H110C配下のS200B/S200Dを、イベントエンティティとしてHome Assistantへ
追加します。公式統合相当の診断6項目もすべて既定無効で登録されます。診断項目を
有効化するまで追加の診断ポーリングは行いません。再起動とペアリング解除は、該当する
ボタンエンティティを明示的に有効化して押した場合だけ実行されます。特にペアリング解除後は
Tapoアプリでの再登録が必要です。設定はHA UIからハブのプライベートIPv4アドレスと
Tapoアカウントを入力します。S200Dの回転は30度ごとに右回転・左回転イベントへ
分割されます。初回の履歴は再生せず、再起動後の重複発火を避けるat-most-once方式です。
ただし、実測したH110では回転ログが読み出せるのは連続回転の終了後であり、回転中の
30度イベントはリアルタイム発火せず、終了後にまとめて発火します。ポーリング間隔を
短くしてもこの挙動は変わりません。また、障害時のイベント欠落を完全には防げません。
