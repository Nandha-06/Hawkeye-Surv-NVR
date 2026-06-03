# Legacy Applications (SharpAI-Hub CLI)

> **Note:** These applications use the `sharpai-cli` Docker-based workflow.
> For the modern experience, use [SharpAI Hawkeye](https://www.sharpai.org) — the desktop companion for Hawkeye.

---

## Application 1: Laptop Screen Monitor (Child Safety)

SharpAI Screen monitor captures screen, extracts image features (embeddings) with AI model, saves unseen features into AI vector database [Milvus](https://milvus.io/), and stores raw images to [Labelstudio](https://labelstud.io) for labeling and model training. All information/images are saved locally.

```bash
sharpai-cli screen_monitor start
```

- Access streaming screen: http://localhost:8000
- Access Labelstudio: http://localhost:8080

---

## Application 2: Person Detector

```bash
sharpai-cli yolov7_person_detector start
```

---

## SharpAI-Hub Application Catalog

SharpAI community is continually working on bringing state-of-the-art computer vision applications to your device.

```bash
sharpai-cli <application name> start
```

| Application | SharpAI CLI Name | OS/Device |
|---|---|---|
| Person Detector | yolov7_person_detector | Jetson Nano/AGX/Windows/Linux/MacOS |
| [Laptop Screen Monitor](https://github.com/SharpAI/laptop_monitor) | screen_monitor | Windows/Linux/MacOS |
| [Parking Lot Monitor](Yolo_Parking.md) | yoloparking | Jetson AGX |

---

## Tested Devices

### Edge AI Devices / Workstation
- [Jetson Nano (ReComputer j1010)](https://www.seeedstudio.com/Jetson-10-1-H0-p-5335.html)
- Jetson Xavier AGX
- MacOS 12.4
- Windows 11
- Ubuntu 20.04

### Tested Cameras
- DaHua / Lorex / AMCREST: URL Path: `/cam/realmonitor?channel=1&subtype=0` Port: `554`
- IP Camera Lite on iOS: URL Path: `/live` Port: `8554`
- Nest Camera indoor/outdoor by Home-Assistant integration

---

## ❓ FAQ

### Installation & Setup
- [How to install Python3](https://www.python.org/downloads)
- [How to install pip3](https://pip.pypa.io/en/stable/installation)
- [How to configure RTSP on GUI](https://github.com/SharpAI/Hawkeye/blob/master/docs/shinobi.md)
- [Camera streaming URL formats](https://shinobi.video)

### Jetson Nano Docker-compose
```bash
sudo apt-get install -y libhdf5-dev python3 python3-pip
pip3 install -U pip
sudo pip3 install docker-compose==1.27.4
```
