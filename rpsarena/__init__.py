import random
import math
import argparse
import time
import datetime
import sys

# ---------------- Configuration defaults ----------------
DEFAULT_WIDTH, DEFAULT_HEIGHT = 800, 800
DEFAULT_UNITS_PER_KIND = 50   # per emoji kind (3 kinds => total 150)
DEFAULT_DELAY_MS = 30         # tick delay; 0 requested -> coerced to 1
DEFAULT_BACKGROUND = "white"

FONT_SIZE = 24                # emoji font size
RADIUS = 14                   # approximate collision radius for an emoji at FONT_SIZE
MIN_SEP = RADIUS * 2 + 6      # minimum separation for initial placement

BASE_SPEED = 2.2              # movement cap per tick
ATTRACTION = 1.6              # toward prey
REPULSION = 1.8               # away from predators
ALLY_REPEL = 1.3              # mild repel from allies to avoid clumping
WALL_BOUNCE = 0.9             # bounce damping
JITTER = 0.25                 # tiny noise to prevent stalemates

POSTGAME_DELAY_MS = 5000      # pause after each game (windowed mode only)

DEFAULT_EMOJI = {
    "rock": u"🪨",
    "paper": u"📄",
    "scissors": u"✂️",
}
DEFAULT_BEATS = {
    "rock": "scissors",
    "paper": "rock",
    "scissors": "paper",
}
DEFAULT_LOSES_TO = {
    "rock": "paper",
    "paper": "scissors",
    "scissors": "rock",
}

LOG_FILENAME = "rps_arena_log.txt"

# ---------------- Data model ----------------
class Emoji(object):
    def __init__(self, kind, x, y, vx, vy, item=None):
        self.kind = kind
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.item = item  # Canvas item id (None in windowless mode)

# ---------------- Helpers ----------------
def distance_between(x1, y1, x2, y2):
    dx, dy = x1 - x2, y1 - y2
    return dx*dx + dy*dy

def normalize(dx, dy):
    mag = math.hypot(dx, dy)
    if mag == 0:
        return 0.0, 0.0
    return dx / mag, dy / mag

def cap_speed(vx, vy, cap):
    s = math.hypot(vx, vy)
    if s > cap and s > 0:
        scale = cap / s
        return vx * scale, vy * scale
    return vx, vy

