import os
import json
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
DEFAULT_BACKGROUND = "white"  # can also be an image path (windowed mode)
DEFAULT_BLOCKS = "0"          # string: "0" for none, "<int>" for random, or path to JSON file

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

# --- Color utilities (no Tk dependency required) ---
_COLOR_NAME_MAP = {
    # CSS-like basic names (common cases)
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "magenta": (255, 0, 255),
    "fuchsia": (255, 0, 255),
    "cyan": (0, 255, 255),
    "aqua": (0, 255, 255),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "lightgray": (211, 211, 211),
    "lightgrey": (211, 211, 211),
    "darkgray": (169, 169, 169),
    "darkgrey": (169, 169, 169),
    "navy": (0, 0, 128),
    "maroon": (128, 0, 0),
    "purple": (128, 0, 128),
    "teal": (0, 128, 128),
    "olive": (128, 128, 0),
    "silver": (192, 192, 192),
    "lime": (0, 255, 0),
    "orange": (255, 165, 0),
    "pink": (255, 192, 203),
    "brown": (165, 42, 42),
}

def _parse_hex_color(s):
    s = s.strip()
    if not s.startswith("#"):
        return None
    s = s[1:]
    if len(s) == 3:
        r = int(s[0]*2, 16)
        g = int(s[1]*2, 16)
        b = int(s[2]*2, 16)
        return (r, g, b)
    if len(s) == 6:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
        return (r, g, b)
    return None

def _rgb_from_name_or_hex(color_str):
    if not isinstance(color_str, str):
        return None
    c = color_str.strip().lower()
    rgb = _parse_hex_color(c)
    if rgb is not None:
        return rgb
    if c in _COLOR_NAME_MAP:
        return _COLOR_NAME_MAP[c]
    return None

def pick_contrast_color_from_rgb(rgb):
    """Given (r,g,b) 0..255, return 'black' or 'white' for contrast."""
    r, g, b = rgb
    luminance = (0.299 * r + 0.587 * g + 0.114 * b)  # 0..255
    return "black" if luminance >= 128 else "white"

