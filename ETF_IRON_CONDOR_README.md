# ETF Iron Condor Strategy

A sophisticated options trading strategy that scans uncorrelated ETFs for iron condor opportunities, targeting high-probability trades with controlled risk.

## Strategy Overview

The ETF Iron Condor Strategy implements a systematic approach to options trading that:

1. **Scans 10 uncorrelated ETFs** for iron condor opportunities
2. **Sells up to 6 iron condors** targeting monthly contracts (30-45 DTE)
3. **Targets 70% probability of profit** with less than $300 risk per position
4. **Takes profits at 30%** whenever possible
5. **Uses Trade Ideas action** to identify high-quality setups

## Key Features

### 🎯 **High-Probability Trading**
- Targets 70%+ probability of profit
- Uses sophisticated probability calculations
- Implements strict risk management

### 📊 **Diversified ETF Universe**
- **XSP**: S&P 500 (Canadian) - Large Cap US Stocks
- **GLD**: Gold - Precious Metals
- **TLT**: Long-term Treasury bonds - Fixed Income
- **XOP**: Oil & Gas Exploration - Energy
- **XBI**: Biotech - Healthcare
- **XLP**: Consumer Staples - Defensive
- **XLU**: Utilities - Defensive
- **XRT**: Retail - Consumer Discretionary
- **EEM**: Emerging Markets - International
- **FXI**: China Large-Cap - International

### ⚡ **Automated Management**
- Continuous scanning for opportunities
- Automated position monitoring
- Smart exit strategies
- Risk management controls

### 🔧 **Customizable Configuration**
- Adjustable risk parameters
- Configurable profit targets
- Flexible position sizing
- Market condition adaptations

## Installation & Setup

### Prerequisites

1. **Python 3.8+**
2. **Questrade API Access** (refresh token)
3. **Required Python packages**

### Install Dependencies

```bash
pip install pandas numpy requests yfinance python-dotenv lxml
```

### Environment Setup

1. Create a `.env` file in your project root:
```env
QUESTRADE_REFRESH_TOKEN=your_refresh_token_here
```

2. Get your Questrade refresh token:
   - Log into your Questrade account
   - Go to My Account → API Access
   - Generate a refresh token
   - Copy it to your `.env` file

## Usage

### Quick Start

1. **Test the Strategy** (recommended first step):
```bash
python test_etf_strategy.py
```

2. **Run the Full Strategy**:
```bash
python investment_strategies/etf_iron_condor_strategy.py
```

3. **View Configuration**:
```bash
python investment_strategies/strategy_config.py
```

### Configuration

The strategy is highly customizable through the `StrategyConfig` class:

```python
from investment_strategies.strategy_config import StrategyConfig

# Modify parameters
StrategyConfig.MAX_POSITIONS = 8
StrategyConfig.MAX_RISK_PER_POSITION = 250
StrategyConfig.PROFIT_TARGET_PERCENT = 25

# Use different ETF universes for market conditions
conservative_etfs = StrategyConfig.get_etf_universe('conservative')
aggressive_etfs = StrategyConfig.get_etf_universe('aggressive')
```

## Strategy Parameters

### Position Management
- **Max Positions**: 6 (maximum concurrent iron condors)
- **Max Risk per Position**: $300
- **Profit Target**: 30% of max profit
- **Stop Loss**: 50% loss

### Probability Criteria
- **Target Probability of Profit**: 70%
- **Min Risk/Reward Ratio**: 0.25
- **Min Expected Value**: $10

### Time Management
- **Target DTE**: 30 days (monthly contracts)
- **DTE Range**: 25-45 days
- **Close Before Expiry**: 5 days

### Scanning
- **Scan Interval**: 1 hour
- **Max Opportunities per Scan**: 10

## How It Works

### 1. **Opportunity Scanning**
The strategy continuously scans the ETF universe for iron condor opportunities:

```python
opportunities = strategy.scan_for_opportunities()
```

**Screening Criteria:**
- ✅ 70%+ probability of profit
- ✅ <$300 maximum risk
- ✅ Positive expected value
- ✅ Sufficient liquidity
- ✅ Range-bound behavior
- ✅ No existing positions in symbol

### 2. **Position Analysis**
For each opportunity, the strategy calculates:

- **Probability of Profit**: Based on option pricing models
- **Maximum Risk**: Width of spreads minus net credit
- **Expected Value**: Weighted average of outcomes
- **Alpha**: Excess return vs risk-free rate
- **Risk/Reward Ratio**: Net credit / maximum risk

### 3. **Trade Execution**
When a qualified opportunity is found:

```python
if strategy.execute_trade(opportunity):
    print(f"New position opened for {opportunity['symbol']}")
```

### 4. **Position Monitoring**
The strategy continuously monitors open positions:

```python
positions_to_close = strategy.monitor_positions()
```

