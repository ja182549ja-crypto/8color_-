"""Split Tobii gaze results by participant and create Gaze Path PNGs."""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.cm import ScalarMappable
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import Circle
from matplotlib.ticker import FuncFormatter


COLOR_HEX = {
    "red": "#E53935",
    "yellow": "#FDD835",
    "green": "#43A047",
    "orange": "#FB8C00",
    "blue": "#1E88E5",
    "purple": "#8E24AA",
    "white": "#F5F5F5",
}

ROUND_COUNT = 4
MAX_CONNECTED_GAP_NS = 100_000_000
RAW_GAZE_FIELDS = [
    "participant_name",
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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"true", "1", "yes", "valid"}


def as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def screen_size(width_arg: int | None, height_arg: int | None) -> tuple[int, int]:
    if width_arg and height_arg:
        return width_arg, height_arg
    if os.name == "nt":
        try:
            import ctypes

            width = int(ctypes.windll.user32.GetSystemMetrics(0))
            height = int(ctypes.windll.user32.GetSystemMetrics(1))
            if width > 0 and height > 0:
                return width_arg or width, height_arg or height
        except (AttributeError, OSError):
            pass
    return width_arg or 1920, height_arg or 1080


def safe_folder_name(participant_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", participant_id).strip("_") or "participant"


def gather_gaze(data_dir: Path) -> tuple[dict[str, dict[str, Any]], list[Path]]:
    sessions = sorted(path for path in data_dir.glob("*_gaze.csv") if path.is_file())
    if not sessions:
        raise FileNotFoundError(f"ไม่พบไฟล์ *_gaze.csv ใน {data_dir}")

    participants: dict[str, dict[str, Any]] = {}
    for gaze_path in sessions:
        session_id = gaze_path.name.removesuffix("_gaze.csv")
        for row in read_csv(gaze_path):
            name = (row.get("participant_name") or "").strip()
            if not name:
                continue
            key = name.casefold()
            participant = participants.setdefault(
                key,
                {
                    "name": name,
                    "gaze_rows": [],
                    "sessions": set(),
                    "sessions_data": {},
                },
            )
            row["participant_name"] = name
            row["source_session"] = session_id
            participant["gaze_rows"].append(row)
            participant["sessions"].add(session_id)
            participant["sessions_data"].setdefault(session_id, []).append(row)
    return participants, sessions


def load_companion_files(
    data_dir: Path, participant_name: str, session_ids: set[str]
) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {
        "summary": [],
        "events": [],
        "stimuli": [],
    }
    for session_id in sorted(session_ids):
        for kind in result:
            path = data_dir / f"{session_id}_{kind}.csv"
            if not path.exists():
                continue
            for row in read_csv(path):
                if (row.get("participant_name") or "").strip().casefold() != participant_name.casefold():
                    continue
                row["source_session"] = session_id
                result[kind].append(row)
    return result


def make_trial_plot(
    ax: Any,
    rows: list[dict[str, str]],
    stimuli: list[dict[str, str]],
    round_number: int,
    screen_width: int,
    screen_height: int,
) -> tuple[ScalarMappable, int]:
    valid_samples: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: as_int(item.get("local_time_ns")) or 0):
        if not truthy(row.get("gaze_valid")):
            continue
        gaze_x = as_float(row.get("gaze_x"))
        gaze_y = as_float(row.get("gaze_y"))
        local_time_ns = as_int(row.get("local_time_ns"))
        if gaze_x is None or gaze_y is None or local_time_ns is None:
            continue
        valid_samples.append(
            {
                "x": gaze_x * screen_width,
                "y": gaze_y * screen_height,
                "time_ns": local_time_ns,
                "aoi": row.get("aoi", "OFF_AOI"),
            }
        )

    min_dimension = min(screen_width, screen_height)
    ring_radius = min_dimension * 0.28
    ball_radius = min(62.0, min_dimension * 0.065)
    for stimulus in stimuli:
        position = as_int(stimulus.get("position_clockwise_from_top"))
        if position is None or not 1 <= position <= 7:
            continue
        angle = -math.pi / 2 + (2 * math.pi * (position - 1) / 7)
        center_x = screen_width / 2 + ring_radius * math.cos(angle)
        center_y = screen_height / 2 + ring_radius * math.sin(angle)
        color = stimulus.get("color_hex") or "#777777"
        ax.add_patch(
            Circle(
                (center_x, center_y),
                ball_radius,
                facecolor=color,
                edgecolor="#333333",
                linewidth=0.9,
                alpha=0.25,
                zorder=1,
            )
        )
        label_color = "#202020" if color.casefold() in {"#f5f5f5", "#fdd835"} else "#ffffff"
        ax.text(
            center_x,
            center_y,
            str(position),
            ha="center",
            va="center",
            fontsize=8,
            color=label_color,
            fontweight="bold",
            zorder=2,
        )

    if len(valid_samples) > 1:
        start_ns = valid_samples[0]["time_ns"]
        end_ns = valid_samples[-1]["time_ns"]
        total_span_ns = max(1, end_ns - start_ns)
        segments: list[list[tuple[float, float]]] = []
        progress: list[float] = []
        for first, second in zip(valid_samples, valid_samples[1:]):
            gap = second["time_ns"] - first["time_ns"]
            if 0 <= gap <= MAX_CONNECTED_GAP_NS:
                segments.append([(first["x"], first["y"]), (second["x"], second["y"])])
                midpoint = (first["time_ns"] + second["time_ns"]) / 2
                progress.append((midpoint - start_ns) / total_span_ns)
        if segments:
            path = LineCollection(
                segments,
                cmap="plasma",
                norm=Normalize(0, 1),
                linewidths=1.1,
                alpha=0.9,
                zorder=3,
            )
            path.set_array(np.asarray(progress))
            ax.add_collection(path)

        point_groups: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for sample in valid_samples:
            point_groups[sample["aoi"]].append((sample["x"], sample["y"]))
        for aoi, points in point_groups.items():
            color = COLOR_HEX.get(aoi, "#777777")
            ax.scatter(
                [point[0] for point in points],
                [point[1] for point in points],
                s=8,
                c=color,
                alpha=0.7,
                edgecolors="#333333" if aoi == "white" else "#ffffff",
                linewidths=0.25,
                zorder=4,
            )
        ax.scatter(
            [valid_samples[0]["x"]],
            [valid_samples[0]["y"]],
            marker="o",
            s=52,
            c="#2E8B57",
            edgecolors="#ffffff",
            linewidths=0.9,
            zorder=6,
        )
        ax.scatter(
            [valid_samples[-1]["x"]],
            [valid_samples[-1]["y"]],
            marker="X",
            s=56,
            c="#C62828",
            edgecolors="#ffffff",
            linewidths=0.8,
            zorder=6,
        )

    ax.set_xlim(0, screen_width)
    ax.set_ylim(screen_height, 0)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks(np.linspace(0, screen_width, 5))
    ax.set_yticks(np.linspace(0, screen_height, 5))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value / screen_width:.2f}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value / screen_height:.2f}"))
    ax.set_xlabel("Screen X (normalized)")
    ax.set_ylabel("Screen Y (normalized)")
    ax.set_title(f"Round {round_number} · {len(valid_samples):,} valid samples")
    ax.grid(color="#d9d9d9", linewidth=0.5, alpha=0.6)
    ax.set_axisbelow(True)
    sm = ScalarMappable(norm=Normalize(0, 1), cmap="plasma")
    sm.set_array(np.asarray([0, 1]))
    return sm, len(valid_samples)


