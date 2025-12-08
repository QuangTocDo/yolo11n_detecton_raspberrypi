package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math"
	"math/big"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/accounts/abi/bind"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/ethclient"
	"github.com/ethereum/go-ethereum/rpc"
	"github.com/gorilla/websocket"
	"github.com/joho/godotenv"
)

// (Structs không đổi)
type Config struct {
	RPCURL          string
	ContractAddress common.Address
	PythonWsURL     string
	ContractABI     abi.ABI
	PrivateKey      *ecdsa.PrivateKey
}

type SensorStatusUpdate struct {
	Type        string   `json:"type"`
	Temperature *float64 `json:"physical_temp_celsius"`
	Humidity    *float64 `json:"humidity_percent"`
	Power       *float64 `json:"power_consumption_watts"`
}

type ErrorPayload struct {
	Type   string `json:"type"`
	Reason string `json:"reason"`
}

type EnergyReportPayload struct {
	Type    string  `json:"type"`
	TotalWh float64 `json:"total_wh"`
}

const (
	sensorHistorySize = 5
)

var (
	wsConn                 *websocket.Conn
	wsMux                  sync.Mutex
	lastContractUpdateTime time.Time
	tempHistory            []float64
	humidityHistory        []float64
	txMux                  sync.Mutex // <-- BIẾN MỚI: Mutex để bảo vệ việc gửi giao dịch
)

const lastBlockFile = "last_block.txt"

func readLastBlock() (uint64, error) {
	data, err := os.ReadFile(lastBlockFile)
	if err != nil {
		// Nếu file không tồn tại, coi như bắt đầu từ đầu (block 0)
		if os.IsNotExist(err) {
			log.Println("File 'last_block.txt' không tìm thấy, sẽ quét từ block hiện tại.")
			return 0, nil
		}
		return 0, err
	}
	block, err := strconv.ParseUint(string(data), 10, 64)
	if err != nil {
		return 0, err
	}
	log.Printf("Đã đọc block cuối cùng được xử lý là: %d", block)
	return block, nil
}

// Ghi số block vừa xử lý vào file
func writeLastBlock(blockNumber uint64) error {
	data := []byte(strconv.FormatUint(blockNumber, 10))
	return os.WriteFile(lastBlockFile, data, 0644)
}

func processLog(vLog types.Log, config *Config) {
	log.Printf("Đang xử lý sự kiện từ block %d, Tx: %s", vLog.BlockNumber, vLog.TxHash.Hex())

	// Lấy định nghĩa các sự kiện
	targetTempSetEvent := config.ContractABI.Events["TargetTemperatureSet"]
	targetHumiditySetEvent := config.ContractABI.Events["TargetHumiditySet"]
	errorEvent := config.ContractABI.Events["SystemErrorOccurred"]

	// Phân loại và xử lý sự kiện
	switch vLog.Topics[0] {
	case targetTempSetEvent.ID:
		handleTargetTempSetLog(vLog, config, &targetTempSetEvent)
	case targetHumiditySetEvent.ID:
		handleTargetHumiditySetLog(vLog, config, &targetHumiditySetEvent)
	case errorEvent.ID:
		log.Println("!!! Đã phát hiện sự kiện SystemErrorOccurred trên blockchain!")
	default:
		// Bỏ qua các log không xác định
		return
	}

	// GHI CHÚ QUAN TRỌNG: Chỉ cập nhật block sau khi đã xử lý xong sự kiện
	if err := writeLastBlock(vLog.BlockNumber); err != nil {
		log.Printf("!!! LỖI NGHIÊM TRỌNG: Không thể ghi block cuối cùng %d: %v", vLog.BlockNumber, err)
	}
}

