# timestep_logger.py
import csv
import os
import time
from datetime import datetime
from config import ACTIVATE_MINUTES, RESET_AFTER_SECONDS, STABLE_FRAME_COUNT

class TimeStepLogger:
    def __init__(self):
        self.activate_seconds = ACTIVATE_MINUTES * 60
        self.reset_after_seconds = RESET_AFTER_SECONDS
        self.stable_frame_limit = STABLE_FRAME_COUNT

        self.first_detect_time = {}
        self.last_seen_time = {}
        self.activated = {}
        
        self.frame_counts = {}
        self.logged_initial = {}

        os.makedirs("logs", exist_ok=True)

    def _get_csv_file(self, class_name):
        return f"logs/{class_name}.csv"

    def _get_csv_activate_file(self, class_name):
        return f"logs/{class_name}_activated.csv"

    # --- QUAN TRỌNG: HÀM BÙ GIỜ ---
    def handle_pause(self, pause_duration):
        if pause_duration <= 0: return

        # Cập nhật thời điểm lần cuối nhìn thấy
        for cid in self.last_seen_time:
            self.last_seen_time[cid] += pause_duration
        
        # Cập nhật thời điểm bắt đầu
        for cid in self.first_detect_time:
            self.first_detect_time[cid] += pause_duration
            
        print(f"[SYSTEM] Resume: Adjusted timers by +{pause_duration:.2f}s")
    # ------------------------------

    def log_first_detect(self, class_id, class_name, conf):
        now = time.time()

        # ------------------------------------------------------------------
        # Logic Reset nếu mất dấu quá lâu (Object mới hoặc object cũ quay lại)
        # ------------------------------------------------------------------
        if (class_id not in self.last_seen_time) or \
           (now - self.last_seen_time.get(class_id, now)) > self.reset_after_seconds:
            
            # Reset lại các thông số nội bộ
            self.first_detect_time[class_id] = now
            self.activated[class_id] = False
            self.frame_counts[class_id] = 0
            self.logged_initial[class_id] = False

            # [MỚI SỬA] XÓA FILE ACTIVATED NGAY TẠI ĐÂY
            # Vì đã reset (coi là vật thể mới), ta xóa file cũ đi để tránh nhầm lẫn.
            activate_file_path = self._get_csv_activate_file(class_name)
            if os.path.exists(activate_file_path):
                try:
                    os.remove(activate_file_path)
                    print(f"[SYSTEM] Reset & Cleaned old activation file for: {class_name}")
                except OSError as e:
                    print(f"[ERROR] Failed to delete old file: {e}")

        # Cập nhật thời gian nhìn thấy lần cuối
        self.last_seen_time[class_id] = now
        self.frame_counts[class_id] = self.frame_counts.get(class_id, 0) + 1

        # Logic kiểm tra ổn định (Bắt đầu tracking chính thức)
        if self.frame_counts[class_id] >= self.stable_frame_limit and not self.logged_initial.get(class_id, False):
            self.logged_initial[class_id] = True
            self.first_detect_time[class_id] = now # Bắt đầu tính giờ chuẩn từ đây

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self._get_csv_file(class_name), "a", newline="") as f:
                csv.writer(f).writerow([timestamp, class_name, conf, "Tracking Started"])
            print(f"[LOG] {class_name} tracking started.")

    def check_and_log_activation(self, class_id, class_name):
        if not self.logged_initial.get(class_id, False): return False
        if self.activated.get(class_id, False): return True

        now = time.time()
        diff = now - self.first_detect_time.get(class_id, now)

        if diff >= self.activate_seconds:
            self.activated[class_id] = True
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self._get_csv_activate_file(class_name), "a", newline="") as f:
                csv.writer(f).writerow([timestamp, class_name, "ACTIVATED"])
            print(f"[ALERT] {class_name} ACTIVATED")
            return True
        return False

    def get_duration(self, class_id):
        if not self.logged_initial.get(class_id, False): return 0
        return time.time() - self.first_detect_time.get(class_id, time.time())

    def is_activated(self, class_id):
        return self.activated.get(class_id, False)
