# RPS Arena

A simulation of Rock Paper Scissors units chasing each other in a GUI window. Highly configurable.

Units roam a white arena using a **closest-choice** strategy:
- Each unit **chases** the nearest unit it can defeat, **or** **flees** the nearest unit that can defeat it — whichever is closer.
- On contact, the loser converts to the winner’s kind.
- When only one kind remains, the game ends; either exits (if a seed was specified) or restarts with a fresh random seed.

## Features
- **Fast Forward:** When only two kinds remain and one beats the other (eventual victory), delay auto-switches to **1 ms** to speed up the finish. Enabled by default; disable with `--noff`.
- **Fixed-size window:** no user resizing (preventing accidental scale changes).
- **Deterministic runs:** `--seed` fixes the RNG seed (plays a single game and exits).
- **Live logging:** `rps_arena_log.txt` records settings, a header row, conversion snapshots, and an end-of-game summary including **elapsed time** and **total step count**.

## Requirements
- Python **3.2+**
- Standard library only (tkinter included with most Python installs)

## Usage

```bash
python rps_arena.py -s 1200 800 -u 240 -d 16
python rps_arena.py --seed 12345 -d 0      # delay 0 is coerced to 1 ms
python rps_arena.py --noff                 # disable Fast Forward
````

### Command-line Options

* `-s, --size WIDTH HEIGHT` – window size (default `1000 700`)
* `-u, --units N` – total units (default `150`)
* `-d, --delay MS` – per-tick delay in milliseconds; **0 → coerced to 1** (default `30`)
* `--seed SEED` – run exactly one deterministic game with the given seed
* `--noff` – disable Fast Forward auto-speed-up

## Log File Format (`rps_arena_log.txt`)

1. **Line 1:** Start timestamp and settings (size, units, delay, seed, kinds, fast\_forward on/off)
2. **Line 2:** CSV header – `step,<emoji1>,<emoji2>,<emoji3>`
3. **Subsequent lines:** `step,count1,count2,count3` – appended **only when a conversion happens**
4. **Final line:** `game_end at <timestamp>; elapsed=<seconds>s; steps=<N>`

Example snippet:

```
start=2025-08-23 12:34:56 | size=1000x700 | units=150 | delay_ms=30 | seed=987654 | kinds=paper,rock,scissors | fast_forward=on
step,📄,🪨,✂️
42,60,55,35
57,65,50,35
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

> This build uses **single-prey/single-predator** rules per kind. If you’d like multi-prey or multi-predator rules (e.g., a kind beats multiple others), ask and we’ll provide a drop-in patch.

## License

MIT
