# EVE Pressure Overlay

A minimal, click-through combat HUD for EVE Online on Windows. It reads the combat logs EVE writes on your PC and shows how well you're applying damage, how hard you're neuting your targets, and who's hurting you.

![The overlay showing every meter with sample data](docs/screenshot.png)

## Install

1. Download **[EVE-Pressure.zip](https://github.com/aminomancer/eve-pressure-overlay/releases/latest/download/EVE-Pressure.zip)** from the [latest release](https://github.com/aminomancer/eve-pressure-overlay/releases/latest). Right-click it, choose **Properties**, tick **Unblock**, then extract it wherever you like.
2. Double-click **EVE Pressure.cmd**. The first start adds EVE Pressure to the Start menu, so you can start it from there afterwards.

Nothing gets installed. To remove it, delete the folder and its Start menu entry.

## Set up EVE

- Play in **Windowed** or **Fixed Window** mode.
- Play in English. The overlay works by reading EVE's log messages, which change with the game's language, and I can't test separate logic for every language. In **Log and Messages** settings, make sure combat messages include the weapon and hit quality.
- For per-pilot rows, enable **Pilot Name** and **Ship Type** under **Ships** in your Overview Settings. EVE writes these labels into the combat log.

## Use it

The overlay shows over the focused EVE window, and a row only appears while it has something to show. Click the tray icon for **Move overlay**, **Settings** and **Quit**.

In Move mode you can drag the overlay, and every meter shows sample data so you can see its full size. Lock it again with **Move overlay** or **Ctrl+Alt+F10** (**Ctrl+Shift+F10** if that's taken). A locked overlay passes clicks through to EVE; hover a row's icon for details.

In **Settings** you can pick which character to follow, which side the meters fill from, and more. **Simulate a fight for testing** plays a fake fight on a loop, so you can watch the meters without undocking.

## The meters

| Meter | What it shows |
| --- | --- |
| Turret hit quality | Your last ten shots on the target, as a percentage of a normal hit. Around 100% means you're tracking well. Misses count as 0%, wrecking shots as 300%. |
| Missile hit quality | Your latest volley vs. your best volley of the same missile on the same target, which is assumed to be a good proxy for full application. The row dims when your volleys stop arriving, for example when the target is out of missile range (but also when you just stop firing - though the meter eventually disappears). |
| Drone hit quality | Like turret hit quality, for your drones' recent hits on their target. |
| Breacher pods | The targets your pods are ticking on, with their damage per second. |
| Outgoing neuts | For each target: how much your latest pulse neutralized and your total GJ/s. See below for the colored states. |
| Incoming DPS | Damage you're taking and the three biggest sources, with drone damage counted for their owner. |
| Incoming neuts | Capacitor you're losing to neuts and nos, and the three biggest sources. |

The incoming totals turn amber, then red, at levels that scale with your ship class. That needs a [linked character](#link-a-character-optional); otherwise cruiser levels apply.

If you shoot two targets at once, the turret or missile meter shows twice, labelled with each target's ship.

### Neut states

Each pulse is compared with that module's normal pulse on the target:

- **dry** (green): your last pulse took nothing.
- **pinned** (blue): they're nearly empty. A pulse took a fifth of normal or less, or under half while they were already low.
- **recovered** (amber): after being dry or pinned, a pulse took three quarters or more. They have cap again, so neut hard. It stays up for about two cycles.

A pulse that's only somewhat weaker usually means falloff and doesn't change the state. The state clears when your neuts stop cycling on that target.

## Link a character (optional)

Everything works without this. Linking a character through EVE's official login lets the overlay read that pilot's location and current ship, which it uses to:

- scale the warning colors to your ship class,
- hide as soon as you dock,
- reset the hit meters when you dock or change ships.

To link, open **Settings**, click **Link character…** and log in to EVE in the browser that opens. If it fails, make sure nothing else is using port 8765. The login is stored encrypted for your Windows account. Unlink in **Settings**, or revoke access on [EVE's third-party applications page](https://community.eveonline.com/support/third-party-applications/).

## AI use

EVE Pressure was built with the help of an AI coding assistant (Anthropic's Claude). It wasn't vibe-coded: I designed how every meter behaves, steered the implementation, and tested it in real fights. I'm mentioning it so you know how it was made.

## Development

Run from source with [Python](https://www.python.org/downloads/windows/) 3.11+ (with Tcl/Tk) and [Pillow](https://python-pillow.org/): `python -m pip install pillow`, then `python -m pressure_overlay`, or double-click [Start Overlay.pyw](Start%20Overlay.pyw). Add `--preview` to show the overlay without EVE focused.

- [`python replay.py "Pilot Name"`](replay.py) replays your most recent fight with that pilot from your game logs, or your last fight without a name.
- [`python tools/screenshot.py`](tools/screenshot.py) regenerates the screenshot above.
- [`python build.py`](build.py) builds the portable zip into `dist`.
- Tests: `python -m unittest discover -s tests`. Lint and format with [Ruff](https://docs.astral.sh/ruff/).

To use your own ESI app, register one at [developers.eveonline.com](https://developers.eveonline.com/) with the scopes `esi-location.read_location.v1` and `esi-location.read_ship_type.v1` and the callback `http://localhost:8765/callback`, then set `esi_client_id` in `%LOCALAPPDATA%\EVE Pressure\settings.json`.

Note that there are some contexts this overlay hasn't been tested in. I don't often PvE, man structure guns, fly capital ships, mine, etc. If something breaks, file an issue, and ideally attach the log for the session you noticed the problem in (e.g. `C:/Users/<you>/Documents/EVE/logs/Gamelogs/*.txt`) so I can reproduce it.
