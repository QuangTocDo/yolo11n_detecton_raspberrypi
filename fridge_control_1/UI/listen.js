// Import các thư viện cần thiết
import express from "express";
import { ethers } from "ethers";
import { readFile } from "fs/promises";
import { WebSocketServer } from "ws";
import cors from "cors";
import "dotenv/config";
import { setTargetTemperature, setTargetHumidity } from "./send.js";


import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Bỏ qua xác minh TLS (chỉ cho môi trường phát triển)
process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";

// ================== CÀI ĐẶT MÁY CHỦ EXPRESS (ĐÃ CẬP NHẬT) ==================
const app = express();
app.use(express.static(path.join(__dirname, 'public')));

app.use(cors());
app.use(express.json());
const PORT = 3000;

// Endpoint MỚI để thiết lập chỉ nhiệt độ
app.post("/set-temperature", async (req, res) => {
  const { temperature } = req.body;
  console.log(`🔥 Nhận được yêu cầu thiết lập nhiệt độ: ${temperature}°C`);

  try {
    if (temperature === undefined) {
      return res.status(400).send({ message: "Giá trị nhiệt độ là bắt buộc." });
    }
    await setTargetTemperature(temperature.toString());
    res.status(200).send({ message: "Thiết lập nhiệt độ thành công!" });
  } catch (error) {
    console.error("💥 Lỗi khi thiết lập nhiệt độ:", error);
    res.status(500).send({ message: "Lỗi khi gửi giao dịch nhiệt độ." });
  }
});

// Endpoint MỚI để thiết lập chỉ độ ẩm
app.post("/set-humidity", async (req, res) => {
  const { humidity } = req.body;
  console.log(`🔥 Nhận được yêu cầu thiết lập độ ẩm: ${humidity}%`);

  try {
    if (humidity === undefined) {
      return res.status(400).send({ message: "Giá trị độ ẩm là bắt buộc." });
    }
    await setTargetHumidity(humidity.toString());
    res.status(200).send({ message: "Thiết lập độ ẩm thành công!" });
  } catch (error) {
    console.error("💥 Lỗi khi thiết lập độ ẩm:", error);
    res.status(500).send({ message: "Lỗi khi gửi giao dịch độ ẩm." });
  }
});

app.listen(PORT, () => {
  console.log(`🚀 Máy chủ Express đang chạy tại http://localhost:${PORT}`);
});

// ================== CÀI ĐẶT MÁY CHỦ WEBSOCKET ==================
const wss = new WebSocketServer({ port: 8080 });

wss.on("connection", (ws) => {
  console.log("✅ Giao diện người dùng đã kết nối.");
  ws.on("error", console.error);
  ws.on("close", () => {
    console.log("❌ Giao diện người dùng đã ngắt kết nối.");
  });
});

console.log("🚀 Máy chủ WebSocket đang chạy tại địa chỉ ws://localhost:8080");

function broadcast(data) {
  const jsonData = JSON.stringify(data);
  wss.clients.forEach((client) => {
    if (client.readyState === client.OPEN) {
      client.send(jsonData);
    }
  });
}

// ================== LẮNG NGHE SMART CONTRACT (PHIÊN BẢN SỬA LỖI) ==================
let provider;
let contract;
let heartbeatInterval;
let isReconnecting = false;

async function startListening() {
  try {
    console.log("🔌 Đang kết nối tới Ethereum node qua WebSocket...");
    const rpcUrl = process.env.RPC_URL_WS;
    const contractAddress = process.env.CONTRACT_ADDRESS;

    if (!rpcUrl || !contractAddress) {
      throw new Error(
        "Vui lòng điền đầy đủ RPC_URL_WS và CONTRACT_ADDRESS trong file .env"
      );
    }

    const abi = JSON.parse(
      await readFile(new URL("./abi.json", import.meta.url))
    );

    provider = new ethers.WebSocketProvider(rpcUrl);

    await provider.getNetwork();
    console.log("✅ Kết nối WebSocket tới node đã được thiết lập.");

    contract = new ethers.Contract(contractAddress, abi, provider);

    console.log("✅ Đã khởi tạo contract thành công.");
    console.log(
      "\n🎧 Bắt đầu lắng nghe sự kiện 'SensorDataUpdated'...\n-----------------------------------------"
    );

    contract.removeAllListeners("SensorDataUpdated");

    contract.on(
      "SensorDataUpdated",
      (temperature, humidity, power, timestamp) => {
        console.log("🔥 Cập nhật dữ liệu cảm biến mới!");
        const sensorData = {
          type: "sensor_update",
          temperature: temperature.toString(),
          humidity: humidity.toString(),
          power: power.toString(),
          timestamp: Number(timestamp) * 1000,
        };
        console.log(sensorData);
        console.log("📡 Đang phát sóng dữ liệu tới giao diện người dùng...");
        broadcast(sensorData);
      }
    );

    contract.on("SystemErrorOccurred", (reason, timestamp) => {
      console.error(`🚨 Lỗi hệ thống từ Smart Contract: ${reason}`);
      const errorData = {
        type: "system_error", // "type" để UI biết đây là một thông báo lỗi
        message: reason,
        timestamp: Number(timestamp) * 1000,
      };
      console.log(
        "📡 Đang phát sóng thông báo lỗi tới giao diện người dùng..."
      );
      broadcast(errorData);
    });

    contract.on("EnergyReported", (totalEnergyWhScaled, timestamp) => {
      console.log("⚡️ Cập nhật dữ liệu năng lượng!");
      const energyData = {
        type: "energy_update",
        totalEnergy: totalEnergyWhScaled.toString(),
        timestamp: Number(timestamp) * 1000,
      };
      console.log(energyData);
      console.log(
        "📡 Đang phát sóng dữ liệu năng lượng tới giao diện người dùng..."
      );
      broadcast(energyData);
    });

    if (heartbeatInterval) clearInterval(heartbeatInterval);
    heartbeatInterval = setInterval(async () => {
      try {
        await contract.getHistoryCount();
      } catch (err) {
        console.error(
          "❌ Heartbeat: Mất kết nối. Đang khởi tạo quá trình kết nối lại..."
        );
        if (!isReconnecting) reconnect();
      }
    }, 20000);
  } catch (error) {
    console.error("💥 Lỗi trong quá trình khởi tạo kết nối:", error.message);
    if (!isReconnecting) reconnect();
  }
}

function reconnect() {
  isReconnecting = true;
  if (heartbeatInterval) clearInterval(heartbeatInterval);

  if (provider) {
    provider.destroy().catch((e) => {
      console.log(
        "Lưu ý: Không thể hủy provider cũ, có thể nó đã bị đóng rồi."
      );
    });
  }

  console.log("🔄 Sẽ thử kết nối lại sau 5 giây...");
  setTimeout(() => {
    isReconnecting = false;
    startListening();
  }, 5000);
}

console.log(
  "🚀 Khởi chạy chương trình lắng nghe sự kiện tủ lạnh thông minh..."
);
startListening();