def pick_contrast_color(bgcolor, tk_root=None):
    """
    Contrast text color for a solid background:
    - Try parse name/hex first (no Tk).
    - If that fails and tk_root provided, try tk_root.winfo_rgb.
    - Else default to 'white'.
    """
    rgb = _rgb_from_name_or_hex(bgcolor)
    if rgb is None and tk_root is not None:
        try:
            r16, g16, b16 = tk_root.winfo_rgb(bgcolor)  # 0..65535
            rgb = (r16 // 256, g16 // 256, b16 // 256)
        except Exception:
            pass
    if rgb is None:
        return "white"
    return pick_contrast_color_from_rgb(rgb)

# ---------------- Simulation ----------------
class RPSArena(object):
    def __init__(self, root, width, height, units_per_kind, delay_ms,
                 emoji=None, beats=None, loses_to=None,
                 fixed_seed=None, num_games=0,
                 log_filename=LOG_FILENAME, ff_enabled=True,
                 background_color=DEFAULT_BACKGROUND, countdown_s=0,
                 windowless=False, quiet=False, showstats=False, blocks=DEFAULT_BLOCKS):
        self.root = root
        self.windowless = windowless
        self.quiet = quiet
        self.showstats = showstats

        self.width = int(width)
        self.height = int(height)
        self.bg_source = background_color  # color or image path

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

        # Countdown (ignored in windowless mode)
        self.countdown_s = 0 if windowless else max(0, int(countdown_s))
        self._in_countdown = False
        self._countdown_remaining = 0
        self._countdown_item = None
        self._countdown_after_id = None

        # Stats overlay
        self._stats_item = None

        # Blocks (obstacles)
        # Internal representation: list of dicts {'x1','y1','x2','y2','color'}
        self.blocks = []
        self.block_items = []        # canvas ids
        self.block_color = "white"   # default for random blocks
        self.blocks_mode = "none"    # "none" | "random" | "json"
        self.blocks_count = 0
        self.blocks_json = None      # canonical blocks from JSON (persistent across resets)
        self.blocks_json_path = None

        self._parse_blocks_option(blocks)

        # Background image state (windowed)
        self._bg_item = None
        self._bg_photo = None
        self._bg_is_image = False
        self._bg_contrast_color = "white"  # for images, computed from luminance

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
            # Import tkinter only in windowed mode
            global tk
            import tkinter as tk  # type: ignore
            self.root.title(u"RPS Arena")
            self.canvas = tk.Canvas(
                root, width=self.width, height=self.height,
                bg="white", highlightthickness=0
            )
            self.canvas.pack(fill="both", expand=True)

            # Apply background (color or image) and pick text color
            self._apply_background(self.bg_source)

            # Choose text color for overlays (based on bg)
            if self._bg_is_image:
                self.ui_text_color = self._bg_contrast_color
            else:
                self.ui_text_color = pick_contrast_color(self.bg_source, tk_root=self.root)

            # Block color default for random blocks: auto-contrast with background
            self.block_color = self.ui_text_color
        else:
            self.canvas = None
            self.ui_text_color = "white"  # unused in windowless
            self.block_color = "white"

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

    # ---------------- Blocks option parsing ----------------
    def _parse_blocks_option(self, blocks_opt):
        """
        Parse --blocks option which may be:
          - "0" (or "00"...): no blocks
          - an integer string: number of random blocks
          - a path to a JSON file with schema:
                {"blocks":[{"top":int,"left":int,"width":int,"height":int,"color":"optional"}]}
        """
        if blocks_opt is None:
            self.blocks_mode = "none"
            self.blocks_count = 0
            return

        if isinstance(blocks_opt, str) and blocks_opt.strip().isdigit():
            self.blocks_mode = "random"
            self.blocks_count = max(0, int(blocks_opt.strip()))
            return

        # Otherwise treat as file path
        path = str(blocks_opt)
        if not os.path.isfile(path):
            raise ValueError(f"--blocks expects an integer or a JSON file path. Not found: {path}")

        # Load and validate JSON
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception as e:
            raise ValueError(f"Failed to read JSON file for --blocks: {e}")

        if not isinstance(data, dict) or "blocks" not in data or not isinstance(data["blocks"], list):
            raise ValueError("Invalid JSON: expected an object with key 'blocks' containing a list.")

        canon = []
        for i, obj in enumerate(data["blocks"]):
            if not isinstance(obj, dict):
                raise ValueError(f"Invalid JSON: blocks[{i}] is not an object.")
            required = ["top", "left", "width", "height"]
            for k in required:
                if k not in obj:
                    raise ValueError(f"Invalid JSON: blocks[{i}] missing required key '{k}'.")
                if not isinstance(obj[k], int) or obj[k] <= 0:
                    raise ValueError(f"Invalid JSON: blocks[{i}].{k} must be a positive integer.")
            color = obj.get("color", None)
            if color is not None and not isinstance(color, str):
                raise ValueError(f"Invalid JSON: blocks[{i}].color must be a string if provided.")
            # Convert to x1,y1,x2,y2
            x1 = float(obj["left"])
            y1 = float(obj["top"])
            x2 = x1 + float(obj["width"])
            y2 = y1 + float(obj["height"])
            canon.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "color": color})

        self.blocks_mode = "json"
        self.blocks_json = canon
        self.blocks_json_path = path

    # ---------------- Background handling (windowed) ----------------
    def _apply_background(self, source):
        """Apply a color or an image (stretched) as the canvas background."""
        if self.canvas is None:
            return

        # Clear previous bg image if any
        if self._bg_item is not None:
            try:
                self.canvas.delete(self._bg_item)
            except Exception:
                pass
            self._bg_item = None
            self._bg_photo = None
        self._bg_is_image = False
        self._bg_contrast_color = "white"

        # If 'source' looks like a file, try to load as image
        if isinstance(source, str) and os.path.isfile(source):
            # Prefer PIL for resizing & luminance; fall back to Tk PhotoImage
            pil_ok = False
            try:
                from PIL import Image, ImageTk, ImageStat  # type: ignore
                pil_ok = True
            except Exception:
                Image = ImageTk = ImageStat = None  # type: ignore

            if pil_ok:
                try:
                    img = Image.open(source).convert("RGB")
                    img = img.resize((self.width, self.height), Image.LANCZOS)
                    stat = ImageStat.Stat(img)
                    means = stat.mean  # [R,G,B] 0..255
                    self._bg_contrast_color = pick_contrast_color_from_rgb(tuple(int(m) for m in means))
                    self._bg_photo = ImageTk.PhotoImage(img)
                    self._bg_item = self.canvas.create_image(0, 0, image=self._bg_photo, anchor="nw")
                    self.canvas.lower(self._bg_item)  # send to back
                    self._bg_is_image = True
                    return
                except Exception as e:
                    self._log(f"warning: failed to load image '{source}' via PIL: {e}; falling back to Tk PhotoImage")

            # Fallback: Tk PhotoImage (may not resize)
            try:
                self._bg_photo = tk.PhotoImage(file=source)  # type: ignore
                self._bg_item = self.canvas.create_image(0, 0, image=self._bg_photo, anchor="nw")
                self.canvas.lower(self._bg_item)
                self._bg_is_image = True
                # Contrast fallback—assume dark average -> use white
                self._bg_contrast_color = "white"
                self._log("warning: PIL not available; background image not stretched.")
                return
            except Exception as e:
                self._log(f"warning: failed to load background image '{source}': {e}. Using color fallback.")
                # fall through to color

        # Treat as color
        try:
            self.canvas.config(bg=source)
        except Exception:
            # Fallback color if invalid
            self.canvas.config(bg="white")
            self._log(f"warning: invalid background '{source}', defaulting to white.")
        self._bg_is_image = False

    # ---------------- Blocks (obstacles) ----------------
    def _generate_blocks_random(self):
        """Generate random blocks anew (each reset)."""
        self.blocks = []
        if self.blocks_mode != "random" or self.blocks_count <= 0:
            return
        W, H = self.width, self.height
        max_area = 0.20 * (W * H)
        min_w, max_w = int(0.08 * W), int(0.40 * W)
        min_h, max_h = int(0.08 * H), int(0.40 * H)

        attempts = 0
        target = self.blocks_count
        while len(self.blocks) < target and attempts < target * 30:
            attempts += 1
            w = random.randint(min_w, max_w)
            h = random.randint(min_h, max_h)
            # Enforce per-block area cap
            if w * h > max_area:
                # shrink h to fit area (keep >= min_h if possible)
                h = max(int(max_area / max(w, 1)), min_h)
                if h < min_h:
                    continue
            x1 = random.randint(RADIUS + 2, max(RADIUS + 2, W - w - RADIUS - 2))
            y1 = random.randint(RADIUS + 2, max(RADIUS + 2, H - h - RADIUS - 2))
            x2 = x1 + w
            y2 = y1 + h
            if x2 - x1 >= 4 and y2 - y1 >= 4:
                self.blocks.append({
                    "x1": float(x1), "y1": float(y1),
                    "x2": float(x2), "y2": float(y2),
                    "color": self.block_color
                })

    def _apply_blocks_from_json(self):
        """Copy pre-validated JSON blocks (same each reset)."""
        self.blocks = []
        if self.blocks_mode != "json" or not self.blocks_json:
            return
        # Clone and apply per-block color defaults
        for b in self.blocks_json:
            color = b.get("color")
            if color is None:
                # Default to auto-contrast color against bg
                color = self.ui_text_color if not self.windowless else "white"
            self.blocks.append({
                "x1": float(b["x1"]), "y1": float(b["y1"]),
                "x2": float(b["x2"]), "y2": float(b["y2"]),
                "color": color
            })

    def _draw_blocks(self):
        """Draw blocks on the canvas (windowed only)."""
        if self.canvas is None:
            return
        # Clear existing
        for cid in self.block_items:
            try:
                self.canvas.delete(cid)
            except Exception:
                pass
        self.block_items = []

        for b in self.blocks:
            x1, y1, x2, y2 = b["x1"], b["y1"], b["x2"], b["y2"]
            color = b.get("color", self.ui_text_color)
            cid = self.canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline=color)
            # Keep blocks above background but below emojis
            self.canvas.tag_lower(cid)  # send low in stack
            if self._bg_item is not None:
                self.canvas.tag_raise(cid, self._bg_item)
            self.block_items.append(cid)

    def _point_in_any_block(self, x, y, margin=0.0):
        """Return True if point (x,y) is inside any block expanded by margin."""
        for b in self.blocks:
            x1, y1, x2, y2 = b["x1"], b["y1"], b["x2"], b["y2"]
            if (x1 - margin) <= x <= (x2 + margin) and (y1 - margin) <= y <= (y2 + margin):
                return True
        return False

    def _colliding_block(self, x, y, margin=0.0):
        """Return the first block dict containing point (x,y) with margin, or None."""
        for b in self.blocks:
            x1, y1, x2, y2 = b["x1"], b["y1"], b["x2"], b["y2"]
            if (x1 - margin) <= x <= (x2 + margin) and (y1 - margin) <= y <= (y2 + margin):
                return b
        return None

    # ---------------- Logging helpers ----------------
    def _log(self, msg):
        self.logf.write(msg + "\n")
        self.logf.flush()
        if not self.quiet:
            print(msg)

    def _write_log_header(self):
        now = datetime.datetime.now().isoformat(" ")
        blocks_desc = self.blocks_mode if self.blocks_mode != "json" else f"json:{self.blocks_json_path}"
        if self.blocks_mode == "random":
            blocks_desc += f"({self.blocks_count})"
        settings = ("start={0} | size={1}x{2} | units_per_kind={3} | total_units={4} | "
                    "delay_ms={5} | seed={6} | kinds={7} | fast_forward={8} | num_games={9} | blocks={10}"
                    .format(now, self.width, self.height,
                            self.units_per_kind, self.num_units,
                            self.delay_ms,
                            self.current_seed if self.fixed_seed is not None else "random",
                            ",".join(self.kinds_order),
                            "on" if self.ff_enabled else "off",
                            self.num_games, blocks_desc))
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
            # Re-apply background after clearing canvas
            self._apply_background(self.bg_source)

        # Blocks: regenerate for random mode each reset; reuse JSON blocks as-is
        if self.blocks_mode == "random":
            self._generate_blocks_random()
        elif self.blocks_mode == "json":
            self._apply_blocks_from_json()
        else:
            self.blocks = []

        if self.canvas is not None and self.blocks:
            self._draw_blocks()

        self.units = []
        self.step_num = 0
        self.game_start_time = time.time()
        self.ff_active = False
        self._in_countdown = False
        self.delay_ms = self.base_delay_ms
        self._countdown_item = None
        self._stats_item = None

        # Exactly units_per_kind of each kind
        kinds_list = list(self.kinds_order)
        kinds = []
        for k in kinds_list:
            kinds.extend([k] * self.units_per_kind)
        random.shuffle(kinds)

        # Place with minimum separation (best-effort) and outside blocks (margin=RADIUS)
        placed = 0
        attempts = 0
        max_attempts = len(kinds) * 500
        while placed < len(kinds) and attempts < max_attempts:
            attempts += 1
            x = random.uniform(RADIUS + 2, self.width - RADIUS - 2)
            y = random.uniform(RADIUS + 2, self.height - RADIUS - 2)

            if self._point_in_any_block(x, y, margin=RADIUS):
                continue

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

        # If we couldn't place all with constraints, place remaining without min-sep but still outside blocks
        for k in kinds[placed:]:
            tries = 0
            while True and tries < 2000:
                tries += 1
                x = random.uniform(RADIUS + 2, self.width - RADIUS - 2)
                y = random.uniform(RADIUS + 2, self.height - RADIUS - 2)
                if not self._point_in_any_block(x, y, margin=RADIUS):
                    break
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

    # --- Stats overlay ---
    def _update_stats_overlay(self):
        if not self.showstats or self.canvas is None:
            return
        elapsed = time.time() - self.game_start_time
        counts = self._counts_by_kind()
        parts = [f"{k}:{counts.get(k,0)}" for k in self.kinds_order]
        text = f"t={elapsed:.1f}s step={self.step_num} " + " ".join(parts)
        if self._stats_item is None:
            self._stats_item = self.canvas.create_text(
                self.width - 5, self.height - 5,
                text=text, anchor="se",
                font=("Helvetica", 10), fill=self.ui_text_color
            )
        else:
            self.canvas.itemconfigure(self._stats_item, text=text)

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
                fill=self.ui_text_color,
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
        # Proposed movement
        nx = u.x + u.vx
        ny = u.y + u.vy

        # Walls
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

        # Blocks collision — prevent center from entering any expanded rectangle
        for _ in range(2):
            b = self._colliding_block(nx, ny, margin=RADIUS)
            if b is None:
                break
            x1, y1, x2, y2 = b["x1"], b["y1"], b["x2"], b["y2"]
            left = x1 - RADIUS
            right = x2 + RADIUS
            top = y1 - RADIUS
            bottom = y2 + RADIUS

            dx_left = abs(nx - left)
            dx_right = abs(nx - right)
            dy_top = abs(ny - top)
            dy_bottom = abs(ny - bottom)

            m = min(dx_left, dx_right, dy_top, dy_bottom)
            if m == dx_left:
                nx = left
                u.vx = -abs(u.vx) * WALL_BOUNCE
            elif m == dx_right:
                nx = right
                u.vx = abs(u.vx) * WALL_BOUNCE
            elif m == dy_top:
                ny = top
                u.vy = -abs(u.vy) * WALL_BOUNCE
            else:
                ny = bottom
                u.vy = abs(u.vy) * WALL_BOUNCE
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
            self._update_stats_overlay()
        self.root.after(self.delay_ms, self.step)

    # --- Windowless runner (headless loop) ---
    def run_windowless(self):
        while True:
            self.step_num = 0
            self.game_start_time = time.time()
            self.ff_active = False
            while True:
                self.step_num += 1
                for u in self.units:
                    self._apply_forces(u)
                for u in self.units:
                    self._move(u)
                converted = self._handle_collisions_and_conversions()
                self._log_counts_if_needed(converted)
                self._maybe_fast_forward()
                kinds = set([u.kind for u in self.units])
                if len(kinds) == 1:
                    self._log_game_end()
                    self.games_played += 1
                    break
            if self.num_games > 0 and self.games_played >= self.num_games:
                break
            if self.fixed_seed is not None:
                self.current_seed += 1
                random.seed(self.current_seed)
            else:
                self.current_seed = random.randint(1, 1000000)
                random.seed(self.current_seed)
            self.reset()

