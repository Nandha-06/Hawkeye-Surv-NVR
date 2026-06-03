---
name: homeassistant-bridge
description: "Home Assistant Bridge — forwards Hawkeye detections and threat triage events to Home Assistant"
version: 1.0.0
icon: assets/icon.png
entry: scripts/bridge.py
deploy: deploy.sh

requirements:
  python: ">=3.9"
  requests: ">=2.25.0"
  websocket-client: ">=1.0.0"
  platforms: ["linux", "macos", "windows"]

parameters:
  - name: auto_start
    label: "Auto Start"
    type: boolean
    default: true
    group: Lifecycle

  - name: hass_url
    label: "Home Assistant URL"
    type: string
    default: "http://homeassistant.local:8123"
    group: Connection

  - name: access_token
    label: "Long-Lived Access Token"
    type: password
    default: ""
    group: Connection

  - name: webhook_id
    label: "Webhook ID"
    type: string
    default: ""
    group: Connection

  - name: trigger_threat_levels
    label: "Trigger Threat Levels"
    type: string
    default: "warning,critical"
    group: Rules

  - name: trigger_classes
    label: "Trigger Object Classes"
    type: string
    default: "person"
    group: Rules

capabilities:
  event_forwarding:
    script: scripts/bridge.py
    description: "Forward filtered local YOLO detections and VLM threat assessments to Home Assistant event bus"
---

# Home Assistant Bridge

Pluggable bridge connecting your SharpAI Hawkeye security platform with Home Assistant.

## Setup & Configuration

Configure the connection in your Hawkeye dashboard:
1. **Home Assistant URL**: The address of your Home Assistant server (e.g. `http://192.168.1.100:8123`).
2. **Access Token** or **Webhook ID**:
   - **REST API (Recommended)**: Create a **Long-Lived Access Token** in your Home Assistant profile page (at the very bottom) and paste it here.
   - **Webhook Trigger**: Create a webhook automation in Home Assistant, copy the Webhook ID, and paste it here.

## How it works

The skill runs in the background, listening to the local Hawkeye Gateway WebSocket stream:
1. When **detections** occur, it evaluates the detected classes. If any match the trigger classes (e.g. `person`), it posts to Home Assistant.
2. When **threat_analysis** occurs (triage from VLM), it evaluates the threat level. If it matches the trigger levels (e.g. `warning`, `critical`), it posts to Home Assistant.
3. Events are published on Home Assistant's event bus under `sharpai_detection`.

## Home Assistant Automation Example

Add this to your Home Assistant `automations.yaml` to receive push notifications on your phone when a threat is identified:

```yaml
- alias: "Hawkeye Threat Alert Notification"
  trigger:
    platform: event
    event_type: sharpai_detection
  condition:
    - condition: template
      value_template: "{{ trigger.event.data.event_type == 'threat_alert' }}"
  action:
    - service: notify.notify
      data:
        title: "Hawkeye Security Alert: {{ trigger.event.data.threat_level | upper }}"
        message: "{{ trigger.event.data.message }} (Camera: {{ trigger.event.data.camera }})"
```
