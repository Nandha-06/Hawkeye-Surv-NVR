---
name: channel-telegram
description: "Telegram messaging channel for Clawdbot agent"
version: 1.0.0

parameters:
  - name: token
    label: "Bot Token"
    type: password
    required: true
    group: Connection
    description: "Telegram Bot Token from BotFather"

  - name: chat_id
    label: "Chat ID"
    type: string
    required: true
    group: Connection
    description: "The Telegram Chat ID to send notifications to"

capabilities:
  messaging_channel:
    script: scripts/channel.py
    description: "Telegram messaging channel"
    supported_actions:
      - send
---

# Telegram Channel

Connect SharpAI Hawkeye's Clawdbot agent to Telegram. Have alerts and chats delivered straight to your mobile device via a Telegram bot.

## Setup

1. Create a bot using [@BotFather](https://t.me/BotFather) on Telegram and retrieve your **Bot Token**.
2. Find your **Chat ID** (for example, message your bot and call the `/getUpdates` endpoint, or use a bot like `@userinfobot`).
3. Enter both parameters under Settings > Services & Integrations, deploy, and start the channel.
