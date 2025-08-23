#!/usr/bin/env python
# -*- coding: utf-8 -*-
import random
import math
import argparse
import time
import datetime
import tkinter as tk

# ---------------- Configuration defaults ----------------
DEFAULT_WIDTH, DEFAULT_HEIGHT = 1000, 700
DEFAULT_UNITS = 150
DEFAULT_DELAY_MS = 30       # tick delay; 0 requested -> coerced to 1
BACKGROUND = "white"

FONT_SIZE = 24              # emoji font size
RADIUS = 14                 # approximate collision radius for an emoji at FONT_SIZE
MIN_SEP = RADIUS * 2 + 6    # minimum separation for initial placement

BASE_SPEED = 2.2            # movement cap per tick
ATTRACTION = 1.6            # toward prey
REPULSION = 1.8             # away from predators
ALLY_REPEL = 0.3            # mild repel from allies to avoid clumping
WALL_BOUNCE = 0.9           # bounce damping
JITTER = 0.25               # tiny noise to prevent stalemates

RESTART_DELAY_MS = 5000

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
class Unit(object):
    def __init__(self, kind, x, y, vx, vy, item):
        self.kind = kind
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.item = item  # Canvas item id

# ---------------- Helpers ----------------
def dist2(ax, ay, bx, by):
    dx, dy = ax - bx, ay - by
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

def choose_seed():
    sysrand = random.SystemRandom()
    return sysrand.randrange(1, 2**31 - 1)

