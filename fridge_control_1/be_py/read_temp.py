import time
from max6675 import MAX6675

print("Đang khởi tạo kết nối SPI cho MAX6675...")
sensor = MAX6675(bus=1, device=0)

print("=============================================")
print("Bắt đầu đọc nhiệt độ. Nhấn CTRL+C để thoát.")
print("=============================================")

try:
    while True:
        temp_C = sensor.read_temperature()
        if temp_C is None:
            print("Không phát hiện cảm biến!")
        else:
            temp_F = temp_C * 9 / 5 + 32
            print(f"Nhiệt độ: {temp_C:.2f} °C   |   {temp_F:.2f} °F")
        time.sleep(1)

except KeyboardInterrupt:
    sensor.close()
    print("\nChương trình đã dừng.")
