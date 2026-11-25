"""Color attention experiment for Tobii Pro Spark / Tobii Pro SDK.

The Tobii Pro SDK must be installed separately. See README.md.
"""

from __future__ import annotations

import csv
import math
import queue
import random
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from typing import Any

try:
    import tobii_research as tr
except (ImportError, OSError):
    tr = None


# Six requested colors plus a neutral seventh control circle.
COLORS = [
    {"key": "red", "label": "แดง", "hex": "#E53935"},
    {"key": "yellow", "label": "เหลือง", "hex": "#FDD835"},
    {"key": "green", "label": "เขียว", "hex": "#43A047"},
    {"key": "orange", "label": "ส้ม", "hex": "#FB8C00"},
    {"key": "blue", "label": "น้ำเงิน", "hex": "#1E88E5"},
    {"key": "purple", "label": "ม่วง", "hex": "#8E24AA"},
    {"key": "white", "label": "ขาว (วงกลมควบคุม)", "hex": "#F5F5F5"},
]

REST_SECONDS = 5
COUNTDOWN_SECONDS = 3
ROUNDS = 4
REVEAL_INTERVAL_MS = 250
FULL_DISPLAY_MS = 10_000
FIXATION_THRESHOLD_MS = 250
OFF_AOI_THRESHOLD_MS = 100
MAX_SAMPLE_GAP_MS = 100

BACKGROUND = "#181818"
REST_BACKGROUND = "#808080"

RAW_FIELDS = [
    "participant_name",
    "source_device",
    "session_elapsed_ms",
    "local_time_ns",
    "tobii_system_time_stamp",
    "phase",
    "round",
    "gaze_valid",
    "left_x",
    "left_y",
    "right_x",
    "right_y",
    "gaze_x",
    "gaze_y",
    "aoi",
]


