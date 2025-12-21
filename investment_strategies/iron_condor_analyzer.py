from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import pandas as pd
import numpy as np
from .questrade_data_fetcher import QuestradeDataFetcher
from .questrade_credentials import QuestradeCredentials
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

@dataclass
class IronCondorCriteria:
    """
    Criteria for identifying stocks suitable for iron condor strategy.
    Each criterion is tied to a specific requirement for successful iron condor trading.
    """
    # Price and Volume Criteria
    # These ensure the position size is manageable and there's enough liquidity
    min_price: float = 50.0  # Minimum stock price - ensures reasonable position size
    max_price: float = 200.0  # Maximum stock price - prevents excessive capital requirement
    min_volume: int = 1000000  # Minimum average daily volume - ensures easy entry/exit
    min_market_cap: float = 1000000000  # Minimum market cap ($1B) - ensures stability
    
    # Volatility Criteria
    # These ensure we're trading in a suitable volatility environment
    min_implied_volatility: float = 0.20  # Minimum IV (20%) - ensures enough premium
    max_implied_volatility: float = 0.50  # Maximum IV (50%) - prevents excessive risk
    max_historical_volatility: float = 0.50  # Max HV (50%) - ensures price stability
    min_iv_skew: float = 0.05  # Minimum IV skew (5%) - indicates volatility opportunity
    
    # Liquidity Criteria
    # These ensure tight spreads and easy execution
    min_option_volume: int = 1000  # Minimum option volume - ensures liquidity
    min_open_interest: int = 500  # Minimum open interest - indicates market interest
    max_bid_ask_spread: float = 0.10  # Max spread (10%) - ensures reasonable costs
    
    # Range-Bound Criteria
    # These identify stocks that trade in predictable ranges
    min_range_bound_score: float = 0.55  # Minimum range score - indicates stability
    lookback_period: int = 90  # Days for range analysis - captures recent behavior
    
    # Time Criteria
    # These ensure optimal time decay
    min_days_to_expiry: int = 30  # Min DTE - ensures enough time for strategy
    max_days_to_expiry: int = 45  # Max DTE - prevents excessive time risk