// (loadConfig và connectToEthereum không đổi)
func loadConfig() (*Config, error) {
	if err := godotenv.Load(); err != nil {
		return nil, fmt.Errorf("error loading .env file: %w", err)
	}
	rpcURL := os.Getenv("RPC_URL")
	contractAddressHex := os.Getenv("CONTRACT_ADDRESS")
	pythonWsURL := os.Getenv("PYTHON_WS_URL")
	privateKeyHex := os.Getenv("PRIVATE_KEY")
	if rpcURL == "" || contractAddressHex == "" || pythonWsURL == "" || privateKeyHex == "" {
		return nil, fmt.Errorf("missing required environment variables (RPC_URL, CONTRACT_ADDRESS, PYTHON_WS_URL, PRIVATE_KEY)")
	}
	privateKey, err := crypto.HexToECDSA(privateKeyHex)
	if err != nil {
		return nil, fmt.Errorf("invalid private key: %w", err)
	}
	abiFile, err := os.Open("abi.json")
	if err != nil {
		return nil, fmt.Errorf("could not open ABI.json: %w", err)
	}
	defer abiFile.Close()
	byteValue, _ := io.ReadAll(abiFile)
	contractABI, err := abi.JSON(strings.NewReader(string(byteValue)))
	if err != nil {
		return nil, fmt.Errorf("could not parse ABI: %w", err)
	}
	return &Config{
		RPCURL:          rpcURL,
		ContractAddress: common.HexToAddress(contractAddressHex),
		PythonWsURL:     pythonWsURL,
		ContractABI:     contractABI,
		PrivateKey:      privateKey,
	}, nil
}

func connectToEthereum(rpcURL string) (*ethclient.Client, error) {
	parsedURL, err := url.Parse(rpcURL)
	if err != nil {
		return nil, fmt.Errorf("invalid RPC URL: %w", err)
	}

	insecureTLSConfig := &tls.Config{InsecureSkipVerify: true}

	switch parsedURL.Scheme {
	case "https", "http":
		transport := &http.Transport{TLSClientConfig: insecureTLSConfig}
		httpClient := &http.Client{Transport: transport}
		rpcClient, err := rpc.DialHTTPWithClient(rpcURL, httpClient)
		if err != nil {
			return nil, err
		}
		return ethclient.NewClient(rpcClient), nil
	case "wss", "ws":
		dialer := websocket.Dialer{
			TLSClientConfig: insecureTLSConfig,
		}
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		rpcClient, err := rpc.DialWebsocketWithDialer(ctx, rpcURL, "", dialer)
		if err != nil {
			return nil, err
		}
		return ethclient.NewClient(rpcClient), nil
	default:
		return nil, fmt.Errorf("unsupported RPC scheme: %s", parsedURL.Scheme)
	}
}

// *** HÀM ĐÃ ĐƯỢC CẬP NHẬT ***
func updateSmartContract(config *Config, temp float64, humidity float64, power float64) {
	txMux.Lock()         // <-- KHÓA MUTEX: Yêu cầu quyền gửi giao dịch, các goroutine khác phải đợi
	defer txMux.Unlock() // <-- MỞ KHÓA KHI HÀM KẾT THÚC: Trả lại quyền cho goroutine khác

	log.Printf("Chuẩn bị cập nhật Smart Contract: Temp Mịn=%.2f, Độ ẩm Mịn=%.2f, Công suất=%.2fW", temp, humidity, power)

	client, err := connectToEthereum(config.RPCURL)
	if err != nil {
		log.Printf("Lỗi kết nối RPC để cập nhật contract: %v", err)
		return
	}
	defer client.Close()

	publicKey := config.PrivateKey.Public()
	publicKeyECDSA, ok := publicKey.(*ecdsa.PublicKey)
	if !ok {
		log.Println("Lỗi: không thể chuyển đổi public key sang kiểu ECDSA")
		return
	}
	fromAddress := crypto.PubkeyToAddress(*publicKeyECDSA)

	// Lấy nonce MỚI NHẤT tại thời điểm thực thi
	nonce, err := client.PendingNonceAt(context.Background(), fromAddress)
	if err != nil {
		log.Printf("Lỗi lấy nonce: %v", err)
		return
	}
	gasPrice, err := client.SuggestGasPrice(context.Background())
	if err != nil {
		log.Printf("Lỗi lấy gas price: %v", err)
		return
	}
	chainID, err := client.ChainID(context.Background())
	if err != nil {
		log.Printf("Lỗi lấy chain ID: %v", err)
		return
	}
	auth, err := bind.NewKeyedTransactorWithChainID(config.PrivateKey, chainID)
	if err != nil {
		log.Printf("Lỗi tạo transactor: %v", err)
		return
	}
	auth.Nonce = big.NewInt(int64(nonce))
	auth.Value = big.NewInt(0)
	auth.GasLimit = uint64(300000)
	auth.GasPrice = gasPrice

	tempInt := new(big.Int).SetInt64(int64(math.Round(temp * 100)))
	humidityInt := new(big.Int).SetUint64(uint64(math.Round(humidity * 100)))
	powerInt := new(big.Int).SetUint64(uint64(math.Round(power * 100)))

	packedData, err := config.ContractABI.Pack("updateSensorData", tempInt, humidityInt, powerInt)
	if err != nil {
		log.Printf("Lỗi đóng gói dữ liệu cho contract: %v", err)
		return
	}
	tx := types.NewTransaction(nonce, config.ContractAddress, auth.Value, auth.GasLimit, auth.GasPrice, packedData)
	signedTx, err := types.SignTx(tx, types.NewEIP155Signer(chainID), config.PrivateKey)
	if err != nil {
		log.Printf("Lỗi ký giao dịch: %v", err)
		return
	}
	err = client.SendTransaction(context.Background(), signedTx)
	if err != nil {
		log.Printf("Lỗi gửi giao dịch: %v", err)
		return
	}
	log.Printf("==> Giao dịch cập nhật dữ liệu cảm biến đã được gửi! Hash: %s", signedTx.Hash().Hex())
}

