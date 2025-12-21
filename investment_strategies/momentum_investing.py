import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

@dataclass
class PriceData:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float

class MomentumInvestingStrategy:
    def __init__(self, lookback_period: int = 20):
        self.lookback_period = lookback_period
        self.momentum_indicators = {
            'rsi': None,
            'macd': None,
            'bollinger_bands': None,
            'volume_trend': None
        }
    
    def calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """
        Calculate Relative Strength Index (RSI)
        """
        if len(prices) < period + 1:
            return 50.0  # Default neutral value
            
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        
        if avg_loss == 0:
            return 100.0
            
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def calculate_macd(self, prices: List[float]) -> Tuple[float, float, float]:
        """
        Calculate Moving Average Convergence Divergence (MACD)
        Returns (MACD line, Signal line, MACD histogram)
        """
        if len(prices) < 26:
            return 0.0, 0.0, 0.0
            
        ema12 = pd.Series(prices).ewm(span=12, adjust=False).mean()
        ema26 = pd.Series(prices).ewm(span=26, adjust=False).mean()
        
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_histogram = macd_line - signal_line
        
        return macd_line.iloc[-1], signal_line.iloc[-1], macd_histogram.iloc[-1]
    
    def calculate_bollinger_bands(self, prices: List[float], period: int = 20) -> Tuple[float, float, float]:
        """
        Calculate Bollinger Bands
        Returns (upper band, middle band, lower band)
        """
        if len(prices) < period:
            return 0.0, 0.0, 0.0
            
        middle_band = np.mean(prices[-period:])
        std_dev = np.std(prices[-period:])
        
        upper_band = middle_band + (2 * std_dev)
        lower_band = middle_band - (2 * std_dev)
        
        return upper_band, middle_band, lower_band
    
    def analyze_volume_trend(self, volumes: List[float], period: int = 5) -> str:
        """
        Analyze volume trend
        """
        if len(volumes) < period:
            return "Insufficient Data"
            
        recent_volumes = volumes[-period:]
        avg_volume = np.mean(volumes[-period*2:-period])
        
        if np.mean(recent_volumes) > avg_volume * 1.5:
            return "Strong Volume"
        elif np.mean(recent_volumes) > avg_volume:
            return "Increasing Volume"
        else:
            return "Decreasing Volume"
    
    def calculate_momentum_score(self, 
                               price_data: List[PriceData],
                               weights: Dict[str, float] = None) -> float:
        """
        Calculate overall momentum score based on multiple indicators
        """
        if weights is None:
            weights = {
                'rsi': 0.3,
                'macd': 0.3,
                'bollinger_bands': 0.2,
                'volume_trend': 0.2
            }
            
        prices = [data.close for data in price_data]
        volumes = [data.volume for data in price_data]
        
        # Calculate RSI score (normalized to 0-1)
        rsi = self.calculate_rsi(prices)
        rsi_score = abs(rsi - 50) / 50  # Higher deviation from 50 is better
        
        # Calculate MACD score
        macd_line, signal_line, histogram = self.calculate_macd(prices)
        macd_score = 1.0 if histogram > 0 else 0.0
        
        # Calculate Bollinger Bands score
        upper, middle, lower = self.calculate_bollinger_bands(prices)
        current_price = prices[-1]
        bb_score = 1.0 if current_price > middle else 0.0
        
        # Calculate Volume Trend score
        volume_trend = self.analyze_volume_trend(volumes)
        volume_score = 1.0 if volume_trend == "Strong Volume" else \
                     0.5 if volume_trend == "Increasing Volume" else 0.0
        
        # Calculate weighted score
        momentum_score = (
            rsi_score * weights['rsi'] +
            macd_score * weights['macd'] +
            bb_score * weights['bollinger_bands'] +
            volume_score * weights['volume_trend']
        )
        
        return momentum_score
    
    def generate_trading_signals(self, 
                               price_data: List[PriceData],
                               threshold: float = 0.7) -> str:
        """
        Generate trading signals based on momentum analysis
        """
        momentum_score = self.calculate_momentum_score(price_data)
        
        if momentum_score > threshold:
            return "Strong Buy"
        elif momentum_score > 0.5:
            return "Buy"
        elif momentum_score < 0.3:
            return "Sell"
        else:
            return "Hold"
    
    def backtest_strategy(self,
                         historical_data: List[PriceData],
                         initial_capital: float = 100000.0) -> Dict[str, float]:
        """
        Backtest the momentum strategy
        """
        if len(historical_data) < self.lookback_period:
            return {"error": "Insufficient historical data"}
            
        capital = initial_capital
        position = 0.0
        trades = []
        
        for i in range(self.lookback_period, len(historical_data)):
            window_data = historical_data[i-self.lookback_period:i]
            signal = self.generate_trading_signals(window_data)
            current_price = historical_data[i].close
            
            if signal == "Strong Buy" and position == 0:
                # Buy with 100% of capital
                position = capital / current_price
                capital = 0
                trades.append(("Buy", current_price))
            elif signal == "Sell" and position > 0:
                # Sell entire position
                capital = position * current_price
                position = 0
                trades.append(("Sell", current_price))
        
        # Close any remaining position
        if position > 0:
            capital = position * historical_data[-1].close
            position = 0
        
        return {
            "final_capital": capital,
            "total_return": (capital - initial_capital) / initial_capital * 100,
            "number_of_trades": len(trades)
        } 