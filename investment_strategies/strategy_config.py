"""
Configuration file for the ETF Iron Condor Strategy
Modify these parameters to customize the strategy behavior.
"""

from typing import List, Dict

class StrategyConfig:
    """
    Configuration class for the ETF Iron Condor Strategy
    Modify these parameters to suit your trading preferences and risk tolerance.
    """
    
    # === POSITION MANAGEMENT ===
    MAX_POSITIONS = 6  # Maximum number of iron condor positions to hold simultaneously
    MAX_RISK_PER_POSITION = 300  # Maximum dollar risk per position
    PROFIT_TARGET_PERCENT = 30  # Take profits at 30% of max profit
    STOP_LOSS_PERCENT = 50  # Exit at 50% loss for risk management
    
    # === PROBABILITY AND RISK CRITERIA ===
    TARGET_PROBABILITY_OF_PROFIT = 0.70  # 70% probability of profit minimum
    MIN_RISK_REWARD_RATIO = 0.25  # Minimum risk/reward ratio (credit/risk)
    MIN_EXPECTED_VALUE = 10  # Minimum expected value in dollars
    
    # === TIME MANAGEMENT ===
    TARGET_DAYS_TO_EXPIRY = 30  # Target DTE for monthly contracts
    MAX_DAYS_TO_EXPIRY = 45  # Maximum DTE to consider
    MIN_DAYS_TO_EXPIRY = 25  # Minimum DTE to consider
    CLOSE_BEFORE_EXPIRY_DAYS = 5  # Close positions this many days before expiry
    
    # === SCANNING PARAMETERS ===
    SCAN_INTERVAL_HOURS = 1  # How often to scan for new opportunities
    MAX_OPPORTUNITIES_PER_SCAN = 10  # Maximum opportunities to return per scan
    
    # === ETF UNIVERSE ===
    # List of uncorrelated ETFs for diversification
    # These ETFs represent different sectors and asset classes
    ETF_UNIVERSE = [
        'XSP',  # S&P 500 (Canadian) - Large Cap US Stocks
        'GLD',  # Gold - Precious Metals
        'TLT',  # Long-term Treasury bonds - Fixed Income
        'XOP',  # Oil & Gas Exploration - Energy
        'XBI',  # Biotech - Healthcare
        'XLP',  # Consumer Staples - Defensive
        'XLU',  # Utilities - Defensive
        'XRT',  # Retail - Consumer Discretionary
        'EEM',  # Emerging Markets - International
        'FXI'   # China Large-Cap - International
    ]
    
    # Alternative ETF universes for different market conditions
    CONSERVATIVE_ETFS = [
        'XSP',  # S&P 500
        'TLT',  # Long-term Treasury bonds
        'XLP',  # Consumer Staples
        'XLU',  # Utilities
        'GLD',  # Gold
        'XLE'   # Energy
    ]
    
    AGGRESSIVE_ETFS = [
        'XSP',  # S&P 500
        'XBI',  # Biotech
        'XOP',  # Oil & Gas
        'XRT',  # Retail
        'EEM',  # Emerging Markets
        'FXI',  # China
        'ARKK', # Innovation
        'SOXL'  # 3x Semiconductor
    ]
    
    # === VOLATILITY CRITERIA ===
    MIN_IMPLIED_VOLATILITY = 0.20  # 20% minimum IV
    MAX_IMPLIED_VOLATILITY = 0.50  # 50% maximum IV
    MIN_IV_SKEW = 0.05  # Minimum IV skew for opportunity
    
    # === LIQUIDITY CRITERIA ===
    MIN_STOCK_PRICE = 50.0  # Minimum stock price
    MAX_STOCK_PRICE = 200.0  # Maximum stock price
    MIN_DAILY_VOLUME = 1000000  # Minimum daily volume
    MIN_OPTION_VOLUME = 1000  # Minimum option volume
    MIN_OPEN_INTEREST = 500  # Minimum open interest
    MAX_BID_ASK_SPREAD = 0.10  # Maximum bid-ask spread (10%)
    
    # === RANGE-BOUND CRITERIA ===
    MIN_RANGE_BOUND_SCORE = 0.55  # Minimum range-bound behavior score
    LOOKBACK_PERIOD_DAYS = 90  # Days for historical analysis
    
    # === POSITION SIZING ===
    POSITION_SIZE_PERCENT = 5  # Percentage of account per position
    MAX_ACCOUNT_RISK_PERCENT = 15  # Maximum total account risk
    
    # === EXIT STRATEGIES ===
    EXIT_STRATEGIES = {
        'profit_target': True,  # Exit at profit target
        'stop_loss': True,      # Exit at stop loss
        'time_decay': True,     # Exit before expiration
        'technical_breakout': False,  # Exit on technical breakout
        'volatility_spike': False     # Exit on volatility spike
    }
    
    # === TECHNICAL INDICATORS ===
    USE_TECHNICAL_INDICATORS = False  # Enable technical analysis
    TECHNICAL_INDICATORS = {
        'rsi': {'period': 14, 'oversold': 30, 'overbought': 70},
        'bollinger_bands': {'period': 20, 'std_dev': 2},
        'moving_averages': {'short': 10, 'long': 50}
    }
    
    # === NOTIFICATIONS ===
    ENABLE_NOTIFICATIONS = True
    NOTIFICATION_TYPES = {
        'new_opportunity': True,
        'position_opened': True,
        'position_closed': True,
        'profit_target_hit': True,
        'stop_loss_hit': True,
        'error': True
    }
    
    # === LOGGING ===
    LOG_LEVEL = 'INFO'  # DEBUG, INFO, WARNING, ERROR
    LOG_TO_FILE = True
    LOG_FILE_PATH = 'strategy_logs/etf_iron_condor.log'
    
    # === BACKTESTING ===
    BACKTEST_START_DATE = '2023-01-01'
    BACKTEST_END_DATE = '2024-01-01'
    BACKTEST_INITIAL_CAPITAL = 100000
    
    @classmethod
    def get_etf_universe(cls, market_condition: str = 'normal') -> List[str]:
        """
        Get ETF universe based on market conditions
        
        Args:
            market_condition: 'normal', 'conservative', or 'aggressive'
        
        Returns:
            List of ETF symbols
        """
        if market_condition == 'conservative':
            return cls.CONSERVATIVE_ETFS
        elif market_condition == 'aggressive':
            return cls.AGGRESSIVE_ETFS
        else:
            return cls.ETF_UNIVERSE
    
    @classmethod
    def validate_config(cls) -> Dict[str, List[str]]:
        """
        Validate configuration parameters
        
        Returns:
            Dictionary with 'errors' and 'warnings' lists
        """
        errors = []
        warnings = []
        
        # Validate position management
        if cls.MAX_POSITIONS <= 0:
            errors.append("MAX_POSITIONS must be positive")
        if cls.MAX_RISK_PER_POSITION <= 0:
            errors.append("MAX_RISK_PER_POSITION must be positive")
        if cls.PROFIT_TARGET_PERCENT <= 0 or cls.PROFIT_TARGET_PERCENT > 100:
            errors.append("PROFIT_TARGET_PERCENT must be between 0 and 100")
        
        # Validate probability criteria
        if cls.TARGET_PROBABILITY_OF_PROFIT <= 0 or cls.TARGET_PROBABILITY_OF_PROFIT >= 1:
            errors.append("TARGET_PROBABILITY_OF_PROFIT must be between 0 and 1")
        
        # Validate time parameters
        if cls.TARGET_DAYS_TO_EXPIRY <= 0:
            errors.append("TARGET_DAYS_TO_EXPIRY must be positive")
        if cls.MAX_DAYS_TO_EXPIRY <= cls.TARGET_DAYS_TO_EXPIRY:
            errors.append("MAX_DAYS_TO_EXPIRY must be greater than TARGET_DAYS_TO_EXPIRY")
        
        # Validate ETF universe
        if not cls.ETF_UNIVERSE:
            errors.append("ETF_UNIVERSE cannot be empty")
        
        # Warnings
        if cls.MAX_POSITIONS > 10:
            warnings.append("MAX_POSITIONS > 10 may lead to over-diversification")
        if cls.MAX_RISK_PER_POSITION > 500:
            warnings.append("MAX_RISK_PER_POSITION > $500 may be too risky")
        
        return {'errors': errors, 'warnings': warnings}
    
    @classmethod
    def print_config_summary(cls):
        """Print a summary of the current configuration"""
        print("=" * 60)
        print("ETF IRON CONDOR STRATEGY CONFIGURATION")
        print("=" * 60)
        
        print(f"Position Management:")
        print(f"  - Max Positions: {cls.MAX_POSITIONS}")
        print(f"  - Max Risk per Position: ${cls.MAX_RISK_PER_POSITION}")
        print(f"  - Profit Target: {cls.PROFIT_TARGET_PERCENT}%")
        print(f"  - Stop Loss: {cls.STOP_LOSS_PERCENT}%")
        
        print(f"\nProbability Criteria:")
        print(f"  - Target Probability of Profit: {cls.TARGET_PROBABILITY_OF_PROFIT:.1%}")
        print(f"  - Min Risk/Reward Ratio: {cls.MIN_RISK_REWARD_RATIO:.2f}")
        print(f"  - Min Expected Value: ${cls.MIN_EXPECTED_VALUE}")
        
        print(f"\nTime Management:")
        print(f"  - Target DTE: {cls.TARGET_DAYS_TO_EXPIRY} days")
        print(f"  - DTE Range: {cls.MIN_DAYS_TO_EXPIRY}-{cls.MAX_DAYS_TO_EXPIRY} days")
        print(f"  - Close Before Expiry: {cls.CLOSE_BEFORE_EXPIRY_DAYS} days")
        
        print(f"\nETF Universe ({len(cls.ETF_UNIVERSE)} ETFs):")
        for etf in cls.ETF_UNIVERSE:
            print(f"  - {etf}")
        
        print(f"\nScanning:")
        print(f"  - Scan Interval: {cls.SCAN_INTERVAL_HOURS} hour(s)")
        print(f"  - Max Opportunities per Scan: {cls.MAX_OPPORTUNITIES_PER_SCAN}")
        
        # Validate configuration
        validation = cls.validate_config()
        if validation['errors']:
            print(f"\n❌ CONFIGURATION ERRORS:")
            for error in validation['errors']:
                print(f"  - {error}")
        
        if validation['warnings']:
            print(f"\n⚠️  CONFIGURATION WARNINGS:")
            for warning in validation['warnings']:
                print(f"  - {warning}")
        
        if not validation['errors'] and not validation['warnings']:
            print(f"\n✅ Configuration is valid!")

if __name__ == '__main__':
    # Print configuration summary
    StrategyConfig.print_config_summary() 