// *** HÀM ĐÃ ĐƯỢC CẬP NHẬT ***
func reportErrorToSmartContract(config *Config, reason string) {
	txMux.Lock()         // <-- KHÓA MUTEX: Yêu cầu quyền gửi giao dịch, các goroutine khác phải đợi
	defer txMux.Unlock() // <-- MỞ KHÓA KHI HÀM KẾT THÚC: Trả lại quyền cho goroutine khác

	log.Printf("!!! Chuẩn bị báo lỗi lên Smart Contract: %s", reason)

	client, err := connectToEthereum(config.RPCURL)
	if err != nil {
		log.Printf("Lỗi kết nối RPC để báo lỗi: %v", err)
		return
	}
	defer client.Close()

	publicKey := config.PrivateKey.Public()
	publicKeyECDSA, ok := publicKey.(*ecdsa.PublicKey)
	if !ok {
		log.Println("Lỗi: không thể chuyển đổi public key sang kiểu ECDSA")
		return
	}
	fromAddress := crypto.PubkeyToAddress(*publicKeyECDSA)

	// Lấy nonce MỚI NHẤT tại thời điểm thực thi
	nonce, err := client.PendingNonceAt(context.Background(), fromAddress)
	if err != nil {
		log.Printf("Lỗi lấy nonce: %v", err)
		return
	}
	gasPrice, err := client.SuggestGasPrice(context.Background())
	if err != nil {
		log.Printf("Lỗi lấy gas price: %v", err)
		return
	}
	chainID, err := client.ChainID(context.Background())
	if err != nil {
		log.Printf("Lỗi lấy chain ID: %v", err)
		return
	}
	auth, err := bind.NewKeyedTransactorWithChainID(config.PrivateKey, chainID)
	if err != nil {
		log.Printf("Lỗi tạo transactor: %v", err)
		return
	}
	auth.Nonce = big.NewInt(int64(nonce))
	auth.Value = big.NewInt(0)
	auth.GasLimit = uint64(300000)
	auth.GasPrice = gasPrice

	packedData, err := config.ContractABI.Pack("reportError", reason)
	if err != nil {
		log.Printf("Lỗi đóng gói dữ liệu cho hàm reportError: %v", err)
		return
	}

	tx := types.NewTransaction(nonce, config.ContractAddress, auth.Value, auth.GasLimit, auth.GasPrice, packedData)
	signedTx, err := types.SignTx(tx, types.NewEIP155Signer(chainID), config.PrivateKey)
	if err != nil {
		log.Printf("Lỗi ký giao dịch báo lỗi: %v", err)
		return
	}

	err = client.SendTransaction(context.Background(), signedTx)
	if err != nil {
		log.Printf("Lỗi gửi giao dịch báo lỗi: %v", err)
		return
	}
	log.Printf("==> Giao dịch BÁO LỖI đã được gửi! Hash: %s", signedTx.Hash().Hex())
}

