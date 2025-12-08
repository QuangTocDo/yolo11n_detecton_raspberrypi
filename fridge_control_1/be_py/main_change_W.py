import os
import asyncio
import busio
import websockets
import json
import logging
import time
import board
import adafruit_ahtx0
from typing import Set, List, Optional
from logging.handlers import RotatingFileHandler
from websockets.exceptions import ConnectionClosed
from websockets.server import WebSocketServerProtocol
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# --- CẤU HÌNH LOG ---
LOG_DIR = 'log'
LOG_FILE = 'fridge_controller.log'
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(os.path.join(LOG_DIR, LOG_FILE))
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)

file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, LOG_FILE),
    maxBytes=(5 * 1024 * 1024),
    backupCount=5
)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

# ----------------------

# Cấu hình phần cứng
CHIP_NAME = 'gpiochip1'
BLOCK_RELAY_PIN: int = 268
FAN_RELAY_PIN: int = 226
HUMIDITY_RELAY_PIN: int = 227
GPIO_TIMEOUT = 2.0

READ_INTERVAL = 2

CURRENT_SENSOR_SENSITIVITY = 0.100 # V/A (100mV/A)
LINE_VOLTAGE = 220.0 # Điện áp lưới (V)

# Cấu hình logic điều khiển
RELAY_COOLDOWN_SECONDS = 300 # 5 phút
HUMIDITY_HYSTERESIS_PERCENT = 2.0
FAN_HYSTERESIS_DEGREES = 2.5

FAN_OFF_REQUIRED_READINGS = 3

# --- CẤU HÌNH MỚI: PHÁT HIỆN LỖI CÔNG SUẤT ---
POWER_FAULT_TIMEFRAME_SECONDS = 180 # 3 phút
power_fault_check_start_time: Optional[float] = None
power_fault_reported = False


# --- TRẠNG THÁI TOÀN CỤC CỦA HỆ THỐNG ---
CONNECTED_MONITORS: Set[WebSocketServerProtocol] = set()
last_measured_power_w: float = 0.0 # Công suất đo được gần nhất (Watts)
total_energy_wh: float = 0.0 # Tổng năng lượng đã tiêu thụ (Watt-giờ)


# Trạng thái vật lý
block_relay_is_on = False
fan_relay_is_on = False
humidity_relay_is_on = False
humidity_sensor: Optional[adafruit_ahtx0.AHTx0] = None # <-- BỔ SUNG: Biến cho cảm biến độ ẩm
# Lưu thời điểm relay được TẮT lần cuối
last_deactivation_time = -RELAY_COOLDOWN_SECONDS

# --- BIẾN MỚI CHO CẢM BIẾN CÔNG SUẤT ---
power_sensor_channel: Optional[AnalogIn] = None # Kênh analog của cảm biến
last_measured_power_w: float = 0.0 # Công suất đo được gần nhất (Watts

# Trạng thái logic điều khiển
current_target_temp: Optional[float] = 8.0
current_target_humidity: Optional[float] = 20.0
system_mode = 'IDLE'

def get_rms_current(chan: AnalogIn, samples=200):
    """Đo và tính toán dòng điện hiệu dụng (RMS)."""
    if not chan:
        return 0.0

    # Tính điện áp offset (điểm 0 của cảm biến)
    offset_voltage = 0
    try:
        for _ in range(samples):
            offset_voltage += chan.voltage
        offset_voltage /= samples

        sum_sq_current = 0
        for _ in range(samples):
            sensor_voltage = chan.voltage
            # Chuyển đổi điện áp đọc được sang dòng điện tức thời
            instant_current = (sensor_voltage - offset_voltage) / CURRENT_SENSOR_SENSITIVITY
            sum_sq_current += instant_current ** 2

        mean_sq_current = sum_sq_current / samples
        rms_current = mean_sq_current ** 0.5
        return rms_current
    except Exception as e:
        logging.warning(f"Error reading current sensor: {e}")
        return 0.0

def calculate_power(current_rms: float, line_voltage: float = LINE_VOLTAGE):
    """Ước tính công suất từ dòng RMS."""
    return line_voltage * current_rms