# ---------------- Simulation ----------------
class RPSArena(object):
    def __init__(self, root, width, height, units_per_kind, delay_ms,
                 emoji=None, beats=None, loses_to=None,
                 fixed_seed=None, num_games=0,
                 log_filename=LOG_FILENAME, ff_enabled=True,
                 background_color=DEFAULT_BACKGROUND, countdown_s=0,
                 windowless=False, quiet=False):
        self.root = root
        self.windowless = windowless
        self.quiet = quiet

        self.width = int(width)
        self.height = int(height)

        # Dictionaries (allow custom games)
        self.emoji = emoji if emoji is not None else DEFAULT_EMOJI
        self.beats = beats if beats is not None else DEFAULT_BEATS
        self.loses_to = loses_to if loses_to is not None else DEFAULT_LOSES_TO
        self.kinds_order = sorted(list(self.emoji.keys()))

        # Per-kind units
        self.units_per_kind = max(1, int(units_per_kind))
        self.num_units = self.units_per_kind * len(self.kinds_order)

        delay_ms = int(delay_ms)
        if delay_ms <= 0:
            delay_ms = 1
        self.delay_ms = delay_ms
        self.base_delay_ms = delay_ms

        # Fast Forward
        self.ff_enabled = bool(ff_enabled)
        self.ff_active = False

        # Countdown
        # Windowless mode ignores countdown entirely.
        self.countdown_s = 0 if windowless else max(0, int(countdown_s))
        self._in_countdown = False
        self._countdown_remaining = 0
        self._countdown_item = None
        self._countdown_after_id = None

        # Multi-game controls
        self.num_games = max(0, int(num_games))  # 0 = unlimited
        self.games_played = 0

        # Seed handling
        self.fixed_seed = fixed_seed
        if self.fixed_seed is None:
            self.current_seed = random.randint(1, 1000000)
        else:
            self.current_seed = int(self.fixed_seed)
        random.seed(self.current_seed)

        # Logging
        self.log_filename = log_filename
        self.logf = open(self.log_filename, "a")
        self._write_log_header()

        # UI only if not windowless
        if not self.windowless:
            # tk is imported in main when needed; available here at runtime.
            self.root.title(u"RPS Arena")
            self.canvas = tk.Canvas(
                root, width=self.width, height=self.height,
                bg=background_color, highlightthickness=0
            )
            self.canvas.pack(fill="both", expand=True)
        else:
            self.canvas = None

        self.units = []
        self._restart_after_id = None

        # Per-game counters
        self.step_num = 0
        self.game_start_time = time.time()

        # First game
        self.reset()
        if not self.windowless:
            self._maybe_start_countdown()
            self.step()
        else:
            self.run_windowless()

    # ---------------- Logging helpers ----------------
    def _log(self, msg):
        """Write to logfile and also stdout unless quiet."""
        self.logf.write(msg + "\n")
        self.logf.flush()
        if not self.quiet:
            print(msg)

    def _write_log_header(self):
        now = datetime.datetime.now().isoformat(" ")
        settings = ("start={0} | size={1}x{2} | units_per_kind={3} | total_units={4} | "
                    "delay_ms={5} | seed={6} | kinds={7} | fast_forward={8} | num_games={9}"
                    .format(now, self.width, self.height,
                            self.units_per_kind, self.num_units,
                            self.delay_ms,
                            self.current_seed if self.fixed_seed is not None else "random",
                            ",".join(self.kinds_order),
                            "on" if self.ff_enabled else "off",
                            self.num_games))
        self._log(settings)
        header = ["STEP"]
        for k in self.kinds_order:
            header.append(self.emoji.get(k, k))
        self._log(",".join([unicode_safe(h) for h in header]))

    def _log_counts_if_needed(self, converted_happened):
        if not converted_happened:
            return
        counts = self._counts_by_kind()
        row = [str(self.step_num)]
        for k in self.kinds_order:
            row.append(str(counts.get(k, 0)))
        self._log(",".join(row))

    def _log_game_end(self):
        end_ts = datetime.datetime.now().isoformat(" ")
        elapsed = time.time() - self.game_start_time
        msg = "game_end at {0}; elapsed={1:.3f}s; steps={2}".format(end_ts, elapsed, self.step_num)
        self._log(msg)

    # ---------------- State & setup ----------------
    def _counts_by_kind(self):
        counts = {}
        for u in self.units:
            counts[u.kind] = counts.get(u.kind, 0) + 1
        return counts

    def reset(self):
        if self._restart_after_id is not None and self.root is not None:
            self.root.after_cancel(self._restart_after_id)
            self._restart_after_id = None
        if self._countdown_after_id is not None and self.root is not None:
            self.root.after_cancel(self._countdown_after_id)
            self._countdown_after_id = None

        if self.canvas is not None:
            self.canvas.delete("all")
        self.units = []
        self.step_num = 0
        self.game_start_time = time.time()
        self.ff_active = False
        self._in_countdown = False
        self.delay_ms = self.base_delay_ms
        self._countdown_item = None  # invalidate old item (deleted with canvas)

        # Exactly units_per_kind of each kind
        kinds_list = list(self.kinds_order)
        kinds = []
        for k in kinds_list:
            kinds.extend([k] * self.units_per_kind)
        random.shuffle(kinds)

        # Place with minimum separation (best-effort)
        placed = 0
        attempts = 0
        max_attempts = len(kinds) * 250
        while placed < len(kinds) and attempts < max_attempts:
            attempts += 1
            x = random.uniform(RADIUS + 2, self.width - RADIUS - 2)
            y = random.uniform(RADIUS + 2, self.height - RADIUS - 2)

            too_close = False
            for u in self.units:
                if distance_between(x, y, u.x, u.y) < (MIN_SEP * MIN_SEP):
                    too_close = True
                    break
            if too_close:
                continue

            kind = kinds[placed]
            item = None
            if self.canvas is not None:
                item = self.canvas.create_text(
                    x, y, text=self.emoji[kind],
                    font=("Apple Color Emoji", FONT_SIZE),
                    anchor="center"
                )

            angle = random.uniform(0, 2*math.pi)
            speed = random.uniform(0, BASE_SPEED)
            vx, vy = math.cos(angle)*speed, math.sin(angle)*speed
            self.units.append(Emoji(kind, x, y, vx, vy, item))
            placed += 1

        # If we couldn't place all with min-sep, place remaining without constraint
        for k in kinds[placed:]:
            x = random.uniform(RADIUS + 2, self.width - RADIUS - 2)
            y = random.uniform(RADIUS + 2, self.height - RADIUS - 2)
            item = None
            if self.canvas is not None:
                item = self.canvas.create_text(
                    x, y, text=self.emoji[k],
                    font=("Apple Color Emoji", FONT_SIZE),
                    anchor="center"
                )
            angle = random.uniform(0, 2*math.pi)
            speed = random.uniform(0, BASE_SPEED)
            vx, vy = math.cos(angle)*speed, math.sin(angle)*speed
            self.units.append(Emoji(k, x, y, vx, vy, item))

    # --- Countdown handling (windowed only) ---
    def _maybe_start_countdown(self):
        if self.canvas is None:
            return
        if self.countdown_s <= 0:
            self._in_countdown = False
            if self._countdown_item is not None:
                self.canvas.itemconfigure(self._countdown_item, state="hidden")
            return

        self._in_countdown = True
        self._countdown_remaining = int(self.countdown_s)

        if self._countdown_item is None:
            approx = int(min(self.width, self.height) * 0.25)
            font_size = max(48, min(approx, 220))
            self._countdown_item = self.canvas.create_text(
                self.width / 2, self.height / 2,
                text="",
                font=("Helvetica", font_size, "bold"),
                fill="black",
                anchor="center"
            )
        self.canvas.itemconfigure(self._countdown_item, state="normal")
        self._update_countdown()

    def _update_countdown(self):
        if not self._in_countdown or self.canvas is None:
            return

        self.canvas.itemconfigure(self._countdown_item, text=str(self._countdown_remaining))

        if self._countdown_remaining <= 0:
            self._end_countdown()
            return

        self._countdown_remaining -= 1
        self._countdown_after_id = self.root.after(1000, self._update_countdown)

    def _end_countdown(self):
        self._in_countdown = False
        if self.canvas is not None and self._countdown_item is not None:
            self.canvas.itemconfigure(self._countdown_item, state="hidden")
        if self._countdown_after_id is not None and self.root is not None:
            self.root.after_cancel(self._countdown_after_id)
            self._countdown_after_id = None

    # --- Behavior/physics ---
    def _force_closest_choice(self, me):
        prey_kind = self.beats[me.kind]
        predator_kind = self.loses_to[me.kind]

        closest_prey = None
        closest_pred = None
        best_prey_d2 = float("inf")
        best_pred_d2 = float("inf")

        for u in self.units:
            if u is me:
                continue
            d2 = distance_between(me.x, me.y, u.x, u.y)
            if u.kind == prey_kind and d2 < best_prey_d2:
                best_prey_d2 = d2
                closest_prey = u
            elif u.kind == predator_kind and d2 < best_pred_d2:
                best_pred_d2 = d2
                closest_pred = u

        fx, fy = 0.0, 0.0
        if closest_prey is not None and closest_pred is not None:
            if best_prey_d2 <= best_pred_d2:
                dx, dy = normalize(closest_prey.x - me.x, closest_prey.y - me.y)
                fx += dx * ATTRACTION
                fy += dy * ATTRACTION
            else:
                dx, dy = normalize(me.x - closest_pred.x, me.y - closest_pred.y)
                fx += dx * REPULSION
                fy += dy * REPULSION
        elif closest_prey is not None:
            dx, dy = normalize(closest_prey.x - me.x, closest_prey.y - me.y)
            fx += dx * ATTRACTION
            fy += dy * ATTRACTION
        elif closest_pred is not None:
            dx, dy = normalize(me.x - closest_pred.x, me.y - closest_pred.y)
            fx += dx * REPULSION
            fy += dy * REPULSION

        # mild ally repel within short range
        for u in self.units:
            if u is me or u.kind != me.kind:
                continue
            d2 = distance_between(me.x, me.y, u.x, u.y)
            if d2 < (MIN_SEP * MIN_SEP):
                dx, dy = normalize(me.x - u.x, me.y - u.y)
                denom = max(math.sqrt(d2), 1.0)
                strength = ALLY_REPEL * (float(MIN_SEP) / denom)
                fx += dx * strength
                fy += dy * strength

        fx += random.uniform(-JITTER, JITTER)
        fy += random.uniform(-JITTER, JITTER)
        return fx, fy

    def _apply_forces(self, u):
        fx, fy = self._force_closest_choice(u)
        u.vx += fx
        u.vy += fy
        u.vx, u.vy = cap_speed(u.vx, u.vy, BASE_SPEED)

    def _move(self, u):
        nx = u.x + u.vx
        ny = u.y + u.vy

        bounced = False
        if nx < RADIUS:
            nx = RADIUS + (RADIUS - nx)
            u.vx = -u.vx * WALL_BOUNCE
            bounced = True
        elif nx > self.width - RADIUS:
            nx = (self.width - RADIUS) - (nx - (self.width - RADIUS))
            u.vx = -u.vx * WALL_BOUNCE
            bounced = True
        if ny < RADIUS:
            ny = RADIUS + (RADIUS - ny)
            u.vy = -u.vy * WALL_BOUNCE
            bounced = True
        elif ny > self.height - RADIUS:
            ny = (self.height - RADIUS) - (ny - (self.height - RADIUS))
            u.vy = -u.vy * WALL_BOUNCE
            bounced = True

        if bounced:
            u.vx += random.uniform(-0.2, 0.2)
            u.vy += random.uniform(-0.2, 0.2)
            u.vx, u.vy = cap_speed(u.vx, u.vy, BASE_SPEED)

        u.x, u.y = nx, ny
        if self.canvas is not None and u.item is not None:
            self.canvas.coords(u.item, u.x, u.y)

    def _handle_collisions_and_conversions(self):
        r2 = float((RADIUS * 1.1) ** 2)
        n = len(self.units)
        i = 0
        converted = False
        while i < n:
            a = self.units[i]
            j = i + 1
            while j < n:
                b = self.units[j]
                if a.kind != b.kind and distance_between(a.x, a.y, b.x, b.y) <= r2:
                    if self.beats[a.kind] == b.kind:
                        b.kind = a.kind
                        if self.canvas is not None and b.item is not None:
                            self.canvas.itemconfigure(b.item, text=self.emoji[b.kind])
                        converted = True
                    elif self.beats[b.kind] == a.kind:
                        a.kind = b.kind
                        if self.canvas is not None and a.item is not None:
                            self.canvas.itemconfigure(a.item, text=self.emoji[a.kind])
                        converted = True
                j += 1
            i += 1
        return converted

    # --- Fast forward when only a resolvable matchup remains ---
    def _maybe_fast_forward(self):
        if not self.ff_enabled or self.ff_active:
            return
        kinds_present = set([u.kind for u in self.units])
        if len(kinds_present) != 2:
            return
        a, b = list(kinds_present)
        if (self.beats.get(a) == b) or (self.beats.get(b) == a):
            if self.delay_ms > 1:
                self.delay_ms = 1
                self.ff_active = True

    # --- End-of-game handling (windowed) ---
    def _check_end(self):
        kinds = set([u.kind for u in self.units])
        if len(kinds) == 1:
            self._log_game_end()
            self.games_played += 1

            # If we've reached the requested number of games, close after postgame delay (windowed only)
            if self.num_games > 0 and self.games_played >= self.num_games:
                if self.root is not None:
                    self._restart_after_id = self.root.after(POSTGAME_DELAY_MS, self.root.destroy)
                return True

            # Otherwise schedule reset into next game after the postgame delay (windowed only)
            if self.root is not None:
                self._restart_after_id = self.root.after(POSTGAME_DELAY_MS, self._do_reset_next_game)
            return True
        return False

    def _do_reset_next_game(self):
        self._restart_after_id = None

        # Advance / choose next seed
        if self.fixed_seed is not None:
            # Deterministic sequence S, S+1, S+2, ...
            self.current_seed += 1
            random.seed(self.current_seed)
        else:
            # Fresh random seed each game
            self.current_seed = random.randint(1, 1000000)
            random.seed(self.current_seed)

        self.reset()
        self._maybe_start_countdown()

    # --- Windowed stepping (Tk after loop) ---
    def step(self):
        # Pause physics while countdown is visible
        if self._in_countdown:
            self.root.after(self.delay_ms, self.step)
            return

        if self._restart_after_id is None:
            self.step_num += 1
            for u in self.units:
                self._apply_forces(u)
            for u in self.units:
                self._move(u)
            converted = self._handle_collisions_and_conversions()
            self._log_counts_if_needed(converted)
            self._maybe_fast_forward()
            self._check_end()
        self.root.after(self.delay_ms, self.step)

    # --- Windowless runner (headless loop) ---
    def run_windowless(self):
        while True:
            # Run one game to completion
            self.step_num = 0
            self.game_start_time = time.time()
            self.ff_active = False

            # Tight loop: run as fast as possible (no GUI delays, no postgame delay)
            while True:
                self.step_num += 1
                for u in self.units:
                    self._apply_forces(u)
                for u in self.units:
                    self._move(u)
                converted = self._handle_collisions_and_conversions()
                self._log_counts_if_needed(converted)
                self._maybe_fast_forward()

                # Check end-of-game (windowless path)
                kinds = set([u.kind for u in self.units])
                if len(kinds) == 1:
                    self._log_game_end()
                    self.games_played += 1
                    break

            # Stop if we've played the requested number of games
            if self.num_games > 0 and self.games_played >= self.num_games:
                break

            # Otherwise, advance seed and start next game immediately (no delay, no countdown)
            if self.fixed_seed is not None:
                self.current_seed += 1
                random.seed(self.current_seed)
            else:
                self.current_seed = random.randint(1, 1000000)
                random.seed(self.current_seed)

            self.reset()
            # (No countdown, no postgame delay in windowless mode)