class IronCondorAnalyzer:
    def __init__(self, data_fetcher: QuestradeDataFetcher):
        self.data_fetcher = data_fetcher
        self.criteria = IronCondorCriteria()

    def _calculate_historical_volatility(self, symbol: str, days: int = 90) -> float:
        """
        Calculate historical volatility using daily returns.
        This metric helps identify stocks with stable price action.
        
        Strategy Requirement: Low to Moderate Volatility
        - Lower historical volatility indicates more stable price action
        - Helps identify stocks that are less likely to break out of their range
        """
        try:
            # Get historical data for volatility calculation
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            historical_data = self.data_fetcher.get_historical_data(
                symbol, 
                start_date.strftime('%Y-%m-%d'),
                end_date.strftime('%Y-%m-%d')
            )
            
            if not historical_data:
                return 0.0
            
            # Calculate daily returns for volatility
            prices = [bar['close'] for bar in historical_data]
            returns = np.diff(np.log(prices))
            
            # Calculate annualized volatility (standard deviation of returns * sqrt(trading days))
            return np.std(returns) * np.sqrt(252)
        except Exception as e:
            print(f"Error calculating historical volatility: {str(e)}")
            return 0.0

    def _analyze_range_bound_behavior(self, symbol: str) -> Tuple[float, Dict]:
        """
        Analyze if the stock exhibits range-bound behavior.
        This is crucial for iron condor success as we want the stock to stay within our range.
        
        Strategy Requirement: Range-Bound Behavior
        - Identifies stocks that trade in predictable ranges
        - Uses mean reversion to detect range-bound characteristics
        - Calculates price stability metrics
        """
        try:
            # Get historical data for range analysis
            end_date = datetime.now()
            start_date = end_date - timedelta(days=self.criteria.lookback_period)
            historical_data = self.data_fetcher.get_historical_data(
                symbol,
                start_date.strftime('%Y-%m-%d'),
                end_date.strftime('%Y-%m-%d')
            )
            
            if not historical_data:
                return 0.0, {}
            
            prices = [bar['close'] for bar in historical_data]
            
            # Calculate range metrics
            price_range = max(prices) - min(prices)
            avg_price = np.mean(prices)
            range_percentage = price_range / avg_price
            
            # Calculate mean reversion (negative correlation indicates range-bound behavior)
            price_changes = np.diff(prices)
            mean_reversion = np.corrcoef(price_changes[:-1], price_changes[1:])[0, 1]
            
            # Calculate final range-bound score
            # Lower range percentage and higher mean reversion = better for iron condor
            range_score = 1 - min(range_percentage, 1.0)
            stability_score = (1 + mean_reversion) / 2
            
            final_score = (range_score * 0.6 + stability_score * 0.4)
            
            metrics = {
                'price_range': price_range,
                'range_percentage': range_percentage,
                'mean_reversion': mean_reversion,
                'range_score': range_score,
                'stability_score': stability_score
            }
            
            return final_score, metrics
            
        except Exception as e:
            print(f"Error analyzing range-bound behavior: {str(e)}")
            return 0.0, {}

    def _analyze_option_liquidity(self, option_chain: Dict) -> Tuple[float, Dict]:
        """
        Analyze option liquidity and bid-ask spreads.
        This is crucial for minimizing trading costs and ensuring easy execution.
        
        Strategy Requirement: High Liquidity
        - Analyzes option volume and open interest
        - Monitors bid-ask spreads
        - Ensures tight spreads for better execution
        """
        try:
            if not option_chain:
                return 0.0, {}
            
            # Handle different possible data structures from Questrade API
            options = []
            if 'options' in option_chain:
                options = option_chain['options']
            elif 'optionChain' in option_chain:
                options = option_chain['optionChain']
            elif isinstance(option_chain, list):
                options = option_chain
            else:
                # If it's a direct options list
                options = option_chain if isinstance(option_chain, list) else []
            
            if not options:
                return 0.0, {}
            
            total_volume = 0
            total_open_interest = 0
            bid_ask_spreads = []
            
            # Calculate liquidity metrics
            for opt in options:
                if isinstance(opt, dict):
                    if 'volume' in opt:
                        total_volume += opt['volume']
                    if 'openInterest' in opt:
                        total_open_interest += opt['openInterest']
                    if 'bid' in opt and 'ask' in opt and opt['bid'] > 0:
                        spread = (opt['ask'] - opt['bid']) / opt['bid']
                        bid_ask_spreads.append(spread)
            
            # Calculate liquidity scores
            avg_spread = np.mean(bid_ask_spreads) if bid_ask_spreads else float('inf')
            liquidity_score = min(1.0, total_volume / self.criteria.min_option_volume)
            spread_score = max(0.0, 1 - (avg_spread / self.criteria.max_bid_ask_spread))
            
            # Combine scores with weights favoring volume over spreads
            final_score = (liquidity_score * 0.6 + spread_score * 0.4)
            
            metrics = {
                'total_volume': total_volume,
                'total_open_interest': total_open_interest,
                'avg_bid_ask_spread': avg_spread,
                'liquidity_score': liquidity_score,
                'spread_score': spread_score
            }
            
            return final_score, metrics
            
        except Exception as e:
            print(f"Error analyzing option liquidity: {str(e)}")
            return 0.0, {}

    def _suggest_strike_prices(self, current_price: float, option_chain: Dict) -> Optional[Dict]:
        """
        Suggests strike prices for an Iron Condor based on the stock's expected move.
        This provides a concrete, actionable trade idea.
        
        Method:
        1. Calculates the expected move using at-the-money (ATM) implied volatility.
        2. Sets short strikes just outside this expected range to maximize premium capture.
        3. Sets long strikes a fixed distance away to define the wings and risk.
        """
        try:
            options = option_chain.get('optionChain')
            if not options:
                return None

            # Find the option chain with the ideal DTE
            target_dte = (self.criteria.min_days_to_expiry + self.criteria.max_days_to_expiry) / 2
            
            best_chain = min(
                options, 
                key=lambda x: abs(pd.to_datetime(x['expiryDate']).dayofyear - (datetime.now().dayofyear + target_dte))
            )

            dte = (pd.to_datetime(best_chain['expiryDate']) - datetime.now()).days
            
            # Find the ATM strike to get a representative IV
            atm_strike_option = min(
                best_chain['chainPerRoot'][0]['chainPerStrikePrice'],
                key=lambda x: abs(x['strikePrice'] - current_price)
            )
            
            iv = atm_strike_option.get('volatility', 0.0)
            if iv == 0: return None

            # Calculate expected move
            expected_move = current_price * iv * np.sqrt(dte / 365)
            
            # Find the closest available strikes
            strikes = sorted([opt['strikePrice'] for opt in best_chain['chainPerRoot'][0]['chainPerStrikePrice']])
            
            short_put_strike = min(strikes, key=lambda x: abs(x - (current_price - expected_move)))
            short_call_strike = min(strikes, key=lambda x: abs(x - (current_price + expected_move)))

            # Define wing width (e.g., 5-15% of the short strike difference)
            wing_width = round(max(1, (short_call_strike - short_put_strike) * 0.15) / 0.5) * 0.5
            
            long_put_strike = min(strikes, key=lambda x: abs(x - (short_put_strike - wing_width)))
            long_call_strike = min(strikes, key=lambda x: abs(x - (short_call_strike + wing_width)))

            # Ensure strikes are logical
            if not (long_put_strike < short_put_strike < current_price < short_call_strike < long_call_strike):
                return None

            return {
                'short_put_strike': short_put_strike,
                'long_put_strike': long_put_strike,
                'short_call_strike': short_call_strike,
                'long_call_strike': long_call_strike,
                'days_to_expiry': dte,
                'expected_move': round(expected_move, 2)
            }
        except (KeyError, IndexError, TypeError) as e:
            # print(f"Could not suggest strikes for {current_price}: {e}")
            return None

    def analyze_stock_suitability(self, symbol: str) -> Dict:
        """
        Analyze if a stock is suitable for iron condor strategy.
        Combines all metrics to provide a comprehensive analysis.
        
        Strategy Requirements:
        1. Low to Moderate Volatility
        2. High Liquidity
        3. Range-Bound Behavior
        4. High IV vs Historical Volatility
        """
        try:
            quote = self.data_fetcher.get_quote(symbol)
            if not quote or not quote.get('lastTradePrice'):
                return {'symbol': symbol, 'error': 'Could not fetch valid quote.', 'recommendation': 'Analysis failed'}

            current_price = quote['lastTradePrice']
            option_chain = self.data_fetcher.get_option_chain(symbol)
            hist_vol = self._calculate_historical_volatility(symbol)
            range_score, range_metrics = self._analyze_range_bound_behavior(symbol)
            liquidity_score, liquidity_metrics = self._analyze_option_liquidity(option_chain)

            analysis = {
                'symbol': symbol, 'current_price': current_price, 'score': 0,
                'criteria_met': [], 'criteria_failed': [],
                'recommendation': '', 'metrics': {
                    'historical_volatility': hist_vol, 'range_score': range_score,
                    'liquidity_score': liquidity_score, **range_metrics, **liquidity_metrics
                },
                'suggested_strikes': None
            }

            weights = {
                'price_range': 0.1, 'volume': 0.1, 'market_cap': 0.1, 'iv': 0.15,
                'hv': 0.1, 'option_liquidity': 0.15, 'range_bound': 0.3
            }
            score = 0

            if self.criteria.min_price <= current_price <= self.criteria.max_price:
                score += weights['price_range']
                analysis['criteria_met'].append('Price Range')
            else:
                analysis['criteria_failed'].append('Price Range')

            if quote.get('volume', 0) >= self.criteria.min_volume:
                score += weights['volume']
                analysis['criteria_met'].append('Sufficient Volume')
            else:
                analysis['criteria_failed'].append('Sufficient Volume')

            if quote.get('marketCap', 0) >= self.criteria.min_market_cap:
                score += weights['market_cap']
                analysis['criteria_met'].append('Market Cap')
            else:
                analysis['criteria_failed'].append('Market Cap')
            
            iv = liquidity_metrics.get('avg_bid_ask_spread', float('inf'))
            if self.criteria.min_implied_volatility <= iv <= self.criteria.max_implied_volatility:
                score += weights['iv']
                analysis['criteria_met'].append('Implied Volatility')
            else:
                analysis['criteria_failed'].append('Implied Volatility')

            if hist_vol <= self.criteria.max_historical_volatility:
                score += weights['hv']
                analysis['criteria_met'].append('Historical Volatility')
            else:
                analysis['criteria_failed'].append('Historical Volatility')

            if liquidity_score >= 0.6:
                score += weights['option_liquidity']
                analysis['criteria_met'].append('Option Liquidity')
            else:
                analysis['criteria_failed'].append('Option Liquidity')

            if range_score >= self.criteria.min_range_bound_score:
                score += weights['range_bound']
                analysis['criteria_met'].append('Range-Bound Behavior')
            else:
                analysis['criteria_failed'].append('Range-Bound Behavior')

            final_score = (score / sum(weights.values())) * 100
            analysis['score'] = round(final_score)

            if final_score >= 70:
                analysis['recommendation'] = 'Highly Recommended'
                analysis['suggested_strikes'] = self._suggest_strike_prices(current_price, option_chain)
            elif final_score >= 50:
                analysis['recommendation'] = 'Moderately Recommended'
            else:
                analysis['recommendation'] = 'Not Recommended'
                
            return analysis
        except Exception as e:
            return {'symbol': symbol, 'error': f'An unexpected error occurred: {str(e)}', 'recommendation': 'Analysis failed'}

    def find_suitable_stocks(self, symbols: List[str]) -> List[Dict]:
        """
        Find stocks suitable for iron condor strategy from a list of symbols.
        Returns stocks that meet minimum criteria for successful iron condor trading.
        """
        suitable_stocks = []
        
        for symbol in symbols:
            analysis = self.analyze_stock_suitability(symbol)
            if analysis.get('score', 0) >= 6:  # Minimum threshold for suitable stocks
                suitable_stocks.append(analysis)
        
        # Sort by score in descending order
        suitable_stocks.sort(key=lambda x: x.get('score', 0), reverse=True)
        return suitable_stocks

