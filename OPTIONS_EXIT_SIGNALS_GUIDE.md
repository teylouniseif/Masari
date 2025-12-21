# Options Exit Signals Reference Guide

## Overview
This guide provides a quick reference for all exit signals implemented in the Options Buying Strategy.

## Exit Signal Types

### 1. Profit Target Exit
- **Trigger**: Option price reaches 200% of entry price
- **Formula**: `exit_price = entry_price × 2.0`
- **Purpose**: Lock in 2:1 risk/reward profit
- **Priority**: High (immediate execution)

### 2. Stop Loss Exit
- **Trigger**: Option price falls to 50% of entry price
- **Formula**: `stop_loss_price = entry_price × 0.5`
- **Purpose**: Limit losses to 50% of investment
- **Priority**: High (immediate execution)

### 3. Time Decay Exit
- **Trigger**: 7 days remaining until expiration
- **Formula**: `days_remaining = DTE - days_held ≤ 7`
- **Purpose**: Avoid rapid time decay in final week
- **Priority**: High (immediate execution)

### 4. High Profit Exit
- **Trigger**: 50% profit achieved
- **Formula**: `profit_percent = (current_price - entry_price) / entry_price ≥ 0.50`
- **Purpose**: Take profits on strong moves
- **Priority**: Medium (can be overridden by other signals)

### 5. Technical Exit Signals

#### 5.1 RSI Reversal Signal
**For CALL Options:**
- **Trigger**: RSI > 70 and declining
- **Logic**: Stock is overbought and momentum is reversing
- **Action**: Exit call position

**For PUT Options:**
- **Trigger**: RSI < 30 and rising
- **Logic**: Stock is oversold and momentum is reversing
- **Action**: Exit put position

#### 5.2 Support/Resistance Break
**For CALL Options:**
- **Trigger**: Price breaks below support level by 2%
- **Formula**: `current_price < support_level × 0.98`
- **Logic**: Support break invalidates bullish thesis
- **Action**: Exit call position

**For PUT Options:**
- **Trigger**: Price breaks above resistance level by 2%
- **Formula**: `current_price > resistance_level × 1.02`
- **Logic**: Resistance break invalidates bearish thesis
- **Action**: Exit put position

#### 5.3 Momentum Divergence
**For CALL Options:**
- **Trigger**: Current momentum < 70% of entry momentum
- **Formula**: `momentum < entry_momentum × 0.7`
- **Logic**: Momentum is weakening significantly
- **Action**: Exit call position

**For PUT Options:**
- **Trigger**: Current momentum > 130% of entry momentum
- **Formula**: `momentum > entry_momentum × 1.3`
- **Logic**: Momentum is strengthening against position
- **Action**: Exit put position

## Exit Signal Priority

1. **Critical Exits** (Immediate execution):
   - Profit Target Hit
   - Stop Loss Hit
   - Time Decay Exit

2. **Technical Exits** (Evaluate daily):
   - RSI Reversal Signal
   - Support/Resistance Break
   - Momentum Divergence

3. **Profit Taking** (Optional):
   - High Profit Exit

## Implementation Notes

### Position Monitoring
- **Frequency**: Daily during market hours
- **Data Required**: Real-time option prices, historical stock data
- **Processing**: Check all exit signals for each open position

### Signal Validation
- **Data Quality**: Ensure sufficient historical data (20+ days)
- **Market Hours**: Only check signals during market hours
- **Error Handling**: Skip signals if data is unavailable

### Execution Order
1. Check critical exits first (profit/stop/time)
2. Evaluate technical signals
3. Consider profit-taking opportunities
4. Execute highest priority signal

## Example Exit Scenarios

### Scenario 1: Successful Call Option
```
Entry: AAPL Call $150, 30 DTE, $2.00
- Day 5: Price reaches $4.00 → Profit Target Exit
- Result: 100% profit in 5 days
```

### Scenario 2: Stop Loss Protection
```
Entry: TSLA Put $200, 25 DTE, $3.00
- Day 3: Price drops to $1.50 → Stop Loss Exit
- Result: 50% loss (controlled)
```

### Scenario 3: Technical Exit
```
Entry: MSFT Call $300, 20 DTE, $2.50
- Day 8: RSI reaches 75 and declining → RSI Reversal Exit
- Result: 40% profit (early exit on technical signal)
```

### Scenario 4: Time Decay Protection
```
Entry: NVDA Put $400, 10 DTE, $4.00
- Day 7: 3 days remaining → Time Decay Exit
- Result: 15% profit (avoided rapid decay)
```

## Best Practices

1. **Never Override Stop Losses**: Always honor stop loss levels
2. **Respect Profit Targets**: Take profits when targets are hit
3. **Monitor Technical Signals**: Check daily for reversal signals
4. **Document Exit Reasons**: Track why positions were closed
5. **Review Performance**: Analyze exit signal effectiveness

## Customization Options

### Adjustable Parameters
- **Profit Target**: Currently 2.0x (configurable)
- **Stop Loss**: Currently 0.5x (configurable)
- **Time Decay Threshold**: Currently 7 days (configurable)
- **High Profit Threshold**: Currently 50% (configurable)

### Signal Sensitivity
- **RSI Thresholds**: 30/70 levels (adjustable)
- **Support/Resistance Break**: 2% buffer (adjustable)
- **Momentum Divergence**: 30% threshold (adjustable)

---

*This guide ensures consistent and systematic exit decisions for optimal risk management and profit maximization.*
