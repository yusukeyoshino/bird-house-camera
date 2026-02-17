from flask import Flask, Response,jsonify, send_from_directory
from picamera2 import Picamera2
import cv2
import time
import os
import requests
import shutil
import subprocess
import threading
import datetime
from flask_cors import CORS
from collections import deque
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")


app = Flask(__name__)
CORS(app)

IMAGE_DIR = "/mnt/usb/images"
VIDEO_DIR = "/mnt/usb/videos"

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

os.makedirs(IMAGE_DIR, exist_ok=True)
os.makedirs(VIDEO_DIR, exist_ok=True)

FPS = 20
PRE_RECORD_SECONDS = 3
RECORD_SECONDS = 10
COOLDOWN = 20

MOTION_THRESHOLD = 1200
MOTION_FRAMES = 2

frame_buffer = deque(maxlen=PRE_RECORD_SECONDS * FPS)
latest_frame = None
is_recording = False

def send_discord_file(text, file_path):
    try:
        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as f:
                requests.post(
                    DISCORD_WEBHOOK,
                    data={"content": text},
                    files={"file": f}
                )
    except Exception as e:
        print("Discord error:", e)

def convert_to_mp4(avi_path):
    if not os.path.exists(avi_path):
        print("AVI not found:", avi_path)
        return None

    mp4_path = avi_path.replace(".avi", ".mp4")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", avi_path,
        "-vcodec", "libx264",
        "-preset", "veryfast",
        mp4_path
    ]

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if os.path.exists(avi_path):
        os.remove(avi_path)

    return mp4_path

def cleanup_old_videos(folder=VIDEO_DIR, min_free_gb=1):
    total, used, free = shutil.disk_usage("/mnt/usb")
    free_gb = free / (1024**3)

    if free_gb > min_free_gb:
        return

    files = sorted(
        [os.path.join(folder, f) for f in os.listdir(folder)],
        key=os.path.getctime
    )

    for file in files:
        os.remove(file)
        total, used, free = shutil.disk_usage("/mnt/usb")
        free_gb = free / (1024**3)
        if free_gb > min_free_gb:
            break

def record_video(frames, video_name, size):
    global is_recording
    is_recording = True

    avi_name = video_name.replace(".mp4", ".avi")

    writer = cv2.VideoWriter(
        avi_name,
        cv2.VideoWriter_fourcc(*'XVID'),
        FPS,
        size
    )

    for f in frames:
        writer.write(f)

    start_time = time.time()

    while time.time() - start_time < RECORD_SECONDS:
        if latest_frame is not None:
            writer.write(latest_frame.copy())
        time.sleep(1 / FPS)

    writer.release()
    time.sleep(0.5)

    mp4_file = convert_to_mp4(avi_name)

    if mp4_file:
        send_discord_file("Recording finished", mp4_file)

    cleanup_old_videos()
    is_recording = False

picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(
    main={"size": (640, 480)}
))
picam2.start()

prev_gray = None
last_capture_time = 0
motion_counter = 0

def gen_frames():
    global prev_gray, last_capture_time, motion_counter, latest_frame

    while True:
        frame = picam2.capture_array()
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        latest_frame = frame
        frame_buffer.append(frame.copy())

        # motion detection
        small = cv2.resize(frame, (480, 360))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (15, 15), 0)

        if prev_gray is not None:
            diff = cv2.absdiff(prev_gray, gray)
            thresh = cv2.threshold(diff, 20, 255, cv2.THRESH_BINARY)[1]

            thresh = cv2.erode(thresh, None, iterations=1)
            thresh = cv2.dilate(thresh, None, iterations=1)

            motion_pixels = cv2.countNonZero(thresh)

            if motion_pixels > MOTION_THRESHOLD:
                motion_counter += 1
            else:
                motion_counter = 0

            if motion_counter >= MOTION_FRAMES and not is_recording:
                now = time.time()

                if now - last_capture_time > COOLDOWN:
                    print("Motion detected")

                    img_name = f"{IMAGE_DIR}/capture_{int(now)}.jpg"
                    cv2.imwrite(img_name, frame)
                    send_discord_file("Motion detected", img_name)

                    video_name = f"{VIDEO_DIR}/video_{int(now)}.mp4"
                    buffered_frames = list(frame_buffer)
                    size = (frame.shape[1], frame.shape[0])

                    threading.Thread(
                        target=record_video,
                        args=(buffered_frames, video_name, size),
                        daemon=True
                    ).start()

                    last_capture_time = now
                    motion_counter = 0

        prev_gray = gray

        ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        frame_bytes = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' +
               frame_bytes +
               b'\r\n')

@app.route('/video')
def video():
    return Response(gen_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route("/images")
def list_images():
    files = sorted(os.listdir(IMAGE_DIR), reverse=True)

    result = []
    for f in files:
        try:
            ts = int(f.split("_")[1].split(".")[0])
            date = datetime.datetime.fromtimestamp(ts).isoformat()
        except:
            date = None

        result.append({
            "filename": f,
            "timestamp": date
        })

    return jsonify(result)

@app.route("/images/<filename>")
def get_image(filename):
    return send_from_directory(IMAGE_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
