# Smart Money Python Analysis

High-frequency trading (HFT) system for detecting iceberg orders and smart money movements on cryptocurrency markets (Binance).

## Features

- **Iceberg Order Detection**: Identify hidden liquidity using L2 order book data
- **Algorithm Classification**: Detect TWAP, VWAP, ICEBERG, and SWEEP algorithms
- **Anti-Spoofing**: Weighted scoring with temporal analysis and execution patterns
- **GEX Integration**: Gamma exposure data from Deribit options market
- **Multi-Asset Support**: Bitcoin, Ethereum, Solana and other cryptocurrencies
- **Real-time Analysis**: WebSocket-based order book and trade stream processing

## Architecture

Clean architecture with distinct layers:
- `domain.py` - Core business logic and models
- `analyzers.py` - Detection algorithms (iceberg, algo classification)
- `services.py` - Orchestration layer
- `infrastructure.py` - External API integrations (Binance, Deribit)
- `repository.py` - PostgreSQL persistence

## Key Algorithms

### Iceberg Detection
- **Delta-t Temporal Validation**: Distinguish between automatic refills (5-30ms) and new orders (50-500ms)
- **Sigmoid Probability Function**: P(Refill|Δt) = 1/(1 + e^α(Δt - τ_cutoff))
- **Anti-Spoofing**: High execution % reduces spoofing probability

### Algorithm Classification
Decision tree approach:
1. **Directional Ratio** ≥85% threshold
2. **Size Uniformity** >90% → ICEBERG (Priority 1)
3. **Coefficient of Variation** <10% → TWAP vs VWAP (Priority 2-3)
4. **Mean Interval Analysis** → SWEEP detection (Priority 4)

## Technical Stack

- **Python 3.13+** with asyncio for non-blocking operations
- **PostgreSQL** for data persistence
- **Pydantic** for data modeling and validation
- **WebSocket** connections to Binance for real-time data
- **Deribit API** for gamma exposure data

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test suite
pytest tests/test_algo_detection.py -v

# Run with debug output
pytest tests/test_algo_detection.py -v -s
```

### Test Coverage
- ✅ Iceberg detection (native and synthetic)
- ✅ Algorithm classification (TWAP/VWAP/ICEBERG/SWEEP)
- ✅ Anti-spoofing mechanisms
- ✅ Gap recovery (edge case: 2-min silence)
- ✅ Multi-asset configuration
- ✅ GEX integration

## Performance

- **Throughput**: 800-1000 TPS (optimized timing pattern analysis)
- **Latency**: <10ms per trade update
- **Memory**: O(1) with bounded deques

## Configuration

Multi-asset thresholds in `config.py`:

```python
BTC_CONFIG = MultiAssetConfig(
    whale_threshold_usd=10000,
    minnow_threshold_usd=100,
    iceberg_min_execution_percent=0.40,
    # ... other parameters
)
```

## Critical Design Patterns

### Snapshot-to-Stream Synchronization
Buffer-before-snapshot pattern with `lastUpdateId` tracking for deterministic update sequencing.

### Mathematical Precision
Uses `Decimal` types throughout to prevent floating-point errors that corrupt detection calculations.

### Cleanup & TTL
60-second timeout for old trades with synchronized deque cleanup to prevent memory leaks.

## Development Setup

```bash
# Clone repository
git clone https://github.com/YOUR_USERNAME/smart_money_python_analysis.git
cd smart_money_python_analysis

# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/ -v
```

## Project Status

**Production Ready** for core Local Order Book functionality with advanced algorithm detection capabilities.

## Documentation

See project documentation files:
- `ALGO_DETECTION_IMPLEMENTATION.md` - Algorithm detection details
- `DELTA_T_IMPLEMENTATION_STATUS.md` - Temporal validation
- `GEX_INTEGRATION_COMPLETE.md` - Gamma exposure integration
- `READINESS_CHECK_COMPLETE.md` - Production readiness assessment

## License

[Your License Here]

## Author

Basilisca (annam)
