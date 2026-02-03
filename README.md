# Decibel-Watch-Background

Lightweight background service for monitoring CCTV audio feeds, estimating decibel levels, and triggering alerts when loud car honks are detected.

## Features
- Connects to a live RTSP/HTTP CCTV feed via `ffmpeg`.
- Continuously measures sound intensity (approximate dB SPL via calibration offset).
- Detects car honks using a frequency-band power ratio.
- Triggers an alert when volume crosses 60 dB *and* honk detection passes the threshold.

## Requirements
- Python 3.9+
- `ffmpeg` available on the host
- Python dependencies:
  ```bash
  pip install -r requirements.txt
  ```

## Usage
```bash
python cctv_audio_monitor.py \
  --rtsp-url rtsp://user:password@camera.example/stream \
  --threshold-db 60 \
  --alert-webhook https://alerts.example/webhook
```

### Tuning for your environment
- **Calibration offset**: `--calibration-offset` converts dBFS to an approximate SPL. You should calibrate once with a known sound source.
- **Honk detection**: `--honk-band 300-800` and `--honk-ratio-threshold 0.25` control the honk classifier.
- **Cooldown**: `--alert-cooldown-s` avoids repeated alerts on the same event.

## Alert payload
Alerts are printed to stdout and optionally sent to the webhook as JSON:
```json
{
  "event": "car_honk_detected",
  "decibels": 66.2,
  "honk_ratio": 0.341,
  "threshold_db": 60.0,
  "rtsp_url": "rtsp://...",
  "timestamp": "2025-01-01T12:00:00Z"
}
```