func reportEnergyToSmartContract(config *Config, totalWh float64) {
	txMux.Lock()
	defer txMux.Unlock()

	log.Printf("Chuẩn bị báo cáo năng lượng lên Smart Contract: %.2f Wh", totalWh)

	client, err := connectToEthereum(config.RPCURL)
	if err != nil {
		log.Printf("Lỗi kết nối RPC để báo cáo năng lượng: %v", err)
		return
	}
	defer client.Close()

	// Phần lấy key, nonce, gas price... giữ nguyên như các hàm giao dịch khác
	publicKey := config.PrivateKey.Public()
	publicKeyECDSA, ok := publicKey.(*ecdsa.PublicKey)
	if !ok {
		log.Println("Lỗi: không thể chuyển đổi public key sang kiểu ECDSA")
		return
	}
	fromAddress := crypto.PubkeyToAddress(*publicKeyECDSA)
	nonce, err := client.PendingNonceAt(context.Background(), fromAddress)
	if err != nil {
		log.Printf("Lỗi lấy nonce: %v", err)
		return
	}
	gasPrice, err := client.SuggestGasPrice(context.Background())
	if err != nil {
		log.Printf("Lỗi lấy gas price: %v", err)
		return
	}
	chainID, err := client.ChainID(context.Background())
	if err != nil {
		log.Printf("Lỗi lấy chain ID: %v", err)
		return
	}
	auth, err := bind.NewKeyedTransactorWithChainID(config.PrivateKey, chainID)
	if err != nil {
		log.Printf("Lỗi tạo transactor: %v", err)
		return
	}
	auth.Nonce = big.NewInt(int64(nonce))
	auth.Value = big.NewInt(0)
	auth.GasLimit = uint64(300000)
	auth.GasPrice = gasPrice

	// QUAN TRỌNG: Chuyển đổi float64 (ví dụ 76.15) thành big.Int (7615)
	totalWhScaled := new(big.Int).SetInt64(int64(math.Round(totalWh * 100)))

	packedData, err := config.ContractABI.Pack("reportEnergyUsage", totalWhScaled)
	if err != nil {
		log.Printf("Lỗi đóng gói dữ liệu cho hàm reportEnergyUsage: %v", err)
		return
	}

	tx := types.NewTransaction(nonce, config.ContractAddress, auth.Value, auth.GasLimit, auth.GasPrice, packedData)
	signedTx, err := types.SignTx(tx, types.NewEIP155Signer(chainID), config.PrivateKey)
	if err != nil {
		log.Printf("Lỗi ký giao dịch báo cáo năng lượng: %v", err)
		return
	}

	err = client.SendTransaction(context.Background(), signedTx)
	if err != nil {
		log.Printf("Lỗi gửi giao dịch báo cáo năng lượng: %v", err)
		return
	}

	log.Printf("==> Giao dịch BÁO CÁO NĂNG LƯỢNG đã được gửi! Hash: %s", signedTx.Hash().Hex())
}

