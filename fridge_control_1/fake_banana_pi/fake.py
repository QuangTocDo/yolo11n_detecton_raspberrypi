import os
import asyncio
import websockets
import json
import logging
import time
import random
from typing import Set, List, Optional
from logging.handlers import RotatingFileHandler
from websockets.exceptions import ConnectionClosed
from websockets import WebSocketServerProtocol

# --- CẤU HÌNH LOG (LOGGING SETUP) ---
# This part is the same as your original script to ensure consistent logging.
LOG_DIR = 'log'
LOG_FILE = 'fridge_controller.log'
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)
    
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Use RotatingFileHandler to prevent log files from getting too large
file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, LOG_FILE), 
    maxBytes=(5 * 1024 * 1024), 
    backupCount=5
)
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)

# Also log to console for real-time monitoring
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

# ----------------------

# --- CONFIGURATION ---
# These values are kept from the original for logical consistency.
READ_INTERVAL = 3 # Check temperature every 3 seconds
RELAY_COOLDOWN_SECONDS = 300 # 5 minutes cooldown

# --- GLOBAL SYSTEM STATE ---
CONNECTED_MONITORS: Set[WebSocketServerProtocol] = set()

# --- SIMULATED PHYSICAL STATE ---
# INSTEAD of reading from real sensors, we simulate the state with these variables.
simulated_temp_celsius = 25.0  # Start at room temperature
simulated_humidity_percent = 55.0
relay_is_on = False
last_deactivation_time = -RELAY_COOLDOWN_SECONDS # Initialize to allow immediate start

# --- LOGICAL CONTROL STATE ---
current_target_temp: Optional[float] = None # Will be set by the Go service
system_mode = 'IDLE'

# --- FAKE HARDWARE CONTROL FUNCTIONS ---

async def set_relays_state(state: bool):
    """
    SIMULATED version of set_relays_state.
    Instead of calling gpioset, this function just updates the global `relay_is_on`
    variable and prints a log message. The cooldown logic is preserved.
    """
    global relay_is_on, last_deactivation_time
    
    current_time = time.monotonic()
    
    if state == relay_is_on:
        return # No change needed

    if state: # If we are trying to turn the relay ON
        time_since_deactivation = current_time - last_deactivation_time
        if time_since_deactivation < RELAY_COOLDOWN_SECONDS:
            cooldown_remaining = RELAY_COOLDOWN_SECONDS - time_since_deactivation
            logging.warning(f"SIMULATOR: IGNORE TURN ON COMMAND. Relay is in cooldown. {cooldown_remaining:.0f}s remaining.")
            return 
    else: # If we are turning the relay OFF
        last_deactivation_time = current_time

    action = "ON" if state else "OFF"
    logging.info(f"SIMULATOR: Turning relay {action}.")
    relay_is_on = state

# --- STATUS REPORTING FUNCTION ---

async def broadcast_status():
    """
    Sends the current simulated system status to all connected Go clients.
    The JSON structure is identical to the original script.
    """
    if not CONNECTED_MONITORS:
        return
    
    status_payload = {
        "type": "status_update",
        "physical_temp_celsius": round(simulated_temp_celsius, 2),
        "humidity_percent": round(simulated_humidity_percent, 2),
        "target_temp_celsius": current_target_temp,
        "system_mode": system_mode,
        "relay_on": relay_is_on,
        "cooldown_seconds_remaining": round(max(0, RELAY_COOLDOWN_SECONDS - (time.monotonic() - last_deactivation_time)))
    }
    message = json.dumps(status_payload)
    # Send the status to all connected clients (usually just the one Go service)
    await asyncio.gather(
        *[client.send(message) for client in CONNECTED_MONITORS],
        return_exceptions=True
    )

# --- MAIN SIMULATION AND CONTROL LOOP ---