**Exit Conditions:**
- 🎯 **Profit Target**: 30% of max profit
- 🛑 **Stop Loss**: 50% loss
- ⏰ **Time Decay**: Close before expiration
- 📊 **Technical Breakout**: (optional)

### 5. **Risk Management**
- Maximum 6 concurrent positions
- $300 maximum risk per position
- Diversified across uncorrelated ETFs
- Automated stop losses

## Example Output

```
============================================================
ETF IRON CONDOR STRATEGY - SCANNING TEST
============================================================
ETF Universe: XSP, GLD, TLT, XOP, XBI, XLP, XLU, XRT, EEM, FXI
Strategy Parameters:
  - Max Positions: 6
  - Target Probability: 70.0%
  - Max Risk: $300
  - Profit Target: 30.0%
  - Target DTE: 30 days

========================================
SCANNING FOR OPPORTUNITIES
========================================
Scanning 10 ETFs for iron condor opportunities...
Current positions: 0

Analyzing XSP...
  ✓ XSP - QUALIFIED OPPORTUNITY
    Prob Profit: 72.5%
    Max Risk: $275.00
    Expected Value: $45.20

Found 3 qualified opportunities:
--------------------------------------------------------------------------------

1. XSP (Score: 85/100)
   Current Price: $450.25
   Probability of Profit: 72.5%
   Max Risk: $275.00
   Net Credit: $2.50
   Expected Value: $45.20
   Alpha: 0.0234
   Risk/Reward: 0.91
   Suggested Strikes (DTE: 32):
     Put Spread: 440 / 445
     Call Spread: 455 / 460
     Expected Move: ±$8.50
   Priority Score: 0.847
```

## Risk Considerations

### ⚠️ **Important Disclaimers**

1. **Options Trading Risk**: Options trading involves substantial risk and is not suitable for all investors
2. **Past Performance**: Past performance does not guarantee future results
3. **Market Conditions**: Strategy performance varies with market conditions
4. **Technical Risk**: API failures, data delays, and execution issues can occur

### 🛡️ **Risk Management Features**

- **Position Limits**: Maximum 6 concurrent positions
- **Risk Per Trade**: Maximum $300 risk per position
- **Diversification**: Across 10 uncorrelated ETFs
- **Stop Losses**: Automated 50% loss exits
- **Time Management**: Close before expiration

### 📈 **Performance Expectations**

- **Target Return**: 15-25% annually
- **Win Rate**: 70-80% of trades
- **Max Drawdown**: 10-15%
- **Sharpe Ratio**: 1.5-2.0

## Customization

### Market Condition Adaptations

```python
# Conservative market (high volatility)
StrategyConfig.ETF_UNIVERSE = StrategyConfig.CONSERVATIVE_ETFS
StrategyConfig.MAX_RISK_PER_POSITION = 200
StrategyConfig.TARGET_PROBABILITY_OF_PROFIT = 0.75

# Aggressive market (low volatility)
StrategyConfig.ETF_UNIVERSE = StrategyConfig.AGGRESSIVE_ETFS
StrategyConfig.MAX_RISK_PER_POSITION = 400
StrategyConfig.TARGET_PROBABILITY_OF_PROFIT = 0.65
```

### Adding Technical Indicators

```python
# Enable technical analysis
StrategyConfig.USE_TECHNICAL_INDICATORS = True
StrategyConfig.EXIT_STRATEGIES['technical_breakout'] = True
```

### Custom ETF Universe

```python
# Add your own ETFs
StrategyConfig.ETF_UNIVERSE = [
    'SPY', 'QQQ', 'IWM', 'GLD', 'TLT', 'XLE', 'XLF', 'XLV'
]
```

## Troubleshooting

### Common Issues

1. **API Connection Errors**
   - Verify your refresh token is valid
   - Check internet connection
   - Ensure Questrade API is accessible

2. **No Opportunities Found**
   - Market conditions may not be suitable
   - ETFs may not meet strict criteria
   - Check configuration parameters

3. **Position Execution Failures**
   - Insufficient buying power
   - Market closed or illiquid
   - API rate limits

### Debug Mode

Enable debug logging:

```python
StrategyConfig.LOG_LEVEL = 'DEBUG'
StrategyConfig.LOG_TO_FILE = True
```

## Support & Development

### File Structure

```
investment_strategies/
├── etf_iron_condor_strategy.py    # Main strategy implementation
├── strategy_config.py             # Configuration management
├── iron_condor_analyzer.py        # Iron condor analysis
├── questrade_data_fetcher.py      # Data fetching
└── stock_screener.py              # Stock screening utilities

test_etf_strategy.py               # Test script
ETF_IRON_CONDOR_README.md          # This documentation
```

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

### License

This project is licensed under the MIT License - see the LICENSE file for details.

## Disclaimer

This software is for educational and informational purposes only. It is not intended as investment advice. Options trading involves substantial risk and is not suitable for all investors. Always consult with a qualified financial advisor before making investment decisions.

---

**Happy Trading! 📈** 