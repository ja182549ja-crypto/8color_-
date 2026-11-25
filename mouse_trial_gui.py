"""Mouse-based Tobii experiment simulator for local development and practice.

This program does not connect to an eye tracker. Mouse pointer coordinates are
written as gaze-compatible samples and must not be treated as research data.
"""

from __future__ import annotations

import csv
import math
import random
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from typing import Any

from tobii_spark_color_attention import (
    COLORS,
    COUNTDOWN_SECONDS,
    FULL_DISPLAY_MS,
    REVEAL_INTERVAL_MS,
    ROUNDS,
    circle_for_gaze,
    fixation_and_focus_events,
)

BACKGROUND = "#181818"
REST_BACKGROUND = "#808080"
SAMPLE_INTERVAL_MS = 16

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

SUMMARY_FIELDS = [
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

EVENT_FIELDS = [
    "participant_name",
    "round",
    "round_complete",
    "event",
    "focus_color",
    "looked_at",
    "duration_ms",
    "session_elapsed_ms",
]

STIMULUS_FIELDS = [
    "participant_name",
    "source_device",
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


class MouseTrialApp:
    def __init__(self, root: tk.Tk, fast: bool = False) -> None:
        self.root = root
        self.fast = fast
        self.rest_ms = 1_000 if fast else 5_000
        self.countdown_step_ms = 350 if fast else 1_000
        self.reveal_interval_ms = 100 if fast else REVEAL_INTERVAL_MS
        self.full_display_ms = 3_000 if fast else FULL_DISPLAY_MS

        self.root.title("Mouse Gaze Simulator - Tobii Spark Practice")
        self.root.attributes("-fullscreen", True)
        self.root.configure(background=REST_BACKGROUND)
        self.root.protocol("WM_DELETE_WINDOW", self.abort_or_close)
        self.root.bind("<Escape>", self.abort_or_close)

        self.canvas = tk.Canvas(root, background=REST_BACKGROUND, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.on_canvas_resize)

        self.screen_width = max(1, root.winfo_screenwidth())
        self.screen_height = max(1, root.winfo_screenheight())
        self.participant_name = ""
        self.session_origin_ns = 0
        self.session_active = False
        self.finishing = False
        self.aborted = False
        self.phase = "setup"
        self.round_number = 0
        self.round_metadata: dict[int, dict[str, Any]] = {}
        self.session_samples: list[dict[str, Any]] = []
        self.visible_colors: dict[str, tuple[float, float, float]] = {}
        self.round_colors: dict[int, str] = {}
        self.round_positions: list[tuple[float, float]] = []
        self.previous_positions: dict[str, int] | None = None
        self.reveal_order: list[int] = []
        self.reveal_index = 0
        self.countdown_value = COUNTDOWN_SECONDS
        self.session_random = random.SystemRandom()
        self.raw_file = None
        self.raw_writer = None
        self.file_paths: dict[str, Path] = {}
        self.last_flush_time = time.monotonic()
        self.name_entry: ttk.Entry | None = None
        self.status_label: ttk.Label | None = None
        self.start_button: ttk.Button | None = None
        self.status_text = ""

        self.draw_setup()

    def on_canvas_resize(self, event: tk.Event) -> None:
        self.screen_width = max(1, event.width)
        self.screen_height = max(1, event.height)

    def draw_setup(self) -> None:
        self.canvas.delete("all")
        self.canvas.configure(background=REST_BACKGROUND)
        panel = tk.Frame(self.canvas, background="#F4F4F4", padx=38, pady=28)
        tk.Label(
            panel,
            text="โหมดจำลองด้วยเมาส์",
            font=("Segoe UI", 22, "bold"),
            background="#F4F4F4",
            foreground="#222222",
        ).pack(pady=(0, 8))
        tk.Label(
            panel,
            text="เลื่อน pointer ไปยังตำแหน่งที่ต้องการจำลองการมอง",
            font=("Segoe UI", 12),
            background="#F4F4F4",
            foreground="#333333",
        ).pack(pady=(0, 12))
        tk.Label(
            panel,
            text="โหมดทดสอบเท่านั้น — ไม่เชื่อม Tobii และไม่ใช่ข้อมูล eye-tracking",
            font=("Segoe UI", 11, "bold"),
            background="#F4F4F4",
            foreground="#A32626",
        ).pack(pady=(0, 16))
        tk.Label(
            panel,
            text="ชื่อผู้ทดลอง / รหัสผู้ทดลอง",
            font=("Segoe UI", 11),
            background="#F4F4F4",
            foreground="#222222",
            anchor="w",
        ).pack(fill="x")
        self.name_entry = ttk.Entry(panel, width=36, font=("Segoe UI", 12))
        self.name_entry.pack(fill="x", pady=(5, 12))
        self.start_button = ttk.Button(
            panel, text="เริ่มทดลองด้วยเมาส์", command=self.start_experiment
        )
        self.start_button.pack()
        mode_label = "โหมดเร็ว" if self.fast else "ระยะเวลาจริง"
        tk.Label(
            panel,
            text=f"รูปแบบเวลา: {mode_label}  |  กด Esc เพื่อหยุดและบันทึก",
            font=("Segoe UI", 9),
            background="#F4F4F4",
            foreground="#666666",
        ).pack(pady=(12, 0))
        self.status_label = ttk.Label(
            panel,
            text=self.status_text,
            font=("Segoe UI", 10),
            background="#F4F4F4",
            foreground="#333333",
            wraplength=520,
        )
        self.status_label.pack(pady=(10, 0))
        self.canvas.create_window(
            self.screen_width / 2,
            self.screen_height / 2,
            window=panel,
            anchor="center",
        )
        self.name_entry.focus_set()

    def set_status(self, message: str) -> None:
        self.status_text = message
        if self.status_label is not None:
            self.status_label.configure(text=message)

    def prepare_files(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(__file__).resolve().parent / "data"
        output_dir.mkdir(parents=True, exist_ok=True)
        base = f"mouse_color_{stamp}"
        suffix = 1
        while any(output_dir.glob(f"{base}*")):
            base = f"mouse_color_{stamp}_{suffix:02d}"
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

    def start_experiment(self) -> None:
        if self.name_entry is None:
            return
        self.participant_name = self.name_entry.get().strip()
        if not self.participant_name:
            self.set_status("กรุณากรอกชื่อหรือรหัสผู้ทดลอง")
            return
        if self.start_button is not None:
            self.start_button.configure(state="disabled")

        self.prepare_files()
        self.session_origin_ns = time.perf_counter_ns()
        self.session_active = True
        self.phase = "rest"
        self.canvas.delete("all")
        self.canvas.configure(background=REST_BACKGROUND)
        self.root.after(SAMPLE_INTERVAL_MS, self.sample_mouse)
        self.root.after(self.rest_ms, self.begin_countdown)

    def sample_mouse(self) -> None:
        if not self.session_active:
            return
        local_time_ns = time.perf_counter_ns()
        mouse_x = self.root.winfo_pointerx() - self.canvas.winfo_rootx()
        mouse_y = self.root.winfo_pointery() - self.canvas.winfo_rooty()
        valid = 0 <= mouse_x < self.screen_width and 0 <= mouse_y < self.screen_height
        if valid:
            gaze_x = mouse_x / self.screen_width
            gaze_y = mouse_y / self.screen_height
            aoi = circle_for_gaze(
                gaze_x,
                gaze_y,
                self.visible_colors,
                self.screen_width,
                self.screen_height,
            )
        else:
            gaze_x = gaze_y = None
            aoi = ""
        row = {
            "participant_name": self.participant_name,
            "source_device": "mouse_simulation",
            "session_elapsed_ms": (local_time_ns - self.session_origin_ns) / 1_000_000,
            "local_time_ns": local_time_ns,
            "tobii_system_time_stamp": "",
            "phase": self.phase,
            "round": self.round_number if self.round_number else "",
            "gaze_valid": valid,
            "left_x": "",
            "left_y": "",
            "right_x": "",
            "right_y": "",
            "gaze_x": gaze_x if gaze_x is not None else "",
            "gaze_y": gaze_y if gaze_y is not None else "",
            "aoi": aoi,
        }
        self.session_samples.append(row)
        if self.raw_writer is not None:
            self.raw_writer.writerow(row)
        if time.monotonic() - self.last_flush_time >= 1.0 and self.raw_file is not None:
            self.raw_file.flush()
            self.last_flush_time = time.monotonic()
        self.root.after(SAMPLE_INTERVAL_MS, self.sample_mouse)

    def begin_countdown(self) -> None:
        if not self.session_active:
            return
        self.phase = "countdown"
        self.countdown_value = COUNTDOWN_SECONDS
        self.show_countdown()

    def show_countdown(self) -> None:
        if not self.session_active:
            return
        self.canvas.delete("all")
        self.canvas.configure(background=REST_BACKGROUND)
        if self.countdown_value <= 0:
            self.start_round()
            return
        self.phase = "countdown"
        self.canvas.create_text(
            self.screen_width / 2,
            self.screen_height / 2,
            text=str(self.countdown_value),
            fill="#FFFFFF",
            font=("Segoe UI", 100, "bold"),
        )
        self.countdown_value -= 1
        self.root.after(self.countdown_step_ms, self.show_countdown)

    def make_assignment(self) -> dict[int, str]:
        while True:
            shuffled = list(COLORS)
            self.session_random.shuffle(shuffled)
            assignment = {pos: shuffled[pos]["key"] for pos in range(len(COLORS))}
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
        self.phase = f"round_{self.round_number}_reveal"
        self.round_colors = self.make_assignment()
        min_dimension = min(self.screen_width, self.screen_height)
        ring_radius = min_dimension * 0.28
        ball_radius = min(62.0, min_dimension * 0.065)
        self.round_positions = []
        for position in range(len(COLORS)):
            angle = -math.pi / 2 + 2 * math.pi * position / len(COLORS)
            center_x = self.screen_width / 2 + ring_radius * math.cos(angle)
            center_y = self.screen_height / 2 + ring_radius * math.sin(angle)
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
            "screen_width_px": self.screen_width,
            "screen_height_px": self.screen_height,
        }
        self.canvas.delete("all")
        self.canvas.configure(background=BACKGROUND)
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
        meta = self.round_metadata[self.round_number]
        if not meta["started_ns"]:
            meta["started_ns"] = time.perf_counter_ns()
        self.draw_visible_circles()
        self.phase = f"round_{self.round_number}_reveal"
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
            self.phase = f"round_{self.round_number}_display"
            self.root.after(self.full_display_ms, self.end_round)
        else:
            self.root.after(self.reveal_interval_ms, self.reveal_next_circle)

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
        self.phase = "inter_round"
        self.visible_colors = {}
        self.root.after(0, self.start_round)

    def finish_experiment(self) -> None:
        if not self.session_active or self.finishing:
            return
        self.session_active = False
        self.finishing = True
        self.aborted = False
        self.finalize_session()

    def abort_or_close(self, _event: Any = None) -> None:
        if self.session_active:
            self.session_active = False
            self.finishing = True
            self.aborted = True
            self.finalize_session()
        elif self.finishing:
            return
        else:
            self.root.destroy()

    def finalize_session(self) -> None:
        if self.raw_file is not None:
            self.raw_file.flush()
            self.raw_file.close()
            self.raw_file = None
            self.raw_writer = None
        self.write_summary_files()
        self.phase = "complete" if not self.aborted else "aborted"
        self.canvas.delete("all")
        self.canvas.configure(background=REST_BACKGROUND)
        title = "หยุดการทดสอบแล้ว" if self.aborted else "ทดสอบครบ 4 รอบแล้ว"
        lines = "\n".join(str(path) for path in self.file_paths.values())
        self.canvas.create_text(
            self.screen_width / 2,
            self.screen_height / 2 - 75,
            text=title,
            fill="#FFFFFF",
            font=("Segoe UI", 30, "bold"),
        )
        self.canvas.create_text(
            self.screen_width / 2,
            self.screen_height / 2 + 10,
            text=(
                "ข้อมูลจำลองด้วยเมาส์ — ไม่ใช่ข้อมูล eye-tracking จริง\n\n"
                + lines
                + "\n\nกด Esc เพื่อปิด"
            ),
            fill="#FFFFFF",
            font=("Segoe UI", 13),
            justify="center",
        )
        self.finishing = False
        self.root.bind("<Escape>", lambda _event: self.root.destroy())
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    def write_summary_files(self) -> None:
        summary_rows: list[dict[str, Any]] = []
        event_rows: list[dict[str, Any]] = []
        stimulus_rows: list[dict[str, Any]] = []
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
            top_label = ""
            if colors_with_fixation:
                top_key = max(
                    colors_with_fixation,
                    key=lambda key: (
                        stats[key]["confirmed_fixation_ms"],
                        stats[key]["raw_gaze_ms"],
                    ),
                )
                top_label = next(item["label"] for item in COLORS if item["key"] == top_key)
            position_by_color = {
                color_key: position + 1
                for position, color_key in meta["assignment"].items()
            }
            for color in COLORS:
                values = stats[color["key"]]
                summary_rows.append(
                    {
                        "participant_name": self.participant_name,
                        "round": round_number,
                        "round_complete": meta["complete"],
                        "color": color["label"],
                        "position_clockwise_from_top": position_by_color[color["key"]],
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
                        "most_confirmed_gaze_color_this_round": top_label,
                    }
                )
            off = stats["OFF_AOI"]
            summary_rows.append(
                {
                    "participant_name": self.participant_name,
                    "round": round_number,
                    "round_complete": meta["complete"],
                    "color": "นอกวงกลม",
                    "position_clockwise_from_top": "",
                    "raw_gaze_ms": round(float(off["raw_gaze_ms"]), 2),
                    "confirmed_fixations": 0,
                    "confirmed_fixation_ms": 0,
                    "longest_fixation_ms": 0,
                    "brief_glances": 0,
                    "focus_loss_events": 0,
                    "most_confirmed_gaze_color_this_round": top_label,
                }
            )
            for event in events:
                focus_label = next(
                    (
                        color["label"]
                        for color in COLORS
                        if color["key"] == event["focus_color"]
                    ),
                    event["focus_color"],
                )
                event_rows.append(
                    {
                        "participant_name": self.participant_name,
                        "round": round_number,
                        "round_complete": meta["complete"],
                        "event": event["event"],
                        "focus_color": focus_label,
                        "looked_at": event["looked_at"],
                        "duration_ms": round(event["duration_ms"], 2),
                        "session_elapsed_ms": round(
                            (event["local_time_ns"] - self.session_origin_ns) / 1_000_000,
                            2,
                        ),
                    }
                )
            for stimulus in meta["stimuli"]:
                color = next(
                    item for item in COLORS if item["key"] == stimulus["color"]
                )
                end_ns = meta["ended_ns"] or time.perf_counter_ns()
                stimulus_rows.append(
                    {
                        "participant_name": self.participant_name,
                        "source_device": "mouse_simulation",
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
                            (end_ns - meta["started_ns"]) / 1_000_000, 2
                        ),
                        "screen_width_px": meta["screen_width_px"],
                        "screen_height_px": meta["screen_height_px"],
                    }
                )
        with self.file_paths["summary"].open(
            "w", newline="", encoding="utf-8-sig"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
            writer.writeheader()
            writer.writerows(summary_rows)
        with self.file_paths["events"].open(
            "w", newline="", encoding="utf-8-sig"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=EVENT_FIELDS)
            writer.writeheader()
            writer.writerows(event_rows)
        with self.file_paths["stimuli"].open(
            "w", newline="", encoding="utf-8-sig"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=STIMULUS_FIELDS)
            writer.writeheader()
            writer.writerows(stimulus_rows)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the Tobii color task with mouse movement as simulated gaze."
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Shorten waits for quick UI/code checks; output remains marked as mouse simulation.",
    )
    args = parser.parse_args()
    root = tk.Tk()
    MouseTrialApp(root, fast=args.fast)
    root.mainloop()


if __name__ == "__main__":
    main()
