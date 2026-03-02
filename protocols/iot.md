# IoT Development Protocol

## Stack
- Device Firmware: C/C++ (ESP32/Arduino) or MicroPython (RPi/ESP32)
- Backend: Python (FastAPI) — telemetry ingestion + device management
- Frontend: TypeScript — monitoring dashboard
- Protocol: MQTT (primary), HTTP (fallback), CoAP (constrained)
- Broker: Mosquitto (local) or AWS IoT Core / HiveMQ (cloud)
- Database: TimescaleDB / InfluxDB (time-series) + PostgreSQL (relational)

## Safety Tiers
- **Simulation** (DEFAULT): All hardware interactions mocked. No real actuators.
- **Monitored**: Live sensor data, simulated actuators. Human monitors.
- **Live**: Real actuators engaged. Requires explicit per-task human confirmation + Safety_Guard sign-off.

## Actuator Lock Protocol
1. Before any actuator test: verify hardware state (query device status)
2. Set safety bounds (max current, max temperature, timeout)
3. Execute with watchdog timer
4. After test: verify hardware returned to safe state
5. Log all actuator state changes

## MQTT Rules
- Topic naming: `{project}/{device_type}/{device_id}/{data_type}`
- QoS levels: 0 (telemetry), 1 (commands), 2 (safety-critical)
- Retained messages for device status
- Last Will and Testament (LWT) for disconnect detection
- TLS encryption mandatory for production
- Per-device authentication (client certificates or username/password)

## Firmware Rules
- OTA update support from day 1
- Watchdog timer on all firmware
- Graceful degradation on network loss (local buffering)
- Configuration via MQTT retained messages (not hardcoded)
- Memory-safe: no dynamic allocation in critical paths
- Power management: sleep modes for battery devices

## Concurrency
- Async everywhere (asyncio for Python backend)
- Message queues for device → backend communication
- No shared mutable state between device handlers
- Race condition testing mandatory
- Timeout on ALL network operations (device may disconnect)

## Testing
- Unit: firmware logic (mocked HAL), backend endpoints
- Integration: MQTT message flow (broker → backend → DB)
- Hardware simulation: virtual devices with realistic timing
- Stress: concurrent device connections (100+ simulated)
- Failover: network drop, broker restart, device crash

## Environment Mocking
- Virtual MQTT broker for tests
- Simulated device fleet (configurable count)
- Latency injection (simulate real network conditions)
- Power cycle simulation (device reboot mid-operation)

## Filesystem Access
Workers, Kimi, and the Orchestrator have FULL filesystem access to the project folder.
This includes: creating/editing/deleting files, running Docker, executing shell scripts,
flashing firmware (simulation mode), running MQTT brokers, and any operational task.
NO human permission required for operational actions.
Human involvement ONLY for: safety tier upgrades, architectural decisions, and escalations.