# ---------------- Utility ----------------
def unicode_safe(x):
    try:
        return unicode(x)
    except Exception:
        return x

# ---------------- CLI / Main ----------------
def parse_args():
    p = argparse.ArgumentParser(description="RPS Arena (closest-choice strategy).")
    p.add_argument("-s","--size", type=int, nargs=2, metavar=("WIDTH","HEIGHT"),
                   help=f"Window size as WIDTH HEIGHT (default {DEFAULT_WIDTH} {DEFAULT_HEIGHT})")
    p.add_argument("-u","--units", type=int, default=DEFAULT_UNITS_PER_KIND,
                   help=f"Number of units per emoji kind (default {DEFAULT_UNITS_PER_KIND})")
    p.add_argument("-d","--delay", type=int, default=DEFAULT_DELAY_MS,
                   help=f"Tick delay in ms (0 coerced to 1) (default {DEFAULT_DELAY_MS})")
    p.add_argument("--seed", type=int, default=None,
                   help="Random seed for first game; subsequent games use seed+1, seed+2, ...")
    p.add_argument("-n","--num-games", type=int, default=0,
                   help="Number of games to play (0=unlimited). Closes after last game.")
    p.add_argument("--noff", action="store_true",
                   help="Disable Fast Forward (no auto-switch to delay=1)")
    p.add_argument("--bg", type=str, default=DEFAULT_BACKGROUND,
                   help="Background color or image filename (windowed). Colors: name or #RRGGBB.")
    p.add_argument("--countdown", type=int, default=0,
                   help="Seconds to pause after placement (windowed only).")
    p.add_argument("--windowless", action="store_true",
                   help="Run without Tk window. If -n not set, defaults to 1.")
    p.add_argument("-q","--quiet", action="store_true",
                   help="Suppress stdout log messages (still written to log file).")
    p.add_argument("--showstats", action="store_true",
                   help="Show elapsed time, step, and counts in lower-right corner (windowed only).")
    p.add_argument("--blocks", type=str, default=DEFAULT_BLOCKS,
                   help="Number of random blocks (e.g., '5') OR path to JSON file describing blocks.")
    return p.parse_args()

def main():
    args = parse_args()
    if args.windowless and args.num_games == 0:
        args.num_games = 1
    if args.size is not None:
        width, height = args.size
    else:
        width, height = DEFAULT_WIDTH, DEFAULT_HEIGHT
    delay_ms = args.delay if args.delay > 0 else 1

    if args.windowless:
        root = None
    else:
        # Import tkinter only when needed (keeps windowless runs free of Tk)
        global tk
        import tkinter as tk  # type: ignore
        root = tk.Tk()
        root.configure(bg="black")  # hidden by canvas; use neutral color
        root.geometry(f"{width}x{height}")
        root.resizable(False, False)
        root.title("RPS Arena")

    RPSArena(root, width, height, args.units, delay_ms,
             emoji=DEFAULT_EMOJI, beats=DEFAULT_BEATS, loses_to=DEFAULT_LOSES_TO,
             fixed_seed=args.seed, num_games=args.num_games,
             log_filename=LOG_FILENAME, ff_enabled=(not args.noff),
             background_color=args.bg, countdown_s=args.countdown,
             windowless=args.windowless, quiet=args.quiet,
             showstats=args.showstats, blocks=args.blocks)

    if not args.windowless:
        root.mainloop()

if __name__=="__main__":
    main()