# --- CÁC HÀM ĐIỀU KHIỂN PHẦN CỨNG ---
async def run_gpioset_async(chip: str, line: int, value: int):
    # (Hàm này giữ nguyên)
    command = ['gpioset', chip, f"{line}={value}"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await asyncio.wait_for(proc.communicate(), timeout=GPIO_TIMEOUT)
        if proc.returncode != 0:
            logging.error(f"[GPIO ERROR] Lệnh {' '.join(command)} thất bại")
    except Exception as e:
        logging.error(f"[GPIO EXCEPTION] Lỗi khi chạy gpioset: {e}")

async def send_energy_report_async(total_wh: float):
    """Gửi một tin nhắn báo cáo năng lượng đến Go service."""
    if not CONNECTED_MONITORS:
        logging.warning("Không thể gửi báo cáo năng lượng: không có client nào được kết nối.")
        return

    report_payload = {
        "type": "energy_report",
        "total_wh": round(total_wh, 2)
    }
    message = json.dumps(report_payload)
    logging.info(f"==> GỬI BÁO CÁO NĂNG LƯỢNG TỚI GO SERVICE: {total_wh:.2f} Wh")
    await asyncio.gather(
        *[client.send(message) for client in CONNECTED_MONITORS],
        return_exceptions=True
    )

async def energy_reporting_task():
    """Tác vụ này chạy nền và báo cáo tổng năng lượng tiêu thụ mỗi 5 phút."""
    reporting_interval_seconds = 60

    while True:
        await asyncio.sleep(reporting_interval_seconds)

        # Đọc giá trị tổng năng lượng toàn cục
        total_kwh = total_energy_wh / 1000.0

        # In báo cáo ra log
        logging.info("="*50)
        logging.info(f"BÁO CÁO NĂNG LƯỢNG TIÊU THỤ (TỔNG CỘNG)")
        logging.info(f"==> {total_energy_wh:.2f} Wh")
        logging.info(f"==> {total_kwh:.4f} kWh")
        logging.info("="*50)
        await send_energy_report_async(total_energy_wh)

# --- THAY ĐỔI: HÀM ĐIỀU KHIỂN BLOCK (CÓ COOLDOWN) ---
async def set_block_relay_state(state: bool):
    """Điều khiển relay của block, có áp dụng thời gian nghỉ."""
    global block_relay_is_on, last_deactivation_time

    current_time = time.monotonic()
    if state == block_relay_is_on:
        return

    if state: # Nếu muốn BẬT
        time_since_deactivation = current_time - last_deactivation_time
        if time_since_deactivation < RELAY_COOLDOWN_SECONDS:
            cooldown_remaining = RELAY_COOLDOWN_SECONDS - time_since_deactivation
            logging.warning(f"BỎ QUA LỆNH BẬT BLOCK: Đang trong thời gian nghỉ. Còn lại {cooldown_remaining:.0f}s")
            return
    else: # Nếu muốn TẮT
        last_deactivation_time = current_time

    action = "BẬT" if state else "TẮT"
    logging.info(f"Đang {action} block (cục lạnh) trên chân: {BLOCK_RELAY_PIN}")
    value_to_set = 1 if state else 0
    await run_gpioset_async(CHIP_NAME, BLOCK_RELAY_PIN, value_to_set)
    block_relay_is_on = state

# --- THAY ĐỔI: HÀM ĐIỀU KHIỂN QUẠT (KHÔNG CÓ COOLDOWN) ---
async def set_fan_relay_state(state: bool):
    """Điều khiển relay của quạt."""
    global fan_relay_is_on
    if state == fan_relay_is_on:
        return

    action = "BẬT" if state else "TẮT"
    logging.info(f"Đang {action} quạt trên chân: {FAN_RELAY_PIN}")
    value_to_set = 1 if state else 0
    await run_gpioset_async(CHIP_NAME, FAN_RELAY_PIN, value_to_set)
    fan_relay_is_on = state

async def set_humidity_relay_state(state: bool):
    global humidity_relay_is_on
    if state == humidity_relay_is_on:
        return

    action = "BẬT" if state else "TẮT"
    logging.info(f"Đang {action} relay độ ẩm trên chân: {HUMIDITY_RELAY_PIN}")
    value_to_set = 1 if state else 0
    await run_gpioset_async(CHIP_NAME, HUMIDITY_RELAY_PIN, value_to_set)
    humidity_relay_is_on = state

# --- HÀM GỬI TRẠNG THÁI ---
# --- HÀM GỬI TRẠNG THÁI (ĐÃ SỬA) ---
# --- HÀM GỬI TRẠNG THÁI (ĐÃ SỬA) ---
async def broadcast_status(physical_temp: Optional[float], humidity: Optional[float]):
    """
    SỬA ĐỔI: Hàm này giờ đây nhận các giá trị cảm biến
    làm tham số thay vì tự đọc chúng.
    """
    if not CONNECTED_MONITORS:
        return

    # --- THAY ĐỔI: CẬP NHẬT TRẠNG THÁI RELAY MỚI ---
    status_payload = {
        "type": "status_update",
        # Sử dụng các giá trị được truyền vào:
        "physical_temp_celsius": round(physical_temp, 2) if physical_temp is not None else None,
        "humidity_percent": round(humidity, 2) if humidity is not None else None,
        "target_temp_celsius": current_target_temp,
        "target_humidity_percent": current_target_humidity,
        "system_mode": system_mode,
        "block_relay_on": block_relay_is_on,
        "fan_relay_on": fan_relay_is_on,
        "humidity_relay_on": humidity_relay_is_on,
        "power_consumption_watts": round(last_measured_power_w, 2),
        "cooldown_seconds_remaining": round(max(0, RELAY_COOLDOWN_SECONDS - (time.monotonic() - last_deactivation_time)))
    }
    # --- KẾT THÚC THAY ĐỔI ---
    message = json.dumps(status_payload)
    await asyncio.gather(
        *[client.send(message) for client in CONNECTED_MONITORS],
        return_exceptions=True
    )
# --- HÀM MỚI: GỬI BÁO CÁO LỖI ---
async def send_error_report_async(reason: str):
    """Gửi một tin nhắn báo lỗi đến Go service."""
    if not CONNECTED_MONITORS:
        logging.warning("Không thể gửi báo lỗi: không có client nào được kết nối.")
        return

    error_payload = {
        "type": "system_error",
        "reason": reason
    }
    message = json.dumps(error_payload)
    logging.error(f"!!! GỬI BÁO CÁO LỖI HỆ THỐNG: {reason} !!!")
    await asyncio.gather(
        *[client.send(message) for client in CONNECTED_MONITORS],
        return_exceptions=True
    )

# --- VÒNG LẶP ĐIỀU KHIỂN CHÍNH ---
# Hãy chép và thay thế toàn bộ hàm này
async def control_loop_task():
    global system_mode, last_measured_power_w, total_energy_wh
    global power_fault_check_start_time
    
    # Biến đếm để làm mịn việc tắt quạt
    fan_off_consecutive_readings = 0

    while True:
        await asyncio.sleep(READ_INTERVAL)

        # THAY ĐỔI: Kiểm tra 'humidity_sensor' thay vì 'sensor'
        if current_target_temp is None or humidity_sensor is None:
            if system_mode != 'IDLE':
                logging.info("Chưa có nhiệt độ mục tiêu hoặc cảm biến AHT20 chưa sẵn sàng. Chuyển sang IDLE.")
                system_mode = 'IDLE'
                await set_block_relay_state(False)
                await set_fan_relay_state(False)
            continue

        # --- THAY ĐỔI: Đọc cả nhiệt độ và độ ẩm từ AHT20 ---
        try:
            physical_temp = humidity_sensor.temperature
            humidity = humidity_sensor.relative_humidity
        except Exception as e:
            logging.warning(f"Không đọc được cảm biến AHT20: {e}. Bỏ qua vòng lặp này.")
            # Đặt giá trị là None để logic bên dưới không chạy sai
            physical_temp = None
            humidity = None
            continue # Rất quan trọng: Bỏ qua phần còn lại của vòng lặp nếu đọc lỗi
        # --- KẾT THÚC THAY ĐỔI ---


        # --- Logic điều khiển nhiệt độ ---
        if current_target_temp is not None and physical_temp is not None:

            # --- 1. KHAI BÁO CÁC NGƯỠNG NHIỆT ĐỘ ---
            fan_on_threshold = current_target_temp + FAN_HYSTERESIS_DEGREES
            fan_off_threshold = current_target_temp
            block_off_threshold = current_target_temp - 2.0 # Giữ lại ngưỡng an toàn

            # --- 2. LOGIC ĐIỀU KHIỂN QUẠT (CÓ LÀM MỊN) ---
            if fan_relay_is_on:
                # Nếu quạt đang BẬT, kiểm tra xem có nên TẮT không
                if physical_temp <= fan_off_threshold:
                    fan_off_consecutive_readings += 1
                    logging.info(f"Nhiệt độ dưới ngưỡng. Bộ đếm tắt quạt: {fan_off_consecutive_readings}/{FAN_OFF_REQUIRED_READINGS}")
                    if fan_off_consecutive_readings >= FAN_OFF_REQUIRED_READINGS:
                        system_mode = 'MAINTAINING'
                        await set_fan_relay_state(False)
                        fan_off_consecutive_readings = 0
                else:
                    # Nhiệt độ vẫn còn cao, reset bộ đếm
                    fan_off_consecutive_readings = 0
            else:
                # Nếu quạt đang TẮT, kiểm tra xem có nên BẬT không
                if physical_temp > fan_on_threshold:
                    system_mode = 'FAST_COOLING'
                    await set_fan_relay_state(True)
                fan_off_consecutive_readings = 0

            # --- 3. LOGIC ĐIỀU KHIỂN BLOCK (BẢO VỆ MÁY NÉN) ---
            if fan_relay_is_on and not block_relay_is_on:
                await set_block_relay_state(True)
            elif physical_temp <= block_off_threshold and block_relay_is_on:
                system_mode = 'IDLE_COLD'
                await set_block_relay_state(False)
            elif not fan_relay_is_on and not block_relay_is_on and physical_temp > block_off_threshold:
                system_mode = 'MAINTAINING'
                await set_block_relay_state(True)

        # --- Logic độ ẩm ---
        if humidity is not None and current_target_humidity is not None:
            turn_on_threshold_humidity = current_target_humidity - HUMIDITY_HYSTERESIS_PERCENT
            turn_off_threshold_humidity = current_target_humidity
            if humidity < turn_on_threshold_humidity and not humidity_relay_is_on:
                await set_humidity_relay_state(True)
            elif humidity >= turn_off_threshold_humidity and humidity_relay_is_on:
                await set_humidity_relay_state(False)

        # --- Logic đo công suất ---
        if block_relay_is_on and power_sensor_channel:
            current_rms = get_rms_current(power_sensor_channel)
            last_measured_power_w = calculate_power(current_rms)
            energy_this_interval_wh = (last_measured_power_w * READ_INTERVAL) / 3600.0
            total_energy_wh += energy_this_interval_wh
        else:
            last_measured_power_w = 0.0

        # --- LOGIC KIỂM TRA LỖI CÔNG SUẤT ---
        if block_relay_is_on and last_measured_power_w < 20.0:
            if power_fault_check_start_time is None:
                logging.warning("Phát hiện Block BẬT nhưng công suất < 20W. Bắt đầu theo dõi...")
                power_fault_check_start_time = time.monotonic()
            else:
                elapsed_time = time.monotonic() - power_fault_check_start_time
                if elapsed_time >= POWER_FAULT_TIMEFRAME_SECONDS:
                    await send_error_report_async(
                        f"Lỗi hệ thống: Block đã BẬT trong {POWER_FAULT_TIMEFRAME_SECONDS}s nhưng công suất vẫn < 20W."
                    )
                    logging.info(f"Đã gửi báo cáo lỗi công suất. Reset bộ đếm {POWER_FAULT_TIMEFRAME_SECONDS} giây.")
                    power_fault_check_start_time = time.monotonic()
        else:
            if power_fault_check_start_time is not None:
                logging.info("Tình trạng công suất đã trở lại bình thường. Dừng theo dõi lỗi.")
                power_fault_check_start_time = None
        
        # --- Cập nhật Log ---
        log_message = f"Mode: {system_mode}, "
        if physical_temp is not None:
             log_message += f"Temp={physical_temp:.2f}°C, Target={current_target_temp}°C, "
        else:
             log_message += "Temp=LỖI, "
             
        log_message += f"Block={'ON' if block_relay_is_on else 'OFF'}, Fan={'ON' if fan_relay_is_on else 'OFF'}"
        
        if humidity is not None:
            log_message += f", humidity={humidity:.1f}%"
        else:
            log_message += ", humidity=LỖI"
            
        if block_relay_is_on:
            log_message += f", Power={last_measured_power_w:.1f}W"
        logging.info(log_message)

        # THAY ĐỔI: Gửi đi các giá trị đã được đọc (để khớp với log)
        await broadcast_status(physical_temp, humidity)

# --- BỘ XỬ LÝ KẾT NỐI WEBSOCKET ---
async def handler(websocket: WebSocketServerProtocol):
    # (Hàm này giữ nguyên)
    global current_target_temp ,current_target_humidity

    logging.info(f"Client đã kết nối từ {websocket.remote_address}")
    CONNECTED_MONITORS.add(websocket)

    try:
        async for message in websocket:
            logging.info(f"Đã nhận lệnh từ Go Service: {message}")
            try:
                data = json.loads(message)

                if "temperature" in data:
                    new_target = float(data["temperature"])
                    logging.info(f"==> NHẬN NHIỆT ĐỘ MỤC TIÊU MỚI: {new_target}°C <==")
                    current_target_temp = new_target
                    response = {"status": "success", "message": f"Target temperature updated to {new_target}"}
                    await websocket.send(json.dumps(response))

                if "humidity" in data:
                    new_target_h = float(data["humidity"])
                    logging.info(f"==> NHẬN ĐỘ ẨM MỤC TIÊU MỚI: {new_target_h}% <==")
                    current_target_humidity = new_target_h
                    response = {"status": "success", "message": f"Target humidity updated to {new_target_h}"}
                    await websocket.send(json.dumps(response))

            except (json.JSONDecodeError, ValueError) as e:
                logging.error(f"Lỗi xử lý message: {e}")

    except ConnectionClosed:
        logging.info(f"Client {websocket.remote_address} đã ngắt kết nối.")
    finally:
        CONNECTED_MONITORS.remove(websocket)

# --- CÁC HÀM KHỞI ĐỘNG VÀ DỌN DẸP ---
async def main():
    """
    **HÀM ĐÃ ĐƯỢC CẬP NHẬT**
    Xóa MAX6675, chỉ dùng AHT20 cho cả nhiệt độ và độ ẩm.
    """
    global humidity_sensor, power_sensor_channel # <-- THAY ĐỔI: Xóa 'sensor'
    host = "0.0.0.0"
    port = 8765

    # --- XÓA HOÀN TOÀN KHỐI KHỞI TẠO MAX6675 ---

    # --- CẬP NHẬT: KHỞI TẠO CẢM BIẾN AHT20 ---
    try:
        # Khởi tạo cảm biến với giao diện I2C mặc định
        i2c = board.I2C()
        humidity_sensor = adafruit_ahtx0.AHTx0(i2c)
        logging.info("Khởi tạo cảm biến AHT20 (Nhiệt độ/Độ ẩm) thành công.")
    except Exception as e:
        # THAY ĐỔI: Đây là lỗi nghiêm trọng vì không có cảm biến nhiệt độ
        logging.error(f"!!! LỖI NGHIÊM TRỌNG: KHÔNG THỂ KHỞI TẠO CẢM BIẾN AHT20: {e}.")
        logging.error("!!! Vòng lặp điều khiển sẽ không hoạt động. !!!")
        humidity_sensor = None
    # --- KẾT THÚC CẬP NHẬT ---

    # --- KHỞI TẠO CẢM BIẾN CÔNG SUẤT ADS1115 (Giữ nguyên) ---
    try:
        logging.info("Đang khởi tạo cảm biến công suất ADS1115...")
        i2c_ads = busio.I2C(scl=board.D28, sda=board.D27)
        ads = ADS.ADS1115(i2c_ads, address=0x48)
        ads.gain = 1 
        power_sensor_channel = AnalogIn(ads, ADS.P0)
        logging.info("Khởi tạo cảm biến ADS1115 thành công trên kênh A0.")
    except Exception as e:
        logging.error(f"KHÔNG THỂ KHỞI TẠO CẢM BIẾN ADS1115: {e}. Dữ liệu công suất sẽ không có sẵn.")
        power_sensor_channel = None

    await set_fan_relay_state(False)
    await set_humidity_relay_state(False)
    
    logging.info("KHỞI CHẠY HỆ THỐNG: Bật block (cục lạnh).")
    await set_block_relay_state(True)

    asyncio.create_task(control_loop_task())
    asyncio.create_task(energy_reporting_task())

    async with websockets.serve(handler, host, port):
        logging.info(f"WebSocket server đang lắng nghe Go Service trên ws://{host}:{port}")
        await asyncio.Future()

async def cleanup():
    logging.info("Đang thực hiện dọn dẹp trước khi thoát...")
    await set_block_relay_state(False)
    await set_fan_relay_state(False)
    await set_humidity_relay_state(False)
    # --- XÓA BỎ sensor.close() ---
    logging.info("Tất cả các relay đã được tắt. Tạm biệt!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Đã nhận tín hiệu dừng (Ctrl+C).")
    finally:
        asyncio.run(cleanup())