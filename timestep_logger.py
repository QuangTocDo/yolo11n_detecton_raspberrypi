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
        
        # [MỚI] Lưu tên class để dùng cho hàm dọn dẹp tự động
        self.id_to_name = {} 

        os.makedirs("logs", exist_ok=True)

    def _get_csv_file(self, class_name):
        return f"logs/{class_name}.csv"

    def _get_csv_activate_file(self, class_name):
        return f"logs/{class_name}_activated.csv"

    # ... (Giữ nguyên hàm handle_pause) ...
    def handle_pause(self, pause_duration):
        if pause_duration <= 0: return
        for cid in self.last_seen_time:
            self.last_seen_time[cid] += pause_duration
        for cid in self.first_detect_time:
            self.first_detect_time[cid] += pause_duration
        print(f"[SYSTEM] Resume: Adjusted timers by +{pause_duration:.2f}s")

    # --- HÀM MỚI QUAN TRỌNG: TỰ ĐỘNG QUÉT VÀ XÓA LOG KHI BIẾN MẤT ---
    def check_active_timeouts(self):
        """
        Hàm này cần được gọi trong vòng lặp chính (ngoài logic detect).
        Nó kiểm tra các object đang theo dõi, nếu mất tích quá lâu -> Xóa log ngay.
        """
        now = time.time()
        # Tạo list copy các ID đang theo dõi để tránh lỗi khi xóa dictionary trong lúc loop
        current_ids = list(self.last_seen_time.keys())
        delected_item_name = None
        for class_id in current_ids:
            # Nếu thời gian mất dấu vượt quá giới hạn reset
            if (now - self.last_seen_time[class_id]) > self.reset_after_seconds:
                class_name = self.id_to_name.get(class_id, "Unknown")
                
                # 1. Xóa file activated nếu tồn tại
                activate_file_path = self._get_csv_activate_file(class_name)
                if os.path.exists(activate_file_path):
                    try:
                        os.remove(activate_file_path)
                        print(f"[CLEANUP] Object {class_name} gone too long. Deleted activated log.")
                    except OSError as e:
                        print(f"[ERROR] Delete failed: {e}")

                # 2. Xóa sạch dữ liệu trong bộ nhớ (Reset hoàn toàn)
                # Để lần sau xuất hiện sẽ tính là object mới tinh
                self._remove_id_from_memory(class_id)
                delected_item_name  = class_name
        return delected_item_name
    
    def _remove_id_from_memory(self, class_id):
        """Hàm phụ trợ để xóa sạch data của 1 ID"""
        self.first_detect_time.pop(class_id, None)
        self.last_seen_time.pop(class_id, None)
        self.activated.pop(class_id, None)
        self.frame_counts.pop(class_id, None)
        self.logged_initial.pop(class_id, None)
        self.id_to_name.pop(class_id, None)
    # ---------------------------------------------------------------

    def log_first_detect(self, class_id, class_name, conf):
        now = time.time()
        
        # [MỚI] Luôn cập nhật tên class
        self.id_to_name[class_id] = class_name

        # Logic Reset (Giữ lại logic này để xử lý trường hợp quay lại ngay lập tức)
        if (class_id not in self.last_seen_time) or \
           (now - self.last_seen_time.get(class_id, now)) > self.reset_after_seconds:
            
            # Reset lại các thông số
            self.first_detect_time[class_id] = now
            self.activated[class_id] = False
            self.frame_counts[class_id] = 0
            self.logged_initial[class_id] = False

            # Xóa file cũ (Dự phòng)
            activate_file_path = self._get_csv_activate_file(class_name)
            if os.path.exists(activate_file_path):
                os.remove(activate_file_path)
                print(f"[SYSTEM] Reset detected for: {class_name}. Cleaned old file.")

        self.last_seen_time[class_id] = now
        self.frame_counts[class_id] = self.frame_counts.get(class_id, 0) + 1

        if self.frame_counts[class_id] >= self.stable_frame_limit and not self.logged_initial.get(class_id, False):
            self.logged_initial[class_id] = True
            self.first_detect_time[class_id] = now
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self._get_csv_file(class_name), "a", newline="") as f:
                csv.writer(f).writerow([timestamp, class_name, conf, "Tracking Started"])
            print(f"[LOG] {class_name} tracking started.")
            return class_name
    # ... (Các hàm get_duration, check_and_log_activation giữ nguyên) ...
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
