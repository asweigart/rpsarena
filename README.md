# RPS Arena

A simulation of Rock Paper Scissors units chasing each other in a GUI window. Highly configurable.

- Each unit chases the nearest unit it can defeat, or flees the nearest unit that can defeat it — whichever is closer.
- On contact, the loser converts to the winner's kind.
- When only one kind remains, the game ends; either exits (if a seed was specified) or restarts with a fresh random seed.

## Features
- **Fast Forward:** When only two kinds remain and one beats the other (eventual victory), delay auto-switches to 1 ms to speed up the finish. Enabled by default; disable with `--noff`.
- **Deterministic runs:** `--seed` fixes the RNG seed (plays a single game and exits).
- **Logging:** `rps_arena_log.txt` records settings, a header row, conversion snapshots, and an end-of-game summary including elapsed time and total simulation step count.

## Requirements
- Python **3.2+**
- Standard library only (tkinter included with most Python installs)

## Usage

```bash
usage: __main__.py [-h] [-s WIDTH HEIGHT] [-u UNITS] [-d DELAY] [--seed SEED] [-n NUM_GAMES] [--no-ff] [--bg BG]
                   [--countdown COUNTDOWN] [--windowless] [-q] [--showstats] [--blocks BLOCKS] [--no-log]
                   [--logfile LOGFILE]

RPS Arena

options:
  -h, --help            show this help message and exit
  -s, --size WIDTH HEIGHT
                        Window size as WIDTH HEIGHT (default 800 800)
  -u, --units UNITS     Number of units per emoji kind (default 50)
  -d, --delay DELAY     Tick delay in ms (0 coerced to 1) (default 30)
  --seed SEED           Random seed for first game; subsequent games use seed+1, seed+2, ...
  -n, --num-games NUM_GAMES
                        Number of games to play (0=unlimited). Closes after last game.
  --no-ff               Disable Fast Forward (no auto-switch to delay=1)
  --bg BG               Background color or image filename (windowed). Colors: name or #RRGGBB.
  --countdown COUNTDOWN
                        Seconds to pause after placement (windowed only).
  --windowless          Run without Tk window. If -n not set, defaults to 1.
  -q, --quiet           Suppress stdout log messages (file logging unaffected unless --no-log).
  --showstats           Show elapsed time, step, and counts in lower-right corner (windowed only).
  --blocks BLOCKS       Number of random blocks (e.g., '5') OR path to JSON file describing blocks.
  --no-log              Disable logging to file (stdout still used unless --quiet).
  --logfile LOGFILE     Log file name (default rps_arena_log.txt)
````

### Command-line Options

* `-u N`, `--units N`
  Number of units per emoji kind (default `50`).
  With 3 kinds, total units = `N * 3`.

* `-d MS`, `--delay MS`
  Tick delay in milliseconds (default `30`). Minimum is 1.

* `--seed INT`
  Use a fixed random seed. If multiple games are run, the first game uses this seed, then increments sequentially (`seed+1`, `seed+2`, ...).

* `-n N`, `--num-games N`
  Number of games to run. `0` = unlimited (default).
  If nonzero, the app closes automatically after the last game (after the postgame delay in windowed mode).

* `--no-ff`
  Disable fast-forward. Normally, if only two kinds remain and one beats the other, the simulation speeds up by setting delay to 1ms.


## Logging

* `--logfile FILE`
  Log file name (default `rps_arena_log.txt`).

* `--no-log`
  Disable writing to the log file (stdout logs still shown unless `--quiet`).

* `-q`, `--quiet`
  Suppress stdout logging.
  Combine with `--no-log` for a fully silent run.

Example log file:

```
start=2025-08-23 12:34:56 | size=1000x700 | units=150 | delay_ms=30 | seed=987654 | kinds=paper,rock,scissors | fast_forward=on
step,📄,🪨,✂️
42,60,55,35
57,65,50,35
--snip--
game_end at 2025-08-23 12:35:49; elapsed=53.123s; steps=172
```

## Customization

You can pass your own dictionaries into the constructor (if integrating into another program):

```python
custom_emoji = {"rock": u"🪨", "paper": u"📄", "scissors": u"✂️"}
custom_beats  = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
custom_loses  = {"rock": "paper",    "paper": "scissors", "scissors": "rock"}

RPSArena(root, width, height, units, delay_ms,
         emoji=custom_emoji, beats=custom_beats, loses_to=custom_loses)
```




### Blocks / Obstacles

* `--blocks N`
  Place `N` random rectangular blocks that units cannot enter. Blocks are regenerated on each reset.
  Each block is ≤ 20% of arena area. Overlap is allowed.

* `--blocks FILE.json`
  Use fixed blocks from JSON file.
  Format:

  ```json
  {
    "blocks": [
      {"top": 0, "left": 0, "width": 100, "height": 100, "color": "green"},
      {"top": 200, "left": 150, "width": 150, "height": 80}
    ]
  }
  ```

  * `color` is optional (auto-contrasts with background if missing).
  * JSON blocks are reused on each reset.


### Windowless Mode

* `--windowless`
  Run without Tkinter window.

  * Defaults to 1 game unless `-n` is specified.
  * Ignores countdowns and postgame delays.
  * No rendering, runs as fast as possible.
  * Logs are printed to stdout unless `--quiet` is set, and optionally to file unless `--no-log` is set.
