import cv2
from picamera2 import Picamera2
import time
import os

SAVE_DIR = os.path.expanduser("/home/rpi/project/captures")
os.makedirs(SAVE_DIR, exist_ok=True)

picam2 = Picamera2(camera_num=1)
config = picam2.create_preview_configuration(main={"size": (640, 480)})
picam2.configure(config)
picam2.start()

cv2.namedWindow("Camera", cv2.WINDOW_NORMAL)

print(f"Images will be saved to: {SAVE_DIR}")
print("Press 'c' to capture, 'q' to quit.")

try:
    while True:
        frame = picam2.capture_array()
        frame_rgb = cv2.cvtColor(frame,cv2.COLOR_RGB2BGR)
        cv2.imshow("Camera", frame_rgb)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("c"):
            filename = os.path.join(SAVE_DIR, f"capture_{int(time.time())}.jpg")
            cv2.imwrite(filename, frame_rgb)
            print(f"Saved: {filename}")

        elif key == ord("q"):
            break

finally:
    cv2.destroyAllWindows()
    picam2.close()