async def control_loop_task():
    """
    This is the core of the simulation. It replaces reading from sensors
    with logic that modifies the simulated temperature based on the relay state.
    """
    global system_mode, simulated_temp_celsius, simulated_humidity_percent

    while True:
        await asyncio.sleep(READ_INTERVAL)

        # --- SIMULATION LOGIC ---
        # If the relay is ON (cooling), temperature should drop.
        if relay_is_on:
            # Decrease temperature by a small random amount
            simulated_temp_celsius -= random.uniform(0.3, 0.8)
        # If the relay is OFF, temperature should slowly rise towards room temp.
        else:
            if simulated_temp_celsius < 25.0: # Assume room temp is 25°C
                simulated_temp_celsius += random.uniform(0.1, 0.4)
        
        # Simulate small fluctuations in humidity
        simulated_humidity_percent += random.uniform(-0.5, 0.5)
        simulated_humidity_percent = max(40.0, min(70.0, simulated_humidity_percent)) # Clamp between 40-70%
        # --- END SIMULATION LOGIC ---

        if current_target_temp is None:
            if system_mode != 'IDLE':
                logging.info("No target temperature set. Switching to IDLE mode.")
                system_mode = 'IDLE'
                await set_relays_state(False)
            continue # Wait until a target is set
        
        if system_mode == 'IDLE':
             system_mode = 'WARMING_UP' # Default state to start comparisons

        logging.info(
            f"Check: Temp={simulated_temp_celsius:.2f}°C, Humidity={simulated_humidity_percent:.2f}%, Target={current_target_temp}°C, Mode={system_mode}, Relay={'ON' if relay_is_on else 'OFF'}"
        )

        # The control logic is identical to the original script
        turn_off_threshold = current_target_temp - 2.0
        turn_on_threshold = current_target_temp 

        if simulated_temp_celsius > turn_on_threshold and not relay_is_on:
             logging.info(f"Temperature ({simulated_temp_celsius:.2f}°C) is above target ({turn_on_threshold}°C). Starting cooling.")
             system_mode = 'COOLING'
             await set_relays_state(True)
        elif simulated_temp_celsius <= turn_off_threshold and relay_is_on:
             logging.info(f"Temperature ({simulated_temp_celsius:.2f}°C) reached turn-off threshold ({turn_off_threshold}°C). Stopping cooling.")
             system_mode = 'WARMING_UP'
             await set_relays_state(False)

        await broadcast_status()

# --- WEBSOCKET CONNECTION HANDLER ---

async def handler(websocket: WebSocketServerProtocol):
    """
    Handles incoming connections from the Go service. This is identical
    to the original script.
    """
    global current_target_temp
    
    logging.info(f"Go service connected from {websocket.remote_address}")
    CONNECTED_MONITORS.add(websocket)
    await broadcast_status() # Send initial status on connect

    try:
        async for message in websocket:
            logging.info(f"Received command from Go Service: {message}")
            try:
                data = json.loads(message)
                
                # The Go service sends the temperature from the smart contract
                if "temperature" in data:
                    # The value from the smart contract might be a large integer (e.g., 1500 for 15.00)
                    # We need to convert it back to a float.
                    new_target = float(data["temperature"]) / 100.0 if isinstance(data["temperature"], int) else float(data["temperature"])
                    
                    logging.info(f"==> NEW TARGET TEMPERATURE RECEIVED: {new_target}°C <==")
                    
                    current_target_temp = new_target
                    
                    # Send a confirmation response back to the Go service
                    response = {"status": "success", "message": f"Target temperature updated to {new_target}"}
                    await websocket.send(json.dumps(response))
                    await broadcast_status() # Immediately send an updated status

            except (json.JSONDecodeError, ValueError) as e:
                logging.error(f"Error processing message from Go service: {e}")

    except ConnectionClosed:
        logging.info(f"Go service {websocket.remote_address} disconnected.")
    finally:
        CONNECTED_MONITORS.remove(websocket)

# --- STARTUP AND CLEANUP ---

async def main():
    host = "0.0.0.0"
    port = 8765

    logging.info("Starting FAKE hardware simulator. NO real hardware will be used.")
    
    # Ensure relay is off at the start
    await set_relays_state(False)

    # Start the main simulation loop as a background task
    asyncio.create_task(control_loop_task())

    # Start the WebSocket server to listen for the Go service
    async with websockets.serve(handler, host, port):
        logging.info(f"WebSocket server listening for Go Service on ws://{host}:{port}")
        await asyncio.Future() # Run forever

async def cleanup():
    logging.info("Shutting down simulator...")
    await set_relays_state(False)
    logging.info("Simulator relay turned off. Goodbye!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Shutdown signal received (Ctrl+C).")
    finally:
        # This cleanup is useful to ensure the final state is logged
        # when you stop the script manually.
        asyncio.run(cleanup())