// (Các hàm còn lại connectToPython, runListener, handleTargetTempSetLog, main không đổi)
func connectToPython(ctx context.Context, config *Config) {
	u, err := url.Parse(config.PythonWsURL)
	if err != nil {
		log.Fatalf("Invalid WebSocket URL: %v", err)
	}
	for {
		select {
		case <-ctx.Done():
			return
		default:
			wsMux.Lock()
			if wsConn != nil {
				wsMux.Unlock()
				time.Sleep(5 * time.Second)
				continue
			}
			wsMux.Unlock()
			log.Printf("Attempting to connect to Python WebSocket server at %s", u.String())
			c, _, err := websocket.DefaultDialer.Dial(u.String(), nil)
			if err != nil {
				log.Printf("Failed to connect to Python service: %v. Retrying in 10 seconds...", err)
				time.Sleep(10 * time.Second)
				continue
			}
			log.Println("Successfully connected to Python WebSocket server.")
			wsMux.Lock()
			wsConn = c
			wsMux.Unlock()

			go func(conn *websocket.Conn) {
				defer func() {
					wsMux.Lock()
					if wsConn == conn {
						wsConn.Close()
						wsConn = nil
					}
					wsMux.Unlock()
				}()
				for {
					_, message, err := conn.ReadMessage()
					if err != nil {
						// Giả định rằng lỗi đọc là do ngắt kết nối
						log.Printf("Ngắt kết nối từ Python service: %v", err)
						return // Thoát khỏi goroutine để thử kết nối lại
					}
					log.Printf(">>> NHẬN RAW TỪ PYTHON: %s", string(message))

					var baseMessage map[string]interface{}
					if err := json.Unmarshal(message, &baseMessage); err != nil {
						log.Printf("Lỗi giải mã JSON từ Python: %v", err)
						continue
					}

					msgType, ok := baseMessage["type"].(string)
					if !ok {
						log.Printf("Tin nhắn từ Python không có trường 'type': %s", message)
						continue
					}

					switch msgType {
					case "status_update":
						var status SensorStatusUpdate
						if err := json.Unmarshal(message, &status); err != nil {
							log.Printf("Lỗi giải mã status_update: %v", err)
							continue
						}

						// Bỏ qua nếu không có dữ liệu nhiệt độ (coi đây là dữ liệu bắt buộc)
						if status.Temperature == nil {
							log.Println("Nhận status_update nhưng thiếu 'physical_temp_celsius', bỏ qua.")
							continue
						}

						// *** BẮT ĐẦU THAY ĐỔI ***

						// 1. Lấy giá trị thô (giá trị thật) trực tiếp
						var rawTemp float64 = *status.Temperature

						var rawHumidity float64 = 0.0 // Giá trị mặc định nếu không có
						if status.Humidity != nil {
							rawHumidity = *status.Humidity
						}

						var rawPower float64 = 0.0 // Giá trị mặc định nếu không có
						if status.Power != nil {
							rawPower = *status.Power
						}

						// 2. XÓA BỎ toàn bộ logic làm mịn (sử dụng tempHistory, humidityHistory)

						// 3. Cập nhật log để chỉ hiển thị giá trị thô
						log.Printf(
							"Nhận status: Temp(Thô:%.2f), Độ ẩm(Thô:%.2f), Công suất(Thô:%.2fW). Chuẩn bị gửi SMC.",
							rawTemp,
							rawHumidity,
							rawPower,
						)

						// 4. GỌI HÀM CẬP NHẬT VỚI GIÁ TRỊ THÔ
						go updateSmartContract(config, rawTemp, rawHumidity, rawPower)

					case "system_error":
						var errorMsg ErrorPayload
						if err := json.Unmarshal(message, &errorMsg); err != nil {
							log.Printf("Lỗi giải mã system_error: %v", err)
							continue
						}
						log.Printf("!!! NHẬN LỖI TỪ PYTHON: %s", errorMsg.Reason)
						go reportErrorToSmartContract(config, errorMsg.Reason)

					case "energy_report":
						var report EnergyReportPayload
						if err := json.Unmarshal(message, &report); err != nil {
							log.Printf("Lỗi giải mã energy_report: %v", err)
							continue
						}
						log.Printf("NHẬN BÁO CÁO NĂNG LƯỢNG TỪ PYTHON: %.2f Wh", report.TotalWh)
						go reportEnergyToSmartContract(config, report.TotalWh)

					default:
						log.Printf("Nhận được message type không xác định từ Python: '%s'", msgType)
					}
				}
			}(c)
		}
	}
}