def gaze_point(data: dict[str, Any], side: str) -> tuple[float, float] | None:
    """Return a valid normalized gaze point for one eye, if available."""
    point = data.get(f"{side}_gaze_point_on_display_area")
    validity = data.get(f"{side}_gaze_point_validity")
    valid_value = getattr(tr, "VALIDITY_VALID", 1) if tr is not None else 1
    if not point or validity != valid_value:
        return None
    try:
        x, y = float(point[0]), float(point[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return x, y


def circle_for_gaze(
    gaze_x: float,
    gaze_y: float,
    targets: dict[str, tuple[float, float, float]],
    screen_width: int,
    screen_height: int,
) -> str:
    """Map normalized gaze coordinates to a visible circular AOI."""
    px = gaze_x * screen_width
    py = gaze_y * screen_height
    for color_key, (center_x, center_y, radius) in targets.items():
        if (px - center_x) ** 2 + (py - center_y) ** 2 <= radius**2:
            return color_key
    return "OFF_AOI"


def fixation_and_focus_events(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, float | int]], list[dict[str, Any]]]:
    """Summarize circle dwell runs and brief glances/focus-loss episodes."""
    color_stats: dict[str, dict[str, float | int]] = {
        color["key"]: {
            "raw_gaze_ms": 0.0,
            "confirmed_fixations": 0,
            "confirmed_fixation_ms": 0.0,
            "longest_fixation_ms": 0.0,
            "brief_glances": 0,
            "focus_loss_events": 0,
        }
        for color in COLORS
    }
    color_stats["OFF_AOI"] = {
        "raw_gaze_ms": 0.0,
        "confirmed_fixations": 0,
        "confirmed_fixation_ms": 0.0,
        "longest_fixation_ms": 0.0,
        "brief_glances": 0,
        "focus_loss_events": 0,
    }
    events: list[dict[str, Any]] = []
    max_gap_ns = MAX_SAMPLE_GAP_MS * 1_000_000
    fixation_ns = FIXATION_THRESHOLD_MS * 1_000_000
    off_aoi_ns = OFF_AOI_THRESHOLD_MS * 1_000_000

    runs: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    continuity_id = 0

    def finish_run() -> None:
        nonlocal current
        if not current:
            return
        duration_ns = max(0, current[-1]["local_time_ns"] - current[0]["local_time_ns"])
        label = current[0]["aoi"]
        duration_ms = duration_ns / 1_000_000
        runs.append(
            {
                "aoi": label,
                "start_ns": current[0]["local_time_ns"],
                "end_ns": current[-1]["local_time_ns"],
                "duration_ns": duration_ns,
                "duration_ms": duration_ms,
                "continuity_id": continuity_id,
            }
        )
        stats = color_stats.get(label)
        if stats is not None and label != "OFF_AOI":
            stats["raw_gaze_ms"] += duration_ms
            if duration_ns >= fixation_ns:
                stats["confirmed_fixations"] += 1
                stats["confirmed_fixation_ms"] += duration_ms
                stats["longest_fixation_ms"] = max(
                    float(stats["longest_fixation_ms"]), duration_ms
                )
        elif label == "OFF_AOI":
            color_stats["OFF_AOI"]["raw_gaze_ms"] += duration_ms
        current = []

    previous_time: int | None = None
    previous_aoi: str | None = None
    for row in sorted(rows, key=lambda item: item["local_time_ns"]):
        if not row["gaze_valid"] or not row["aoi"]:
            finish_run()
            continuity_id += 1
            previous_time = None
            previous_aoi = None
            continue
        sample_time = row["local_time_ns"]
        label = row["aoi"]
        if current and (
            previous_time is None
            or sample_time - previous_time > max_gap_ns
            or label != previous_aoi
        ):
            finish_run()
            if previous_time is not None and sample_time - previous_time > max_gap_ns:
                continuity_id += 1
        if not current:
            current = [row]
        else:
            current.append(row)
        previous_time = sample_time
        previous_aoi = label
    finish_run()

    # A confirmed fixation establishes the current focus. Valid gaze outside
    # that same circle for more than 100 ms records focus loss; shorter visits
    # are recorded as glances. Invalid gaze breaks the sequence.
    focused_color: str | None = None
    away_start_ns: int | None = None
    away_end_ns: int | None = None
    away_labels: set[str] = set()
    active_continuity: int | None = None
    for run in runs:
        if active_continuity is None:
            active_continuity = run["continuity_id"]
        elif run["continuity_id"] != active_continuity:
            focused_color = None
            away_start_ns = None
            away_end_ns = None
            away_labels = set()
            active_continuity = run["continuity_id"]
        label = run["aoi"]
        if focused_color is None:
            if (
                label in color_stats
                and label != "OFF_AOI"
                and run["duration_ns"] >= fixation_ns
            ):
                focused_color = label
            continue

        if label == focused_color:
            if away_start_ns is not None:
                away_duration_ns = max(0, run["start_ns"] - away_start_ns)
                kind = "brief_glance" if away_duration_ns <= off_aoi_ns else "focus_loss"
                event = {
                    "event": kind,
                    "focus_color": focused_color,
                    "looked_at": ",".join(sorted(away_labels)),
                    "duration_ms": away_duration_ns / 1_000_000,
                    "local_time_ns": away_start_ns,
                }
                events.append(event)
                if kind == "brief_glance":
                    color_stats[focused_color]["brief_glances"] += 1
                else:
                    color_stats[focused_color]["focus_loss_events"] += 1
                away_start_ns = None
                away_end_ns = None
                away_labels = set()
            continue

        if away_start_ns is None:
            away_start_ns = run["start_ns"]
        away_end_ns = run["end_ns"]
        away_labels.add(label)
        away_duration_ns = max(0, away_end_ns - away_start_ns)
        if away_duration_ns > off_aoi_ns:
            events.append(
                {
                    "event": "focus_loss",
                    "focus_color": focused_color,
                    "looked_at": ",".join(sorted(away_labels)),
                    "duration_ms": away_duration_ns / 1_000_000,
                    "local_time_ns": away_start_ns,
                }
            )
            color_stats[focused_color]["focus_loss_events"] += 1
            focused_color = None
            away_start_ns = None
            away_end_ns = None
            away_labels = set()

        if (
            label in color_stats
            and label != "OFF_AOI"
            and run["duration_ns"] >= fixation_ns
        ):
            focused_color = label
            away_start_ns = None
            away_end_ns = None
            away_labels = set()

    if focused_color is not None and away_start_ns is not None and away_end_ns is not None:
        away_duration_ns = max(0, away_end_ns - away_start_ns)
        kind = "brief_glance" if away_duration_ns <= off_aoi_ns else "focus_loss"
        events.append(
            {
                "event": kind,
                "focus_color": focused_color,
                "looked_at": ",".join(sorted(away_labels)),
                "duration_ms": away_duration_ns / 1_000_000,
                "local_time_ns": away_start_ns,
            }
        )
        if kind == "brief_glance":
            color_stats[focused_color]["brief_glances"] += 1
        else:
            color_stats[focused_color]["focus_loss_events"] += 1

    return color_stats, events