# ---------------- Simulation ----------------
class RPSArena(object):
    def __init__(self, root, width, height, num_units, delay_ms,
                 emoji=None, beats=None, loses_to=None,
                 fixed_seed=None, log_filename=LOG_FILENAME, ff_enabled=True):
        self.root = root
        self.width = int(width)
        self.height = int(height)
        self.num_units = max(3, int(num_units))

        delay_ms = int(delay_ms)
        if delay_ms <= 0:
            delay_ms = 1
        self.delay_ms = delay_ms
        self.base_delay_ms = delay_ms  # remember original for resets

        # Fast Forward
        self.ff_enabled = bool(ff_enabled)
        self.ff_active = False

        # Dictionaries (allow custom games)
        self.emoji = emoji if emoji is not None else DEFAULT_EMOJI
        self.beats = beats if beats is not None else DEFAULT_BEATS
        self.loses_to = loses_to if loses_to is not None else DEFAULT_LOSES_TO

        # Stable order for logging
        self.kinds_order = sorted(list(self.emoji.keys()))

        # Seed handling
        self.fixed_seed = fixed_seed  # None or int
        if self.fixed_seed is None:
            self.current_seed = choose_seed()
        else:
            self.current_seed = int(self.fixed_seed)
        random.seed(self.current_seed)

        # Logging
        self.log_filename = log_filename
        self.logf = open(self.log_filename, "w")
        self._write_log_header()

        # UI (fixed title)
        self.root.title(u"RPS Arena")
        self.canvas = tk.Canvas(root, width=self.width, height=self.height,
                                bg=BACKGROUND, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.units = []
        self._restart_after_id = None

        # Per-game counters
        self.step_num = 0
        self.game_start_time = time.time()

        self.reset()
        self.step()

    def _write_log_header(self):
        now = datetime.datetime.now().isoformat(" ")
        settings = ("start={0} | size={1}x{2} | units={3} | delay_ms={4} | seed={5} | kinds={6} | fast_forward={7}"
                    .format(now, self.width, self.height, self.num_units, self.delay_ms,
                            self.current_seed, ",".join(self.kinds_order),
                            "on" if self.ff_enabled else "off"))
        self.logf.write(settings + "\n")
        header = ["step"]
        for k in self.kinds_order:
            header.append(self.emoji.get(k, k))
        self.logf.write(",".join([unicode_safe(h) for h in header]) + "\n")
        self.logf.flush()

    def _log_counts_if_needed(self, converted_happened):
        if not converted_happened:
            return
        counts = self._counts_by_kind()
        row = [str(self.step_num)]
        for k in self.kinds_order:
            row.append(str(counts.get(k, 0)))
        self.logf.write(",".join(row) + "\n")
        self.logf.flush()

    def _log_game_end(self):
        end_ts = datetime.datetime.now().isoformat(" ")
        elapsed = time.time() - self.game_start_time
        # Include total step count in end message
        msg = "game_end at {0}; elapsed={1:.3f}s; steps={2}".format(end_ts, elapsed, self.step_num)
        self.logf.write(msg + "\n")
        self.logf.flush()

    def _counts_by_kind(self):
        counts = {}
        for u in self.units:
            counts[u.kind] = counts.get(u.kind, 0) + 1
        return counts

    def reset(self):
        if self._restart_after_id is not None:
            self.root.after_cancel(self._restart_after_id)
            self._restart_after_id = None

        self.canvas.delete("all")
        self.units = []
        self.step_num = 0
        self.game_start_time = time.time()
        self.ff_active = False
        self.delay_ms = self.base_delay_ms  # restore original delay each game

        # Even split among kinds (sprinkle remainder randomly)
        kinds_list = list(self.kinds_order)
        kinds = []
        n_each = self.num_units // len(kinds_list)
        for k in kinds_list:
            kinds.extend([k] * n_each)
        for _ in range(self.num_units - len(kinds)):
            kinds.append(random.choice(kinds_list))
        random.shuffle(kinds)

        # Place with minimum separation
        placed = 0
        attempts = 0
        max_attempts = self.num_units * 250
        while placed < self.num_units and attempts < max_attempts:
            attempts += 1
            x = random.uniform(RADIUS + 2, self.width - RADIUS - 2)
            y = random.uniform(RADIUS + 2, self.height - RADIUS - 2)

            too_close = False
            for u in self.units:
                if dist2(x, y, u.x, u.y) < (MIN_SEP * MIN_SEP):
                    too_close = True
                    break
            if too_close:
                continue

            kind = kinds[placed]
            item = self.canvas.create_text(
                x, y, text=self.emoji[kind],
                font=("Apple Color Emoji", FONT_SIZE),
                anchor="center"
            )

            angle = random.uniform(0, 2*math.pi)
            speed = random.uniform(0, BASE_SPEED)
            vx, vy = math.cos(angle)*speed, math.sin(angle)*speed
            self.units.append(Unit(kind, x, y, vx, vy, item))
            placed += 1

        # If we couldn't meet min-sep for all, finish placement without the constraint
        for k in kinds[placed:]:
            x = random.uniform(RADIUS + 2, self.width - RADIUS - 2)
            y = random.uniform(RADIUS + 2, self.height - RADIUS - 2)
            item = self.canvas.create_text(x, y, text=self.emoji[k],
                                           font=("Apple Color Emoji", FONT_SIZE),
                                           anchor="center")
            angle = random.uniform(0, 2*math.pi)
            speed = random.uniform(0, BASE_SPEED)
            vx, vy = math.cos(angle)*speed, math.sin(angle)*speed
            self.units.append(Unit(k, x, y, vx, vy, item))

        # Title remains fixed as "RPS Arena"

    # --- Single (closest-choice) strategy ---
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
            d2 = dist2(me.x, me.y, u.x, u.y)
            if u.kind == prey_kind and d2 < best_prey_d2:
                best_prey_d2 = d2
                closest_prey = u
            elif u.kind == predator_kind and d2 < best_pred_d2:
                best_pred_d2 = d2
                closest_pred = u

        fx = 0.0
        fy = 0.0

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
            d2 = dist2(me.x, me.y, u.x, u.y)
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
                if a.kind != b.kind and dist2(a.x, a.y, b.x, b.y) <= r2:
                    if self.beats[a.kind] == b.kind:
                        b.kind = a.kind
                        self.canvas.itemconfigure(b.item, text=self.emoji[b.kind])
                        converted = True
                    elif self.beats[b.kind] == a.kind:
                        a.kind = b.kind
                        self.canvas.itemconfigure(a.item, text=self.emoji[a.kind])
                        converted = True
                j += 1
            i += 1
        return converted

    # --- Fast Forward detection: two kinds left & one beats the other ---
    def _maybe_fast_forward(self):
        if not self.ff_enabled or self.ff_active:
            return
        kinds_present = set([u.kind for u in self.units])
        if len(kinds_present) != 2:
            return
        kinds = list(kinds_present)
        a, b = kinds[0], kinds[1]
        if (self.beats.get(a) == b) or (self.beats.get(b) == a):
            if self.delay_ms > 1:
                self.delay_ms = 1
                self.ff_active = True
                # Title stays "RPS Arena"

    def _check_end(self):
        kinds = set([u.kind for u in self.units])
        if len(kinds) == 1:
            self._log_game_end()
            if self.fixed_seed is not None:
                # One game only; stop
                return True
            # Otherwise, schedule reset with a NEW random seed
            self._restart_after_id = self.root.after(RESTART_DELAY_MS, self._do_reset_with_new_seed)
            return True
        return False

    def _do_reset_with_new_seed(self):
        self._restart_after_id = None
        self.current_seed = choose_seed()
        random.seed(self.current_seed)
        self.reset()

    def step(self):
        if self._restart_after_id is None:
            self.step_num += 1
            for u in self.units:
                self._apply_forces(u)
            for u in self.units:
                self._move(u)
            converted = self._handle_collisions_and_conversions()
            self._log_counts_if_needed(converted)
            self._maybe_fast_forward()
            if not self._check_end():
                pass  # Title remains "RPS Arena"
        self.root.after(self.delay_ms, self.step)

# ---------------- Utility ----------------
def unicode_safe(x):
    try:
        return unicode(x)
    except Exception:
        return x

# ---------------- CLI / Main ----------------
def parse_args():
    p = argparse.ArgumentParser(
        description="RPS Arena (closest-choice strategy)."
    )
    p.add_argument("-s", "--size", type=int, nargs=2, metavar=("WIDTH", "HEIGHT"),
                   help="Window size as WIDTH HEIGHT (default {0} {1})".format(DEFAULT_WIDTH, DEFAULT_HEIGHT))
    p.add_argument("-u", "--units", type=int, default=DEFAULT_UNITS,
                   help="Total unit count (default {0})".format(DEFAULT_UNITS))
    p.add_argument("-d", "--delay", type=int, default=DEFAULT_DELAY_MS,
                   help="Tick delay in ms (0 coerced to 1) (default {0})".format(DEFAULT_DELAY_MS))
    p.add_argument("--seed", type=int, default=None,
                   help="Random seed (if set, play one game only and exit)")
    p.add_argument("--noff", action="store_true",
                   help="Disable Fast Forward (no auto-switch to delay=1)")
    return p.parse_args()

def main():
    args = parse_args()
    if args.size is not None:
        width, height = args.size[0], args.size[1]
    else:
        width, height = DEFAULT_WIDTH, DEFAULT_HEIGHT

    delay_ms = args.delay if args.delay > 0 else 1

    root = tk.Tk()
    root.configure(bg=BACKGROUND)
    root.geometry("{0}x{1}".format(width, height))
    root.resizable(False, False)  # fixed-size window
    root.title("RPS Arena")

    RPSArena(root, width, height, args.units, delay_ms,
             emoji=DEFAULT_EMOJI, beats=DEFAULT_BEATS, loses_to=DEFAULT_LOSES_TO,
             fixed_seed=args.seed, log_filename=LOG_FILENAME,
             ff_enabled=(not args.noff))
    root.mainloop()

if __name__ == "__main__":
    main()
