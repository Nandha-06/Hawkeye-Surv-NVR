---
name: visual-event-analyzer
description: "Visual Event Analyzer — performs event-driven visual summaries and custom action alerts using a Visual Language Model (VLM)"
version: 1.0.0
icon: assets/icon.png
entry: scripts/detect.py
deploy: deploy.sh

requirements:
  python: ">=3.9"
  requests: ">=2.25.0"
  platforms: ["linux", "macos", "windows"]

parameters:
  - name: auto_start
    label: "Auto Start"
    type: boolean
    default: true
    group: Lifecycle

  - name: predefined_trigger
    label: "Trigger Type"
    type: select
    options: ["none", "smiling", "thumbs-up", "tongue-out", "peace-sign", "drinking-water", "custom"]
    default: "smiling"
    group: Model

  - name: moondream_api_key
    label: "Moondream API Key"
    type: password
    default: ""
    group: Model

  - name: query_summary
    label: "Visual Summary Prompt"
    type: string
    default: "summarize what you see in one short sentence"
    group: Model

  - name: query_trigger
    label: "Custom Trigger Prompt"
    type: string
    default: "is anyone smiling? yes or no"
    group: Model

  - name: trigger_text
    label: "Custom Trigger Match Text"
    type: string
    default: "yes"
    group: Model

  - name: notification_text
    label: "Custom Alert Message"
    type: string
    default: "😊 Smile Detected!"
    group: Model

  - name: alert_severity
    label: "Alert Severity"
    type: select
    options: ["info", "warning", "critical"]
    default: "warning"
    group: Model

  - name: cooldown
    label: "VLM Cooldown (seconds)"
    type: number
    min: 1
    max: 3600
    default: 5
    group: Performance

  - name: fps
    label: "Processing FPS"
    type: select
    options: [0.2, 0.5, 1, 3, 5]
    default: 1
    group: Performance

capabilities:
  live_detection:
    script: scripts/detect.py
    description: "Real-time webcam/RTSP analysis with continuous visual summaries and custom action alerts using Moondream VLM"
---

# Moondream Live Video Analyzer

This skill is a direct integration of the **Moondream Live Video Player** into Hawkeye. It analyzes live webcam or security camera streams in real time using the Moondream Visual Language Model (VLM).

## Features

- 🎥 **Real-time Camera Analysis**: Continuously narration and action checking of camera feeds.
- 💬 **Live Visual Summaries**: Emits continuous scene descriptions (Query 1) directly to your Hawkeye timeline.
- 🎯 **Predefined Gesture / Action Alerts**: Includes pre-configured triggers for smiling, thumbs up, tongue sticking out, peace signs, and drinking water.
- ⚡ **Custom Triggers**: Compose your own natural language triggers (e.g. *"Is the person wearing a red helmet? yes or no"*) and match rules directly from settings.
- 🔒 **Cloud & Local Support**: Connects to the fast Moondream Cloud API when provided with an API Key, or falls back to your local Hawkeye VLM engine dynamically.

## Predefined Triggers

1. **Smiling**: Checks if anyone in the frame is smiling.
2. **Thumbs Up**: Detects thumbs-up hand gestures.
3. **Sticking Tongue Out**: Fun gesture detector.
4. **Peace Sign**: Detects a peace/victory hand gesture.
5. **Drinking Water**: Perfect for activity/care monitoring.
6. **Custom Trigger**: Compose a custom VLM question, target answer, and alert notification message.
