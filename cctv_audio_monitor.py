#!/usr/bin/env python3
"""Lightweight background monitor for CCTV audio streams."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

import numpy as np


DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHUNK_MS = 500
DEFAULT_HONK_BAND = (300, 800)


@dataclass
class MonitorConfig:
    rtsp_url: str
    threshold_db: float
    calibration_offset: float
    chunk_ms: int
    sample_rate: int
    honk_band: Tuple[int, int]
    honk_ratio_threshold: float
    alert_webhook: Optional[str]
    alert_cooldown_s: float


def build_ffmpeg_command(config: MonitorConfig) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        config.rtsp_url,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(config.sample_rate),
        "-f",
        "s16le",
        "-",
    ]


def compute_decibel(samples: np.ndarray, calibration_offset: float) -> float:
    if samples.size == 0:
        return float("-inf")
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
    if rms <= 0:
        return float("-inf")
    dbfs = 20 * math.log10(rms / 32768.0)
    return dbfs + calibration_offset


def honk_score(samples: np.ndarray, sample_rate: int, band: Tuple[int, int]) -> float:
    if samples.size == 0:
        return 0.0
    window = np.hanning(samples.size)
    spectrum = np.fft.rfft(samples * window)
    power = np.abs(spectrum) ** 2
    freqs = np.fft.rfftfreq(samples.size, 1 / sample_rate)
    band_mask = (freqs >= band[0]) & (freqs <= band[1])
    band_power = power[band_mask].sum()
    total_power = power.sum()
    if total_power <= 0:
        return 0.0
    return float(band_power / total_power)


def chunked(iterable: bytes, chunk_size: int) -> Iterable[bytes]:
    for i in range(0, len(iterable), chunk_size):
        yield iterable[i : i + chunk_size]


def send_alert(webhook: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read()


def monitor_stream(config: MonitorConfig) -> None:
    bytes_per_sample = 2
    chunk_samples = int(config.sample_rate * config.chunk_ms / 1000)
    chunk_bytes = chunk_samples * bytes_per_sample
    cooldown_until = 0.0

    while True:
        process = None
        try:
            process = subprocess.Popen(
                build_ffmpeg_command(config),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if not process.stdout:
                raise RuntimeError("ffmpeg did not provide stdout")

            buffer = b""
            while True:
                chunk = process.stdout.read(chunk_bytes)
                if not chunk:
                    raise RuntimeError("ffmpeg stream ended")
                buffer += chunk
                if len(buffer) < chunk_bytes:
                    continue

                for packet in chunked(buffer, chunk_bytes):
                    if len(packet) < chunk_bytes:
                        buffer = packet
                        break
                    samples = np.frombuffer(packet, dtype=np.int16)
                    db_level = compute_decibel(samples, config.calibration_offset)
                    honk_ratio = honk_score(samples, config.sample_rate, config.honk_band)

                    if (
                        db_level >= config.threshold_db
                        and honk_ratio >= config.honk_ratio_threshold
                        and time.time() >= cooldown_until
                    ):
                        alert_payload = {
                            "event": "car_honk_detected",
                            "decibels": round(db_level, 2),
                            "honk_ratio": round(honk_ratio, 4),
                            "threshold_db": config.threshold_db,
                            "rtsp_url": config.rtsp_url,
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        }
                        print(
                            f"[ALERT] honk detected: {alert_payload}",
                            flush=True,
                        )
                        if config.alert_webhook:
                            send_alert(config.alert_webhook, alert_payload)
                        cooldown_until = time.time() + config.alert_cooldown_s
                else:
                    buffer = b""
        except Exception as exc:
            print(f"[WARN] {exc}. Reconnecting in 5s...", file=sys.stderr)
            time.sleep(5)
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


def parse_honk_band(raw_band: str) -> Tuple[int, int]:
    parts = raw_band.split("-")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("honk band must be like 300-800")
    return int(parts[0]), int(parts[1])


def parse_args() -> MonitorConfig:
    parser = argparse.ArgumentParser(
        description="Monitor CCTV audio and alert on car honks above a decibel threshold.",
    )
    parser.add_argument("--rtsp-url", required=True, help="RTSP/HTTP URL for the CCTV feed")
    parser.add_argument("--threshold-db", type=float, default=60.0, help="Alert threshold in dB SPL")
    parser.add_argument(
        "--calibration-offset",
        type=float,
        default=100.0,
        help="Offset added to dBFS to approximate dB SPL",
    )
    parser.add_argument(
        "--chunk-ms",
        type=int,
        default=DEFAULT_CHUNK_MS,
        help="Audio chunk duration in milliseconds",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=DEFAULT_SAMPLE_RATE,
        help="Audio sample rate used for analysis",
    )
    parser.add_argument(
        "--honk-band",
        type=parse_honk_band,
        default=f"{DEFAULT_HONK_BAND[0]}-{DEFAULT_HONK_BAND[1]}",
        help="Frequency band to detect honks, e.g. 300-800",
    )
    parser.add_argument(
        "--honk-ratio-threshold",
        type=float,
        default=0.25,
        help="Required band power ratio to consider it a honk",
    )
    parser.add_argument(
        "--alert-webhook",
        help="Optional webhook URL to POST JSON alert payloads",
    )
    parser.add_argument(
        "--alert-cooldown-s",
        type=float,
        default=10.0,
        help="Cooldown between alerts in seconds",
    )

    args = parser.parse_args()
    honk_band = args.honk_band
    if isinstance(honk_band, str):
        honk_band = parse_honk_band(honk_band)
    return MonitorConfig(
        rtsp_url=args.rtsp_url,
        threshold_db=args.threshold_db,
        calibration_offset=args.calibration_offset,
        chunk_ms=args.chunk_ms,
        sample_rate=args.sample_rate,
        honk_band=honk_band,
        honk_ratio_threshold=args.honk_ratio_threshold,
        alert_webhook=args.alert_webhook,
        alert_cooldown_s=args.alert_cooldown_s,
    )


def main() -> None:
    config = parse_args()
    monitor_stream(config)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    main()