# ---------------- Utility ----------------
def unicode_safe(x):
    try:
        return unicode(x)
    except Exception:
        return x

# ---------------- CLI / Main ----------------
def parse_args():
    p = argparse.ArgumentParser(description="RPS Arena (closest-choice strategy).")
    p.add_argument("-s", "--size", type=int, nargs=2, metavar=("WIDTH", "HEIGHT"),
                   help=f"Window size as WIDTH HEIGHT (default {DEFAULT_WIDTH} {DEFAULT_HEIGHT})")
    p.add_argument("-u", "--units", type=int, default=DEFAULT_UNITS_PER_KIND,
                   help=f"Number of units per emoji kind (default {DEFAULT_UNITS_PER_KIND})")
    p.add_argument("-d", "--delay", type=int, default=DEFAULT_DELAY_MS,
                   help=f"Tick delay in ms (0 coerced to 1) (default {DEFAULT_DELAY_MS})")
    p.add_argument("--seed", type=int, default=None,
                   help="Random seed for first game; subsequent games use seed+1, seed+2, ...")
    p.add_argument("-n", "--num-games", type=int, default=0,
                   help="Number of games to play (0 = unlimited; default 0). The app closes after the last game.")
    p.add_argument("--noff", action="store_true",
                   help="Disable Fast Forward (no auto-switch to delay=1)")
    p.add_argument("--bg", type=str, default=DEFAULT_BACKGROUND,
                   help=f"Background color (default {DEFAULT_BACKGROUND})")
    p.add_argument("--countdown", type=int, default=0,
                   help="Seconds to pause after placement and show a centered countdown (windowed only). Default 0 = off.")
    p.add_argument("--windowless", action="store_true",
                   help="Run without opening a Tkinter window. If -n/--num-games not set, defaults to 1.")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress stdout log messages (still written to log file).")
    return p.parse_args()

def main():
    args = parse_args()

    # If windowless and num-games not set, default to 1
    if args.windowless and args.num_games == 0:
        args.num_games = 1

    if args.size is not None:
        width, height = args.size[0], args.size[1]
    else:
        width, height = DEFAULT_WIDTH, DEFAULT_HEIGHT

    delay_ms = args.delay if args.delay > 0 else 1

    if args.windowless:
        root = None
    else:
        # Import tkinter only when needed (keeps windowless runs free of Tk)
        global tk
        import tkinter as tk  # noqa: F401
        root = tk.Tk()
        root.configure(bg=args.bg)
        root.geometry(f"{width}x{height}")
        root.resizable(False, False)
        root.title("RPS Arena")

    RPSArena(root, width, height, args.units, delay_ms,
             emoji=DEFAULT_EMOJI, beats=DEFAULT_BEATS, loses_to=DEFAULT_LOSES_TO,
             fixed_seed=args.seed, num_games=args.num_games,
             log_filename=LOG_FILENAME, ff_enabled=(not args.noff),
             background_color=args.bg, countdown_s=args.countdown,
             windowless=args.windowless, quiet=args.quiet)

    if not args.windowless:
        root.mainloop()

if __name__ == "__main__":
    main()