if __name__ == '__main__':
    load_dotenv()
    refresh_token = os.getenv('QUESTRADE_REFRESH_TOKEN')
    if not refresh_token:
        print("Error: QUESTRADE_REFRESH_TOKEN not found in your .env file.")
        exit()

    try:
        credentials = QuestradeCredentials(refresh_token=refresh_token)
        fetcher = QuestradeDataFetcher(credentials)
        analyzer = IronCondorAnalyzer(fetcher)
        
        # --- Test with a list of diverse stocks ---
        sample_stocks = ['SPY', 'QQQ', 'MSFT', 'JNJ', 'XOM', 'HD']
        print(f"--- Analyzing Sample Stocks for Iron Condor Suitability: {', '.join(sample_stocks)} ---")
        
        suitable_stocks = analyzer.find_suitable_stocks(sample_stocks)
        
        print("\n--- Top Iron Condor Candidates ---")
        if suitable_stocks:
            for stock in suitable_stocks:
                rec = stock.get('recommendation', 'N/A')
                score = stock.get('score', 0)
                print(f"\n  - {stock['symbol']} (Price: ${stock['current_price']:.2f}): Rec '{rec}', Score {score}/100")
                
                if stock.get('suggested_strikes'):
                    strikes = stock['suggested_strikes']
                    print(f"    - Suggested Strikes (DTE: {strikes['days_to_expiry']}):")
                    print(f"      - Put Spread: {strikes['short_put_strike']} / {strikes['long_put_strike']}")
                    print(f"      - Call Spread: {strikes['short_call_strike']} / {strikes['long_call_strike']}")
                    print(f"      - Expected move: ±${strikes['expected_move']:.2f} from current price.")
                
                print("    - Criteria Met:", ', '.join(stock['criteria_met']) or "None")
                print("    - Criteria Failed:", ', '.join(stock['criteria_failed']) or "None")
        else:
            print("  No suitable stocks found in the sample.")

    except Exception as e:
        print(f"\nAn error occurred during the analysis: {e}") 