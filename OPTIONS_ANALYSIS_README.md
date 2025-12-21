# Options Analysis & Trading Strategy Documentation

## Table of Contents
1. [Overview](#overview)
2. [Options Analysis Methodology](#options-analysis-methodology)
3. [Scoring System](#scoring-system)
4. [Entry Criteria](#entry-criteria)
5. [Exit Strategy](#exit-strategy)
6. [Risk Management](#risk-management)
7. [Mathematical Formulas](#mathematical-formulas)
8. [Implementation Details](#implementation-details)

## Overview

The Options Buying Strategy is a systematic approach to identifying high-probability options buying opportunities using technical analysis, fundamental screening, and risk management. The strategy focuses on buying options with favorable risk/reward ratios and optimal timing based on technical indicators.

## Options Analysis Methodology

### 1. Stock Screening Criteria

Before analyzing options, stocks must meet these fundamental criteria:

| Criterion | Value | Purpose |
|-----------|-------|---------|
| **Stock Price** | $20 - $500 | Ensures sufficient liquidity and reasonable option prices |
| **Daily Volume** | > 1,000,000 shares | Confirms market interest and liquidity |
| **Market Cap** | > $1 billion | Filters out penny stocks and micro-caps |
| **Options Volume** | > 100 contracts | Ensures option liquidity |
| **Open Interest** | > 200 contracts | Confirms institutional interest |

### 2. Options Chain Analysis

For each qualifying stock, we analyze the entire options chain to find optimal strike prices and expiration dates:

#### Time to Expiration (DTE)
- **Minimum DTE**: 21 days (3 weeks)
- **Maximum DTE**: 45 days (6.5 weeks)
- **Rationale**: Balances time decay risk with sufficient time for price movement

#### Implied Volatility (IV) Analysis
- **Minimum IV**: 15% (ensures some volatility premium)
- **Maximum IV**: 80% (avoids overpriced options)
- **IV Percentile**: Prefer options in bottom 20% of historical IV (cheap volatility)

#### Bid-Ask Spread
- **Maximum Spread**: 20% of option price
- **Formula**: `spread_ratio = (ask - bid) / mid_price ≤ 0.20`

## Scoring System

The strategy uses a comprehensive scoring system to rank options opportunities. Each option receives a score from 0-100+ points based on multiple factors.

### 1. RSI Scoring (0-30 points)

**Relative Strength Index (RSI)** measures momentum and identifies overbought/oversold conditions.

#### Formula:
```
RSI = 100 - (100 / (1 + RS))
where RS = Average Gain / Average Loss over 14 periods
```

#### Scoring Logic:
- **For CALL Options**:
  - RSI < 40 (oversold): +30 points
  - RSI > 60 (overbought): -20 points
  - Rationale: Buy calls when stock is oversold and likely to bounce

- **For PUT Options**:
  - RSI > 60 (overbought): +30 points
  - RSI < 40 (oversold): -20 points
  - Rationale: Buy puts when stock is overbought and likely to decline

### 2. Momentum Scoring (0-20 points)

**Momentum** measures the rate of price change over 20 periods.

#### Formula:
```
momentum = (current_price - price_20_periods_ago) / price_20_periods_ago
normalized_momentum = 1 / (1 + e^(-momentum × 10))
```

#### Scoring Logic:
- **For CALL Options**: `score += normalized_momentum × 20`
- **For PUT Options**: `score += (1 - normalized_momentum) × 20`

### 3. Risk/Reward Scoring (0-25 points)

**Risk/Reward Ratio** compares potential profit to potential loss.

#### Formula:
```
risk_reward = (target_price - entry_price) / (entry_price - stop_loss)
```

#### Scoring Logic:
- Risk/Reward > 2.0: +25 points
- Risk/Reward > 1.5: +15 points
- Risk/Reward < 1.0: -15 points

### 4. Probability of Profit (0-20 points)

**Probability of Profit** estimates the likelihood of achieving the target price.

#### Scoring Logic:
- Probability > 60%: +20 points
- Probability < 40%: -15 points

### 5. Time Value Analysis (0-15 points)

**Time Value Ratio** measures how much of the option price is time value vs intrinsic value.

#### Formula:
```
time_value_ratio = time_value / mid_price
```

#### Scoring Logic:
- Time Value Ratio < 50%: +15 points
- Rationale: Prefer options with more intrinsic value

### 6. Liquidity Scoring (0-20 points)

**Volume and Open Interest** ensure tradeability.

#### Scoring Logic:
- Volume > 1,000: +10 points
- Open Interest > 1,000: +10 points

### Total Score Calculation

```
Total Score = RSI_Score + Momentum_Score + RiskReward_Score + 
              Probability_Score + TimeValue_Score + Liquidity_Score
```

**Minimum Score for Consideration**: 60 points

## Entry Criteria

An option is considered for purchase when ALL of the following conditions are met:

### 1. Technical Conditions
- ✅ RSI indicates favorable momentum direction
- ✅ Price momentum aligns with option direction
- ✅ Stock price within acceptable range ($20-$500)

### 2. Options Conditions
- ✅ DTE between 21-45 days
- ✅ IV between 15%-80%
- ✅ Bid-ask spread ≤ 20%
- ✅ Volume > 100 contracts
- ✅ Open Interest > 200 contracts

### 3. Risk Management
- ✅ Risk/Reward ratio ≥ 1.5:1
- ✅ Probability of profit ≥ 40%
- ✅ Position size ≤ 2% of account value
- ✅ Total open positions ≤ 5

### 4. Scoring Threshold
- ✅ Total score ≥ 60 points

## Exit Strategy

The strategy employs multiple exit triggers to maximize profits and minimize losses:

### 1. Profit Target Exit
**Trigger**: Option price reaches 200% of entry price
**Formula**: `exit_price = entry_price × 2.0`
**Rationale**: Lock in 2:1 risk/reward profit

### 2. Stop Loss Exit
**Trigger**: Option price falls to 50% of entry price
**Formula**: `stop_loss_price = entry_price × 0.5`
**Rationale**: Limit losses to 50% of investment

### 3. Time Decay Exit
**Trigger**: 7 days remaining until expiration
**Formula**: `days_remaining = DTE - days_held ≤ 7`
**Rationale**: Avoid rapid time decay in final week

### 4. High Profit Exit
**Trigger**: 50% profit achieved
**Formula**: `profit_percent = (current_price - entry_price) / entry_price ≥ 0.50`
**Rationale**: Take profits on strong moves

### 5. Technical Exit Signals
**Additional Exits** based on technical analysis:
- RSI reversal signals
- Support/resistance breaks
- Momentum divergence

## Risk Management

### Position Sizing
**Formula**: `position_size = (account_value × 0.02) / option_price`

### Maximum Risk Per Trade
- **Risk**: 2% of account value
- **Maximum Positions**: 5 concurrent trades
- **Total Exposure**: ≤ 10% of account value

### Portfolio Heat
**Formula**: `portfolio_heat = (total_at_risk / account_value) × 100`
**Maximum**: 10%

## Mathematical Formulas

### 1. RSI Calculation
```
RSI = 100 - (100 / (1 + RS))
RS = Average Gain / Average Loss (14 periods)
```

### 2. Momentum Calculation
```
momentum = (P_t - P_{t-n}) / P_{t-n}
normalized = 1 / (1 + e^(-momentum × 10))
```

### 3. Risk/Reward Ratio
```
R/R = (Target Price - Entry Price) / (Entry Price - Stop Loss)
```

### 4. Probability of Profit (Simplified)
```
PoP ≈ 1 - (|Strike - Current| / (Current × IV × √(DTE/365)))
```

### 5. Position Size
```
Position Size = (Account Value × Risk %) / Option Price
```

### 6. Portfolio Heat
```
Heat = Σ(Risk per Position) / Account Value
```

## Implementation Details

### Data Requirements
- **Real-time Stock Prices**: For current market data
- **Options Chain Data**: Strikes, expiration, prices, volume, OI
- **Historical Prices**: For technical indicators (20+ days)
- **Account Information**: For position sizing

### Technical Indicators Used
1. **RSI (14-period)**: Momentum oscillator
2. **Price Momentum (20-period)**: Trend strength
3. **Support/Resistance**: Key price levels
4. **Volume Analysis**: Confirmation signals

### Monitoring Frequency
- **Position Monitoring**: Daily
- **Exit Signal Checks**: Real-time during market hours
- **Re-evaluation**: Weekly for open positions

### Performance Metrics
- **Win Rate**: Percentage of profitable trades
- **Average R/R**: Average risk/reward achieved
- **Maximum Drawdown**: Largest peak-to-trough decline
- **Sharpe Ratio**: Risk-adjusted returns

## Strategy Advantages

1. **Systematic Approach**: Removes emotion from trading decisions
2. **Risk Management**: Built-in stop losses and position sizing
3. **High Probability Setups**: Multiple filters ensure quality opportunities
4. **Flexible Exits**: Multiple exit strategies maximize profits
5. **Scalable**: Can be applied to any liquid options market

## Strategy Limitations

1. **Market Dependency**: Performance varies with market conditions
2. **Time Decay**: Options lose value over time regardless of direction
3. **Liquidity Requirements**: Needs liquid options markets
4. **Capital Intensive**: Requires sufficient capital for position sizing
5. **Learning Curve**: Requires understanding of options mechanics

---

*This documentation provides a comprehensive framework for systematic options trading. Always test strategies with paper trading before using real capital.*