def create_participant_plot(
    participant_id: str,
    sessions_data: dict[str, list[dict[str, str]]],
    stimuli_by_session_round: dict[tuple[str, int], list[dict[str, str]]],
    output_path: Path,
    default_screen_size: tuple[int, int],
    session_screen_sizes: dict[str, tuple[int, int]],
) -> int:
    trials: list[tuple[str, int, list[dict[str, str]]]] = []
    for session_id, rows in sorted(sessions_data.items()):
        for round_number in range(1, ROUND_COUNT + 1):
            trial_rows = [
                row
                for row in rows
                if str(row.get("round", "")) == str(round_number)
                and str(row.get("phase", "")).startswith(f"round_{round_number}_")
            ]
            if trial_rows:
                trials.append((session_id, round_number, trial_rows))

    if not trials:
        return 0
    columns = 2 if len(trials) > 1 else 1
    rows_count = math.ceil(len(trials) / columns)
    figure, axes = plt.subplots(
        rows_count,
        columns,
        figsize=(8.2 * columns, 4.8 * rows_count),
        squeeze=False,
        facecolor="#ffffff",
    )
    axes_list = list(axes.flat)
    color_mapper: ScalarMappable | None = None
    total_samples = 0
    for index, (session_id, round_number, gaze_rows) in enumerate(trials):
        session_stimuli = stimuli_by_session_round.get((session_id, round_number), [])
        screen_width, screen_height = session_screen_sizes.get(
            session_id, default_screen_size
        )
        color_mapper, valid_count = make_trial_plot(
            axes_list[index],
            gaze_rows,
            session_stimuli,
            round_number,
            screen_width,
            screen_height,
        )
        total_samples += valid_count
        if len(sessions_data) > 1:
            axes_list[index].set_title(
                f"{session_id} · Round {round_number} · {valid_count:,} valid samples"
            )
    for ax in axes_list[len(trials) :]:
        ax.set_visible(False)

    figure.suptitle(f"Gaze Path — {participant_id}", fontsize=15, fontweight="medium")
    legend_items = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor="#2E8B57", markeredgecolor="white", markersize=8, label="Start"),
        Line2D([0], [0], marker="X", color="none", markerfacecolor="#C62828", markeredgecolor="white", markersize=8, label="End"),
    ]
    figure.legend(handles=legend_items, loc="upper center", bbox_to_anchor=(0.5, 0.965), ncol=2, frameon=False)
    figure.text(
        0.5,
        0.012,
        "Target circles show the round layout; path color shows time order. Display size is read from stimulus metadata when available.",
        ha="center",
        fontsize=8,
        color="#555555",
    )
    figure.subplots_adjust(top=0.91, bottom=0.07, right=0.91, hspace=0.34, wspace=0.22)
    if color_mapper is not None:
        colorbar = figure.colorbar(color_mapper, ax=axes_list[: len(trials)], fraction=0.025, pad=0.025)
        colorbar.set_label("Trial time (early → late)")
        colorbar.set_ticks([0, 1])
        colorbar.set_ticklabels(["Start", "End"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=200, bbox_inches="tight", facecolor=figure.get_facecolor())
    plt.close(figure)
    return total_samples


def process(data_dir: Path, output_dir: Path, width_arg: int | None, height_arg: int | None) -> None:
    participants, sessions = gather_gaze(data_dir)
    if not participants:
        raise ValueError("ไม่พบชื่อผู้เข้าร่วมในไฟล์ gaze")
    default_screen_size = screen_size(width_arg, height_arg)

    participant_index: list[dict[str, Any]] = []
    sorted_participants = sorted(participants.items(), key=lambda item: item[1]["name"].casefold())
    for index, (_key, participant) in enumerate(sorted_participants, start=1):
        participant_id = f"participant_{index:03d}"
        folder = output_dir / safe_folder_name(participant_id)
        folder.mkdir(parents=True, exist_ok=True)

        raw_rows: list[dict[str, Any]] = []
        for row in participant["gaze_rows"]:
            raw_rows.append({"source_session": row.get("source_session", ""), **row})
        raw_fields = ["source_session", *RAW_GAZE_FIELDS]
        write_csv(folder / "gaze_raw.csv", raw_rows, raw_fields)

        companions = load_companion_files(data_dir, participant["name"], participant["sessions"])
        for kind, rows in companions.items():
            if kind == "summary":
                original_fields = [
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
            elif kind == "events":
                original_fields = [
                    "participant_name",
                    "round",
                    "round_complete",
                    "event",
                    "focus_color",
                    "looked_at",
                    "duration_ms",
                    "session_elapsed_ms",
                ]
            else:
                original_fields = [
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
            write_csv(folder / f"{kind}.csv", rows, ["source_session", *original_fields])

        stimuli_by_session_round: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)
        for row in companions["stimuli"]:
            round_number = as_int(row.get("round"))
            if round_number is not None:
                stimuli_by_session_round[(row.get("source_session", ""), round_number)].append(row)
        gaze_by_session = participant["sessions_data"]
        session_screen_sizes: dict[str, tuple[int, int]] = {}
        for row in companions["stimuli"]:
            session_id = row.get("source_session", "")
            recorded_width = as_int(row.get("screen_width_px"))
            recorded_height = as_int(row.get("screen_height_px"))
            if (
                session_id
                and recorded_width
                and recorded_height
                and recorded_width > 0
                and recorded_height > 0
            ):
                session_screen_sizes[session_id] = (recorded_width, recorded_height)
        valid_count = create_participant_plot(
            participant_id,
            gaze_by_session,
            stimuli_by_session_round,
            folder / "gaze_path.png",
            default_screen_size,
            session_screen_sizes,
        )
        valid_raw_count = sum(
            truthy(row.get("gaze_valid"))
            and as_float(row.get("gaze_x")) is not None
            and as_float(row.get("gaze_y")) is not None
            for row in participant["gaze_rows"]
        )
        participant_index.append(
            {
                "participant_id": participant_id,
                "participant_name": participant["name"],
                "session_count": len(participant["sessions"]),
                "raw_gaze_rows": len(participant["gaze_rows"]),
                "valid_gaze_samples": valid_raw_count,
                "valid_trial_samples_plotted": valid_count,
            }
        )

    write_csv(
        output_dir / "participant_index.csv",
        participant_index,
        [
            "participant_id",
            "participant_name",
            "session_count",
            "raw_gaze_rows",
            "valid_gaze_samples",
            "valid_trial_samples_plotted",
        ],
    )
    print(f"Participants processed: {len(participants)}")
    print(f"Sessions read: {len(sessions)}")
    print(
        "Fallback display geometry used for older sessions: "
        f"{default_screen_size[0]} x {default_screen_size[1]} px"
    )
    print("Participant exports created under data/participants.")


def main() -> None:
    default_data_dir = Path(__file__).resolve().parent / "data"
    parser = argparse.ArgumentParser(
        description="Separate Tobii gaze results by participant and create Gaze Path PNGs."
    )
    parser.add_argument("--data-dir", type=Path, default=default_data_dir)
    parser.add_argument("--output-dir", type=Path, default=default_data_dir / "participants")
    parser.add_argument("--screen-width", type=int)
    parser.add_argument("--screen-height", type=int)
    args = parser.parse_args()
    process(args.data_dir, args.output_dir, args.screen_width, args.screen_height)


if __name__ == "__main__":
    main()