func runListener(ctx context.Context, config *Config) {
	log.Println("Khởi động trình lắng nghe sự kiện phiên bản NÂNG CẤP (có bắt kịp)...")

	for {
		// Kết nối tới Ethereum node
		client, err := connectToEthereum(config.RPCURL)
		if err != nil {
			log.Printf("Lỗi kết nối RPC: %v. Thử lại sau 15 giây...", err)
			time.Sleep(15 * time.Second)
			continue // Quay lại đầu vòng lặp để thử kết nối lại
		}

		// --- GIAI ĐOẠN 1: BẮT KỊP (CATCH-UP) ---
		lastProcessedBlock, err := readLastBlock()
		if err != nil {
			log.Fatalf("Không thể đọc block cuối cùng: %v", err)
		}

		// Lấy block mới nhất trên chain
		latestBlock, err := client.BlockNumber(ctx)
		if err != nil {
			log.Printf("Lỗi lấy block mới nhất: %v. Thử lại sau 15 giây...", err)
			client.Close()
			time.Sleep(15 * time.Second)
			continue
		}

		// Nếu file last_block chưa có gì, ta sẽ bắt đầu từ block hiện tại để tránh quét toàn bộ lịch sử chain
		if lastProcessedBlock == 0 {
			log.Printf("Lần chạy đầu tiên, đặt block bắt đầu là block hiện tại: %d", latestBlock)
			lastProcessedBlock = latestBlock
			if err := writeLastBlock(lastProcessedBlock); err != nil {
				log.Printf("Lỗi ghi block khởi tạo: %v", err)
			}
		}

		// --- GIAI ĐOẠN 1: BẮT KỊP (CATCH-UP) ---
		// (Các dòng phía trên giữ nguyên)

		// THAY THẾ TOÀN BỘ KHỐI `if` NÀY
		if latestBlock > lastProcessedBlock {
			log.Printf("Phát hiện có %d block bị bỏ lỡ. Bắt đầu quét từ block %d đến %d...",
				latestBlock-lastProcessedBlock, lastProcessedBlock+1, latestBlock)

			const batchSize uint64 = 10000 // Giới hạn của RPC node

			// Vòng lặp để xử lý theo từng đợt (batch)
			for fromBlock := lastProcessedBlock + 1; fromBlock <= latestBlock; fromBlock += batchSize {
				// Tính toán block kết thúc cho đợt này
				toBlock := fromBlock + batchSize - 1
				if toBlock > latestBlock {
					toBlock = latestBlock
				}

				log.Printf("--> Đang quét đợt: từ block %d đến %d", fromBlock, toBlock)

				query := ethereum.FilterQuery{
					FromBlock: new(big.Int).SetUint64(fromBlock),
					ToBlock:   new(big.Int).SetUint64(toBlock),
					Addresses: []common.Address{config.ContractAddress},
				}

				pastLogs, err := client.FilterLogs(ctx, query)
				if err != nil {
					// Nếu có lỗi ở một đợt, chỉ cần log và thử lại từ đầu trong lần lặp tiếp theo
					log.Printf("!!! Lỗi quét logs lịch sử cho đợt (%d-%d): %v. Sẽ thử lại sau 15 giây...", fromBlock, toBlock, err)
					client.Close()
					time.Sleep(15 * time.Second)
					// continue của vòng lặp `for` bên ngoài sẽ tự động chạy lại
					// Chúng ta thoát khỏi vòng lặp batch này và để vòng lặp chính kết nối lại
					goto EndCatchUpLoop // Sử dụng goto để thoát khỏi vòng lặp lồng nhau và để vòng for chính chạy lại
				}

				if len(pastLogs) > 0 {
					log.Printf("    --> Tìm thấy %d sự kiện trong đợt này. Đang xử lý...", len(pastLogs))
					for _, vLog := range pastLogs {
						processLog(vLog, config)
					}
				}
			}
		EndCatchUpLoop: // Nhãn cho goto

			log.Println("Đã xử lý xong tất cả sự kiện lịch sử.")
		} else {
			log.Println("Không có block nào bị bỏ lỡ. Bắt đầu lắng nghe thời gian thực.")
		}

		// --- GIAI ĐOẠN 2: LẮNG NGHE THỜI GIAN THỰC (REAL-TIME) ---
		// (Phần code còn lại của hàm giữ nguyên)

		// --- GIAI ĐOẠN 2: LẮNG NGHE THỜI GIAN THỰC (REAL-TIME) ---
		query := ethereum.FilterQuery{
			Addresses: []common.Address{config.ContractAddress},
			FromBlock: new(big.Int).SetUint64(latestBlock + 1),
		}
		logs := make(chan types.Log)
		sub, err := client.SubscribeFilterLogs(ctx, query, logs)
		if err != nil {
			log.Printf("Lỗi đăng ký logs thời gian thực: %v. Thử lại sau 15 giây...", err)
			client.Close()
			time.Sleep(15 * time.Second)
			continue
		}

		// Vòng lặp lắng nghe thời gian thực
		func() {
			defer sub.Unsubscribe()
			defer client.Close()
			log.Println(">>> Đang lắng nghe các sự kiện mới theo thời gian thực...")
			for {
				select {
				case err := <-sub.Err():
					log.Printf("Kết nối subscription bị lỗi: %v. Sẽ thực hiện kết nối lại và bắt kịp...", err)
					return // Thoát khỏi func này để vòng lặp for bên ngoài chạy lại
				case vLog := <-logs:
					processLog(vLog, config)
				case <-ctx.Done():
					log.Println("Nhận tín hiệu tắt chương trình.")
					return
				}
			}
		}()
	}
}