class ColorAttentionExperiment:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Tobii Spark - การทดลองการมองสี")
        self.root.attributes("-fullscreen", True)
        self.root.configure(background=REST_BACKGROUND)
        self.root.protocol("WM_DELETE_WINDOW", self.abort_or_close)
        self.root.bind("<Escape>", self.abort_or_close)

        self.canvas = tk.Canvas(root, background=REST_BACKGROUND, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.on_canvas_resize)

        self.screen_width = max(1, root.winfo_screenwidth())
        self.screen_height = max(1, root.winfo_screenheight())
        self.gaze_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.context_lock = threading.Lock()
        self.recording_enabled = False
        self.shared_phase = "setup"
        self.shared_round: int | None = None
        self.shared_targets: dict[str, tuple[float, float, float]] = {}
        self.session_origin_ns = 0

        self.tracker: Any = None
        self.subscription_active = False
        self.participant_name = ""
        self.session_timestamp = ""
        self.session_active = False
        self.session_finishing = False
        self.aborted = False
        self.session_samples: list[dict[str, Any]] = []
        self.raw_file = None
        self.raw_writer = None
        self.file_paths: dict[str, Path] = {}
        self.round_metadata: dict[int, dict[str, Any]] = {}
        self.round_number = 0
        self.round_colors: dict[int, str] = {}
        self.round_positions: list[tuple[float, float]] = []
        self.reveal_order: list[int] = []
        self.reveal_index = 0
        self.visible_colors: dict[str, tuple[float, float, float]] = {}
        self.previous_positions: dict[str, int] | None = None
        self.session_random = random.SystemRandom()
        self.last_flush_time = time.monotonic()
        self.start_button: ttk.Button | None = None
        self.connect_button: ttk.Button | None = None
        self.name_entry: ttk.Entry | None = None
        self.status_label: ttk.Label | None = None
        self.status_text = ""

        self.draw_setup()
        self.root.after(20, self.poll_queues)

    def on_canvas_resize(self, event: tk.Event) -> None:
        self.screen_width = max(1, event.width)
        self.screen_height = max(1, event.height)

    def draw_setup(self) -> None:
        self.canvas.delete("all")
        self.canvas.configure(background=REST_BACKGROUND)
        panel = tk.Frame(self.canvas, background="#F4F4F4", padx=38, pady=28)
        tk.Label(
            panel,
            text="การทดลองการมองสี",
            font=("Segoe UI", 22, "bold"),
            background="#F4F4F4",
            foreground="#222222",
        ).pack(pady=(0, 10))
        tk.Label(
            panel,
            text="กรอกชื่อผู้เข้าร่วม แล้วเชื่อมต่อ Tobii Pro Spark",
            font=("Segoe UI", 12),
            background="#F4F4F4",
            foreground="#333333",
        ).pack(pady=(0, 16))
        tk.Label(
            panel,
            text="ชื่อผู้เข้าร่วม",
            font=("Segoe UI", 11),
            background="#F4F4F4",
            foreground="#222222",
            anchor="w",
        ).pack(fill="x")
        self.name_entry = ttk.Entry(panel, width=36, font=("Segoe UI", 12))
        self.name_entry.pack(fill="x", pady=(5, 12))
        buttons = tk.Frame(panel, background="#F4F4F4")
        buttons.pack()
        self.connect_button = ttk.Button(
            buttons, text="ค้นหาและเชื่อมต่อ Tobii", command=self.connect_tracker
        )
        self.connect_button.pack(side="left", padx=5)
        self.start_button = ttk.Button(
            buttons,
            text="เริ่มการทดลอง",
            command=self.start_experiment,
            state="disabled",
        )
        self.start_button.pack(side="left", padx=5)
        self.status_label = ttk.Label(
            panel,
            text=self.status_text,
            font=("Segoe UI", 10),
            background="#F4F4F4",
            foreground="#333333",
            wraplength=520,
        )
        self.status_label.pack(pady=(14, 0))
        tk.Label(
            panel,
            text="กด Esc เพื่อออกจากหน้าจอเต็ม",
            font=("Segoe UI", 9),
            background="#F4F4F4",
            foreground="#666666",
        ).pack(pady=(14, 0))
        self.canvas.create_window(
            self.screen_width / 2,
            self.screen_height / 2,
            window=panel,
            anchor="center",
        )
        if self.name_entry is not None:
            self.name_entry.focus_set()

    def set_status(self, message: str) -> None:
        self.status_text = message
        if self.status_label is not None:
            self.status_label.configure(text=message)

    def connect_tracker(self) -> None:
        if tr is None:
            self.set_status(
                "ไม่พบ tobii_research: ติดตั้งด้วย `python -m pip install tobii_research` "
                "ใน Python 3.10 แล้วเปิดโปรแกรมใหม่"
            )
            return
        if self.connect_button is not None:
            self.connect_button.configure(state="disabled")
        self.set_status("กำลังค้นหาเครื่องติดตามสายตา...")

        def worker() -> None:
            try:
                trackers = tr.find_all_eyetrackers()
                if not trackers:
                    self.ui_queue.put(("scan_error", "ไม่พบ eye tracker ที่เชื่อมต่ออยู่"))
                    return
                self.ui_queue.put(("tracker_found", trackers[0]))
            except Exception as exc:  # SDK and device errors are surfaced in the UI.
                self.ui_queue.put(("scan_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def start_experiment(self) -> None:
        if self.tracker is None:
            self.set_status("กรุณาเชื่อมต่อ Tobii ก่อนเริ่มการทดลอง")
            return
        if self.name_entry is None:
            return
        participant = self.name_entry.get().strip()
        if not participant:
            self.set_status("กรุณากรอกชื่อผู้เข้าร่วม")
            return
        self.participant_name = participant
        if self.start_button is not None:
            self.start_button.configure(state="disabled")
        if self.connect_button is not None:
            self.connect_button.configure(state="disabled")
        self.set_status("กำลังเริ่มรับข้อมูลจาก Tobii...")

        def worker() -> None:
            try:
                self.tracker.subscribe_to(
                    tr.EYETRACKER_GAZE_DATA, self.on_gaze_data, as_dictionary=True
                )
                self.ui_queue.put(("stream_ready", None))
            except Exception as exc:
                self.ui_queue.put(("stream_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def prepare_session_files(self) -> None:
        self.session_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(__file__).resolve().parent / "data"
        output_dir.mkdir(parents=True, exist_ok=True)
        base = f"tobii_color_{self.session_timestamp}"
        suffix = 1
        while any(output_dir.glob(f"{base}*")):
            base = f"tobii_color_{self.session_timestamp}_{suffix:02d}"
            suffix += 1
        self.file_paths = {
            "gaze": output_dir / f"{base}_gaze.csv",
            "summary": output_dir / f"{base}_summary.csv",
            "events": output_dir / f"{base}_events.csv",
            "stimuli": output_dir / f"{base}_stimuli.csv",
        }
        self.raw_file = self.file_paths["gaze"].open(
            "w", newline="", encoding="utf-8-sig"
        )
        self.raw_writer = csv.DictWriter(self.raw_file, fieldnames=RAW_FIELDS)
        self.raw_writer.writeheader()
        self.raw_file.flush()

    def on_stream_ready(self, _payload: Any) -> None:
        self.subscription_active = True
        self.session_origin_ns = time.perf_counter_ns()
        self.session_active = True
        self.session_finishing = False
        self.aborted = False
        self.prepare_session_files()
        self.set_shared_context("rest", None, {})
        with self.context_lock:
            self.recording_enabled = True
        self.canvas.delete("all")
        self.canvas.configure(background=REST_BACKGROUND)
        self.root.after(REST_SECONDS * 1000, self.begin_countdown)

    def set_shared_context(
        self,
        phase: str,
        round_number: int | None,
        targets: dict[str, tuple[float, float, float]],
    ) -> None:
        with self.context_lock:
            self.shared_phase = phase
            self.shared_round = round_number
            self.shared_targets = dict(targets)

    def on_gaze_data(self, data: dict[str, Any]) -> None:
        sample_ns = time.perf_counter_ns()
        left = gaze_point(data, "left")
        right = gaze_point(data, "right")
        points = [point for point in (left, right) if point is not None]
        with self.context_lock:
            if not self.recording_enabled:
                return
            phase = self.shared_phase
            round_number = self.shared_round
            targets = dict(self.shared_targets)
        if points:
            gaze_x = sum(point[0] for point in points) / len(points)
            gaze_y = sum(point[1] for point in points) / len(points)
            aoi = circle_for_gaze(
                gaze_x,
                gaze_y,
                targets,
                self.screen_width,
                self.screen_height,
            )
            valid = True
        else:
            gaze_x = gaze_y = None
            aoi = ""
            valid = False
        self.gaze_queue.put(
            {
                "participant_name": self.participant_name,
                "source_device": "tobii_spark",
                "session_elapsed_ms": (sample_ns - self.session_origin_ns) / 1_000_000,
                "local_time_ns": sample_ns,
                "tobii_system_time_stamp": data.get("system_time_stamp", ""),
                "phase": phase,
                "round": round_number if round_number is not None else "",
                "gaze_valid": valid,
                "left_x": left[0] if left else "",
                "left_y": left[1] if left else "",
                "right_x": right[0] if right else "",
                "right_y": right[1] if right else "",
                "gaze_x": gaze_x if gaze_x is not None else "",
                "gaze_y": gaze_y if gaze_y is not None else "",
                "aoi": aoi,
            }
        )

    def poll_queues(self) -> None:
        while True:
            try:
                message, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if message == "tracker_found":
                self.tracker = payload
                model = getattr(payload, "model", "Tobii eye tracker")
                serial = getattr(payload, "serial_number", "")
                description = f"เชื่อมต่อแล้ว: {model}"
                if serial:
                    description += f" (S/N {serial})"
                self.set_status(description)
                if self.start_button is not None:
                    self.start_button.configure(state="normal")
                if self.connect_button is not None:
                    self.connect_button.configure(state="normal")
            elif message in ("scan_error", "stream_error"):
                self.set_status(f"เชื่อมต่อไม่สำเร็จ: {payload}")
                if self.connect_button is not None:
                    self.connect_button.configure(state="normal")
                if self.start_button is not None and self.tracker is not None:
                    self.start_button.configure(state="normal")
            elif message == "stream_ready":
                self.on_stream_ready(payload)
            elif message == "session_closed":
                self.subscription_active = False
                self.finalize_session()

        rows_received = 0
        while True:
            try:
                row = self.gaze_queue.get_nowait()
            except queue.Empty:
                break
            self.session_samples.append(row)
            if self.raw_writer is not None:
                self.raw_writer.writerow({key: row.get(key, "") for key in RAW_FIELDS})
            rows_received += 1
        if rows_received and self.raw_file is not None:
            if time.monotonic() - self.last_flush_time >= 1.0:
                self.raw_file.flush()
                self.last_flush_time = time.monotonic()
        self.root.after(10 if self.session_active else 100, self.poll_queues)

    def begin_countdown(self) -> None:
        if not self.session_active:
            return
        self.countdown_value = COUNTDOWN_SECONDS
        self.show_countdown()

    def show_countdown(self) -> None:
        self.set_shared_context("countdown", None, {})
        self.canvas.delete("all")
        self.canvas.configure(background=REST_BACKGROUND)
        if self.countdown_value <= 0:
            self.start_round()
            return
        self.canvas.create_text(
            self.screen_width / 2,
            self.screen_height / 2,
            text=str(self.countdown_value),
            fill="#FFFFFF",
            font=("Segoe UI", 100, "bold"),
        )
        self.countdown_value -= 1
        self.root.after(1000, self.show_countdown)

    def make_round_positions(self) -> dict[int, str]:
        while True:
            shuffled_colors = list(COLORS)
            self.session_random.shuffle(shuffled_colors)
            assignment = {
                position: shuffled_colors[position]["key"]
                for position in range(len(COLORS))
            }
            if self.previous_positions is None or all(
                assignment[position] != color_key
                for color_key, position in self.previous_positions.items()
            ):
                break
        self.previous_positions = {
            color_key: position for position, color_key in assignment.items()
        }
        return assignment

    def start_round(self) -> None:
        if not self.session_active:
            return
        if self.round_number >= ROUNDS:
            self.finish_experiment()
            return
        self.round_number += 1
        self.round_colors = self.make_round_positions()
        width = self.screen_width
        height = self.screen_height
        min_dimension = min(width, height)
        ring_radius = min_dimension * 0.28
        ball_radius = min(62.0, min_dimension * 0.065)
        self.round_positions = []
        for position in range(len(COLORS)):
            angle = -math.pi / 2 + (2 * math.pi * position / len(COLORS))
            center_x = width / 2 + ring_radius * math.cos(angle)
            center_y = height / 2 + ring_radius * math.sin(angle)
            self.round_positions.append((center_x, center_y))
        self.visible_colors = {}
        self.reveal_order = list(range(len(COLORS)))
        self.session_random.shuffle(self.reveal_order)
        self.reveal_index = 0
        self.round_metadata[self.round_number] = {
            "assignment": dict(self.round_colors),
            "started_ns": 0,
            "ended_ns": 0,
            "complete": False,
            "stimuli": [],
            "ball_radius": ball_radius,
            "screen_width_px": width,
            "screen_height_px": height,
        }
        self.canvas.delete("all")
        self.canvas.configure(background=BACKGROUND)
        self.set_shared_context(f"round_{self.round_number}_reveal", self.round_number, {})
        self.reveal_next_circle()

    def reveal_next_circle(self) -> None:
        if not self.session_active:
            return
        position = self.reveal_order[self.reveal_index]
        color_key = self.round_colors[position]
        color = next(item for item in COLORS if item["key"] == color_key)
        center_x, center_y = self.round_positions[position]
        radius = self.round_metadata[self.round_number]["ball_radius"]
        self.visible_colors[color_key] = (center_x, center_y, radius)
        if not self.round_metadata[self.round_number]["started_ns"]:
            self.round_metadata[self.round_number]["started_ns"] = time.perf_counter_ns()
        self.draw_visible_circles()
        self.set_shared_context(
            f"round_{self.round_number}_reveal", self.round_number, self.visible_colors
        )
        self.round_metadata[self.round_number]["stimuli"].append(
            {
                "color": color_key,
                "position": position + 1,
                "reveal_index": self.reveal_index + 1,
                "appearance_ns": time.perf_counter_ns(),
            }
        )
        self.reveal_index += 1
        if self.reveal_index >= len(self.reveal_order):
            # Start the ten-second viewing period as soon as the final circle
            # is drawn, without adding another 250 ms to the stimulus.
            meta = self.round_metadata[self.round_number]
            meta["hold_started_ns"] = time.perf_counter_ns()
            self.set_shared_context(
                f"round_{self.round_number}_display",
                self.round_number,
                self.visible_colors,
            )
            self.root.after(FULL_DISPLAY_MS, self.end_round)
        else:
            self.root.after(REVEAL_INTERVAL_MS, self.reveal_next_circle)

    def draw_visible_circles(self) -> None:
        self.canvas.delete("all")
        self.canvas.configure(background=BACKGROUND)
        for color_key, (center_x, center_y, radius) in self.visible_colors.items():
            color = next(item for item in COLORS if item["key"] == color_key)
            self.canvas.create_oval(
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
                fill=color["hex"],
                outline="#050505",
                width=2,
            )

    def end_round(self) -> None:
        if not self.session_active:
            return
        meta = self.round_metadata[self.round_number]
        meta["ended_ns"] = time.perf_counter_ns()
        meta["complete"] = True
        self.set_shared_context("inter_round", self.round_number, {})
        self.root.after(0, self.start_round)

    def finish_experiment(self) -> None:
        if not self.session_active or self.session_finishing:
            return
        self.session_active = False
        self.session_finishing = True
        self.aborted = False
        self.stop_recording_and_unsubscribe()

    def abort_or_close(self, _event: Any = None) -> None:
        if self.session_active:
            self.session_active = False
            self.session_finishing = True
            self.aborted = True
            self.stop_recording_and_unsubscribe()
        elif self.session_finishing:
            return
        else:
            self.root.destroy()

    def stop_recording_and_unsubscribe(self) -> None:
        with self.context_lock:
            self.recording_enabled = False
            self.shared_phase = "aborted" if self.aborted else "complete"
            self.shared_targets = {}
        if not self.subscription_active:
            self.ui_queue.put(("session_closed", None))
            return

        def worker() -> None:
            error = ""
            try:
                self.tracker.unsubscribe_from(
                    tr.EYETRACKER_GAZE_DATA, self.on_gaze_data
                )
            except Exception as exc:
                error = str(exc)
            self.ui_queue.put(("session_closed", error))

        threading.Thread(target=worker, daemon=True).start()

    def finalize_session(self) -> None:
        # Drain data delivered immediately before unsubscribe completed.
        while True:
            try:
                row = self.gaze_queue.get_nowait()
            except queue.Empty:
                break
            self.session_samples.append(row)
            if self.raw_writer is not None:
                self.raw_writer.writerow({key: row.get(key, "") for key in RAW_FIELDS})
        if self.raw_file is not None:
            self.raw_file.flush()
            self.raw_file.close()
            self.raw_file = None
            self.raw_writer = None
        self.write_summary_files()
        self.canvas.delete("all")
        self.canvas.configure(background=REST_BACKGROUND)
        if self.aborted:
            title = "หยุดการทดลองแล้ว"
            message = "บันทึกข้อมูลที่เก็บได้ก่อนหยุดการทดลอง"
        else:
            title = "การทดลองเสร็จสมบูรณ์"
            message = "บันทึกข้อมูลครบ 4 รอบแล้ว"
        saved = "\n".join(str(path) for path in self.file_paths.values())
        self.canvas.create_text(
            self.screen_width / 2,
            self.screen_height / 2 - 60,
            text=title,
            fill="#FFFFFF",
            font=("Segoe UI", 32, "bold"),
        )
        self.canvas.create_text(
            self.screen_width / 2,
            self.screen_height / 2,
            text=message + "\n\n" + saved + "\n\nกด Esc เพื่อปิด",
            fill="#FFFFFF",
            font=("Segoe UI", 14),
            justify="center",
        )
        self.session_finishing = False
        self.root.bind("<Escape>", lambda _event: self.root.destroy())
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    def write_summary_files(self) -> None:
        summary_rows: list[dict[str, Any]] = []
        event_rows: list[dict[str, Any]] = []
        for round_number, meta in sorted(self.round_metadata.items()):
            rows = [
                row
                for row in self.session_samples
                if row["round"] == round_number
                and str(row["phase"]).startswith(f"round_{round_number}_")
            ]
            stats, events = fixation_and_focus_events(rows)
            colors_with_fixation = [
                key
                for key in stats
                if key != "OFF_AOI" and stats[key]["confirmed_fixations"] > 0
            ]
            top_color = ""
            if colors_with_fixation:
                top_key = max(
                    colors_with_fixation,
                    key=lambda key: (
                        stats[key]["confirmed_fixation_ms"], stats[key]["raw_gaze_ms"]
                    ),
                )
                top_color = next(item["label"] for item in COLORS if item["key"] == top_key)
            position_by_color = {
                color_key: position + 1
                for position, color_key in meta["assignment"].items()
            }
            for color in COLORS:
                color_key = color["key"]
                values = stats[color_key]
                summary_rows.append(
                    {
                        "participant_name": self.participant_name,
                        "round": round_number,
                        "round_complete": meta["complete"],
                        "color": color["label"],
                        "position_clockwise_from_top": position_by_color[color_key],
                        "raw_gaze_ms": round(float(values["raw_gaze_ms"]), 2),
                        "confirmed_fixations": values["confirmed_fixations"],
                        "confirmed_fixation_ms": round(
                            float(values["confirmed_fixation_ms"]), 2
                        ),
                        "longest_fixation_ms": round(
                            float(values["longest_fixation_ms"]), 2
                        ),
                        "brief_glances": values["brief_glances"],
                        "focus_loss_events": values["focus_loss_events"],
                        "most_confirmed_gaze_color_this_round": top_color,
                    }
                )
            off_values = stats["OFF_AOI"]
            summary_rows.append(
                {
                    "participant_name": self.participant_name,
                    "round": round_number,
                    "round_complete": meta["complete"],
                    "color": "นอกวงกลม",
                    "position_clockwise_from_top": "",
                    "raw_gaze_ms": round(float(off_values["raw_gaze_ms"]), 2),
                    "confirmed_fixations": 0,
                    "confirmed_fixation_ms": 0,
                    "longest_fixation_ms": 0,
                    "brief_glances": 0,
                    "focus_loss_events": 0,
                    "most_confirmed_gaze_color_this_round": top_color,
                }
            )
            for event in events:
                event_rows.append(
                    {
                        "participant_name": self.participant_name,
                        "round": round_number,
                        "round_complete": meta["complete"],
                        "event": event["event"],
                        "focus_color": next(
                            (c["label"] for c in COLORS if c["key"] == event["focus_color"]),
                            event["focus_color"],
                        ),
                        "looked_at": event["looked_at"],
                        "duration_ms": round(event["duration_ms"], 2),
                        "session_elapsed_ms": round(
                            (event["local_time_ns"] - self.session_origin_ns) / 1_000_000,
                            2,
                        ),
                    }
                )

        summary_fields = [
            "participant_name",
            "round",
            "round_complete",
            "color",
            "position_clockwise_from_top",
            "raw_gaze_ms",
            "confirmed_fixations",
            "confirmed_fixation_ms",
            "longest_fixation_ms",
            "brief_glances",
            "focus_loss_events",
            "most_confirmed_gaze_color_this_round",
        ]
        event_fields = [
            "participant_name",
            "round",
            "round_complete",
            "event",
            "focus_color",
            "looked_at",
            "duration_ms",
            "session_elapsed_ms",
        ]
        with self.file_paths["summary"].open(
            "w", newline="", encoding="utf-8-sig"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=summary_fields)
            writer.writeheader()
            writer.writerows(summary_rows)
        with self.file_paths["events"].open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=event_fields)
            writer.writeheader()
            writer.writerows(event_rows)
        stimulus_fields = [
            "participant_name",
            "round",
            "round_complete",
            "color",
            "color_hex",
            "position_clockwise_from_top",
            "reveal_order",
            "appearance_elapsed_ms",
            "round_duration_ms",
            "screen_width_px",
            "screen_height_px",
        ]
        with self.file_paths["stimuli"].open(
            "w", newline="", encoding="utf-8-sig"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=stimulus_fields)
            writer.writeheader()
            for round_number, meta in sorted(self.round_metadata.items()):
                for stimulus in meta["stimuli"]:
                    color = next(
                        item for item in COLORS if item["key"] == stimulus["color"]
                    )
                    started_ns = meta["started_ns"]
                    ended_ns = meta["ended_ns"] or time.perf_counter_ns()
                    writer.writerow(
                        {
                            "participant_name": self.participant_name,
                            "round": round_number,
                            "round_complete": meta["complete"],
                            "color": color["label"],
                            "color_hex": color["hex"],
                            "position_clockwise_from_top": stimulus["position"],
                            "reveal_order": stimulus["reveal_index"],
                            "appearance_elapsed_ms": round(
                                (stimulus["appearance_ns"] - self.session_origin_ns)
                                / 1_000_000,
                                2,
                            ),
                            "round_duration_ms": round(
                                (ended_ns - started_ns) / 1_000_000, 2
                            )
                            if started_ns
                            else 0,
                            "screen_width_px": meta["screen_width_px"],
                            "screen_height_px": meta["screen_height_px"],
                        }
                    )


def main() -> None:
    root = tk.Tk()
    ColorAttentionExperiment(root)
    root.mainloop()


if __name__ == "__main__":
    main()