// *** HÀM ĐÃ ĐƯỢC CẬP NHẬT ***
func handleTargetTempSetLog(vLog types.Log, config *Config, event *abi.Event) {
	type LogTargetTemperatureSet struct {
		NewTargetTemperature *big.Int
	}
	var eventData LogTargetTemperatureSet
	if err := config.ContractABI.UnpackIntoInterface(&eventData, event.Name, vLog.Data); err != nil {
		log.Printf("Error unpacking log data: %v", err)
		return
	}

	// 1. Lấy giá trị thô big.Int (ví dụ: 1200)
	bigIntValue := eventData.NewTargetTemperature
	log.Printf("Detected '%s' event! Raw Target Temp: %s, Tx: %s",
		event.Name, bigIntValue.String(), vLog.TxHash.Hex())

	// 2. Chuyển đổi về số thập phân float64
	floatValue := new(big.Float).SetInt(bigIntValue)
	divisor := big.NewFloat(100.0)
	floatValue.Quo(floatValue, divisor) // Thực hiện phép chia cho 100.0

	// Lấy giá trị cuối cùng (ví dụ: 12.0)
	actualTemp, _ := floatValue.Float64()

	// 3. Tạo payload với giá trị ĐÃ ĐƯỢC CHUYỂN ĐỔI
	payload := map[string]interface{}{
		"temperature": actualTemp, // Gửi đi 12.0 thay vì 1200
	}

	jsonData, err := json.Marshal(payload)
	if err != nil {
		log.Printf("Error marshalling JSON: %v", err)
		return
	}

	wsMux.Lock()
	defer wsMux.Unlock()
	if wsConn == nil {
		log.Println("Cannot send message: WebSocket is not connected.")
		return
	}
	err = wsConn.WriteMessage(websocket.TextMessage, jsonData)
	if err != nil {
		log.Printf("Error sending message via WebSocket: %v", err)
		wsConn.Close()
		wsConn = nil
	} else {
		log.Printf("Successfully sent new target temperature %.2f to Python service.", actualTemp)
	}
}

// *** HÀM ĐÃ ĐƯỢC CẬP NHẬT ***
func handleTargetHumiditySetLog(vLog types.Log, config *Config, event *abi.Event) {
	type LogTargetHumiditySet struct {
		NewTargetHumidity *big.Int
	}
	var eventData LogTargetHumiditySet
	if err := config.ContractABI.UnpackIntoInterface(&eventData, event.Name, vLog.Data); err != nil {
		log.Printf("Error unpacking humidity log data: %v", err)
		return
	}

	// 1. Lấy giá trị thô big.Int
	bigIntValue := eventData.NewTargetHumidity
	log.Printf("Detected '%s' event! Raw Target Humidity: %s, Tx: %s",
		event.Name, bigIntValue.String(), vLog.TxHash.Hex())

	// 2. Chuyển đổi về số thập phân float64
	floatValue := new(big.Float).SetInt(bigIntValue)
	divisor := big.NewFloat(100.0)
	floatValue.Quo(floatValue, divisor)

	actualHumidity, _ := floatValue.Float64()

	// 3. Tạo payload với giá trị ĐÃ ĐƯỢC CHUYỂN ĐỔI
	payload := map[string]interface{}{
		"humidity": actualHumidity,
	}

	jsonData, err := json.Marshal(payload)
	if err != nil {
		log.Printf("Error marshalling JSON for humidity: %v", err)
		return
	}

	wsMux.Lock()
	defer wsMux.Unlock()
	if wsConn == nil {
		log.Println("Cannot send humidity message: WebSocket is not connected.")
		return
	}
	err = wsConn.WriteMessage(websocket.TextMessage, jsonData)
	if err != nil {
		log.Printf("Error sending humidity message via WebSocket: %v", err)
		wsConn.Close()
		wsConn = nil
	} else {
		log.Printf("Successfully sent new target humidity %.2f to Python service.", actualHumidity)
	}
}

func main() {
	config, err := loadConfig()
	if err != nil {
		log.Fatalf("Failed to load configuration: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go connectToPython(ctx, config)
	go runListener(ctx, config)
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	<-sigChan
	log.Println("Shutdown signal received, exiting...")
	cancel()
}
