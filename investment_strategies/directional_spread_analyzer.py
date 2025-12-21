from dataclasses import dataclass
import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from .questrade_data_fetcher import QuestradeDataFetcher
from datetime import datetime

@dataclass
class SpreadCriteria:
    """
    Defines the shared criteria for identifying suitable candidates for directional spreads.
    """
    min_volume: int = 1_000_000
    min_price: float = 20.0
    fast_ma_period: int = 20
    slow_ma_period: int = 50
    min_bullish_momentum_score: float = 0.6  # Score indicating bullish trend
    min_bearish_momentum_score: float = 0.4  # Score indicating bearish trend (1.0 - bullish_score)
    max_iv_for_debit: float = 0.60
    min_option_volume: int = 500
    min_open_interest: int = 200
    min_days_to_expiry: int = 20
    max_days_to_expiry: int = 50

class BaseDirectionalAnalyzer:
    """
    Base class with shared logic for analyzing directional option spreads.
    It handles momentum calculation and provides a template for finding suitable stocks.
    """
    def __init__(self, data_fetcher: QuestradeDataFetcher):
        self.data_fetcher = data_fetcher
        self.criteria = SpreadCriteria()

    def _calculate_momentum(self, historical_data: List[Dict]) -> float:
        """
        Calculates a momentum score from 0.0 to 1.0 based on moving average crossovers.
        - Score > 0.5 suggests bullish momentum.
        - Score < 0.5 suggests bearish momentum.
        """
        if len(historical_data) < self.criteria.slow_ma_period:
            return 0.5

        prices = [d['close'] for d in historical_data]
        df = pd.DataFrame(prices, columns=['close'])
        df['fast_ma'] = df['close'].rolling(window=self.criteria.fast_ma_period).mean()
        df['slow_ma'] = df['close'].rolling(window=self.criteria.slow_ma_period).mean()

        last_fast_ma = df['fast_ma'].iloc[-1]
        last_slow_ma = df['slow_ma'].iloc[-1]

        if pd.isna(last_fast_ma) or pd.isna(last_slow_ma):
            return 0.5

        ma_diff = last_fast_ma - last_slow_ma
        typical_range = df['close'].std() * 0.5
        momentum_score = 1 / (1 + np.exp(-ma_diff / typical_range))
        return momentum_score

    def find_suitable_stocks(self, symbols: List[str]) -> List[Dict]:
        """
        Finds suitable stocks for the strategy by analyzing a list of symbols.
        This method is used by the StockScreener.
        """
        suitable_stocks = []
        for symbol in symbols:
            analysis = self.analyze_suitability(symbol)
            suitable_stocks.append(analysis)
        suitable_stocks.sort(key=lambda x: x.get('score', 0), reverse=True)
        return suitable_stocks

    def analyze_suitability(self, symbol: str) -> Dict:
        """This method must be implemented by the child classes."""
        raise NotImplementedError("Each analyzer must implement its own suitability analysis.")

class BullCallSpreadAnalyzer(BaseDirectionalAnalyzer):
    """Analyzes stocks for Bull Call Spread suitability."""

    def _suggest_bull_call_strikes(self, current_price: float, option_chain: Dict) -> Optional[Dict]:
        try:
            options = option_chain.get('optionChain')
            if not options: return None

            target_dte = (self.criteria.min_days_to_expiry + self.criteria.max_days_to_expiry) / 2
            best_chain = min(options, key=lambda x: abs(pd.to_datetime(x['expiryDate']).dayofyear - (datetime.now().dayofyear + target_dte)))
            dte = (pd.to_datetime(best_chain['expiryDate']) - datetime.now()).days
            
            atm_option = min(best_chain['chainPerRoot'][0]['chainPerStrikePrice'], key=lambda x: abs(x['strikePrice'] - current_price))
            iv = atm_option.get('volatility', 0)
            if iv == 0: return None

            strikes = sorted([opt['strikePrice'] for opt in best_chain['chainPerRoot'][0]['chainPerStrikePrice']])
            
            # Long strike: at-the-money or just below
            long_strike = min(strikes, key=lambda x: abs(x - (current_price * 0.99)))
            
            # Short strike: out-of-the-money, based on a fraction of expected move
            expected_move = current_price * iv * np.sqrt(dte / 365)
            short_strike = min(strikes, key=lambda x: abs(x - (current_price + expected_move * 0.7)))

            if not (long_strike < short_strike): return None
            
            return {'long_call_strike': long_strike, 'short_call_strike': short_strike, 'days_to_expiry': dte}
        except (KeyError, IndexError, TypeError):
            return None

    def analyze_suitability(self, symbol: str) -> Dict:
        try:
            quote = self.data_fetcher.get_quote(symbol)
            if not quote: return {'symbol': symbol, 'error': 'Could not fetch quote.'}

            end_date = pd.Timestamp.now().isoformat()
            start_date = (pd.Timestamp.now() - pd.Timedelta(days=self.criteria.slow_ma_period + 50)).isoformat()
            hist_data = self.data_fetcher.get_historical_data(symbol, start_date, end_date)
            option_chain = self.data_fetcher.get_option_chain(symbol)

            analysis = {'symbol': symbol, 'current_price': quote.get('lastTradePrice'), 'score': 0, 'recommendation': 'Not Recommended', 'details': {}, 'suggested_strikes': None}
            
            if quote.get('volume', 0) >= self.criteria.min_volume: analysis['score'] += 2
            if quote.get('lastTradePrice', 0) >= self.criteria.min_price: analysis['score'] += 1
            
            momentum_score = self._calculate_momentum(hist_data)
            analysis['details']['momentum_score'] = momentum_score
            if momentum_score >= self.criteria.min_bullish_momentum_score: analysis['score'] += 4

            iv = option_chain.get('optionChain', [{}])[0].get('chainPerRoot', [{}])[0].get('chainPerStrikePrice', [{}])[0].get('volatility', 1.0)
            analysis['details']['estimated_iv'] = iv
            if iv <= self.criteria.max_iv_for_debit: analysis['score'] += 2

            options_list = [opt for chain in option_chain.get('optionChain', []) for root in chain.get('chainPerRoot', []) for opt in root.get('chainPerStrikePrice', [])]
            option_volume = sum(opt.get('volume', 0) for opt in options_list)
            open_interest = sum(opt.get('openInterest', 0) for opt in options_list)
            if option_volume >= self.criteria.min_option_volume and open_interest >= self.criteria.min_open_interest: analysis['score'] += 2

            max_score = 11
            score_percentage = (analysis['score'] / max_score) * 100
            if score_percentage >= 70:
                analysis['recommendation'] = 'Highly Suitable'
                analysis['suggested_strikes'] = self._suggest_bull_call_strikes(quote.get('lastTradePrice'), option_chain)
            elif score_percentage >= 50:
                analysis['recommendation'] = 'Moderately Suitable'

            return analysis
        except Exception as e:
            return {'symbol': symbol, 'error': str(e)}

class BearPutSpreadAnalyzer(BaseDirectionalAnalyzer):
    """Analyzes stocks for Bear Put Spread suitability."""

    def _suggest_bear_put_strikes(self, current_price: float, option_chain: Dict) -> Optional[Dict]:
        try:
            options = option_chain.get('optionChain')
            if not options: return None

            target_dte = (self.criteria.min_days_to_expiry + self.criteria.max_days_to_expiry) / 2
            best_chain = min(options, key=lambda x: abs(pd.to_datetime(x['expiryDate']).dayofyear - (datetime.now().dayofyear + target_dte)))
            dte = (pd.to_datetime(best_chain['expiryDate']) - datetime.now()).days
            
            atm_option = min(best_chain['chainPerRoot'][0]['chainPerStrikePrice'], key=lambda x: abs(x['strikePrice'] - current_price))
            iv = atm_option.get('volatility', 0)
            if iv == 0: return None

            strikes = sorted([opt['strikePrice'] for opt in best_chain['chainPerRoot'][0]['chainPerStrikePrice']])
            
            # Long strike: at-the-money or just above
            long_strike = min(strikes, key=lambda x: abs(x - (current_price * 1.01)))

            # Short strike: out-of-the-money, based on a fraction of expected move
            expected_move = current_price * iv * np.sqrt(dte / 365)
            short_strike = min(strikes, key=lambda x: abs(x - (current_price - expected_move * 0.7)))

            if not (short_strike < long_strike): return None

            return {'long_put_strike': long_strike, 'short_put_strike': short_strike, 'days_to_expiry': dte}
        except (KeyError, IndexError, TypeError):
            return None

    def analyze_suitability(self, symbol: str) -> Dict:
        try:
            quote = self.data_fetcher.get_quote(symbol)
            if not quote: return {'symbol': symbol, 'error': 'Could not fetch quote.'}

            end_date = pd.Timestamp.now().isoformat()
            start_date = (pd.Timestamp.now() - pd.Timedelta(days=self.criteria.slow_ma_period + 50)).isoformat()
            hist_data = self.data_fetcher.get_historical_data(symbol, start_date, end_date)
            option_chain = self.data_fetcher.get_option_chain(symbol)

            analysis = {'symbol': symbol, 'current_price': quote.get('lastTradePrice'), 'score': 0, 'recommendation': 'Not Recommended', 'details': {}, 'suggested_strikes': None}
            
            if quote.get('volume', 0) >= self.criteria.min_volume: analysis['score'] += 2
            if quote.get('lastTradePrice', 0) >= self.criteria.min_price: analysis['score'] += 1

            momentum_score = self._calculate_momentum(hist_data)
            analysis['details']['momentum_score'] = momentum_score
            if momentum_score <= self.criteria.min_bearish_momentum_score: analysis['score'] += 4

            iv = option_chain.get('optionChain', [{}])[0].get('chainPerRoot', [{}])[0].get('chainPerStrikePrice', [{}])[0].get('volatility', 1.0)
            analysis['details']['estimated_iv'] = iv
            if iv <= self.criteria.max_iv_for_debit: analysis['score'] += 2

            options_list = [opt for chain in option_chain.get('optionChain', []) for root in chain.get('chainPerRoot', []) for opt in root.get('chainPerStrikePrice', [])]
            option_volume = sum(opt.get('volume', 0) for opt in options_list)
            open_interest = sum(opt.get('openInterest', 0) for opt in options_list)
            if option_volume >= self.criteria.min_option_volume and open_interest >= self.criteria.min_open_interest: analysis['score'] += 2

            max_score = 11
            score_percentage = (analysis['score'] / max_score) * 100
            if score_percentage >= 70:
                analysis['recommendation'] = 'Highly Suitable'
                analysis['suggested_strikes'] = self._suggest_bear_put_strikes(quote.get('lastTradePrice'), option_chain)
            elif score_percentage >= 50:
                analysis['recommendation'] = 'Moderately Suitable'

            return analysis
        except Exception as e:
            return {'symbol': symbol, 'error': str(e)}

if __name__ == '__main__':
    from dotenv import load_dotenv
    import os
    from .questrade_data_fetcher import QuestradeCredentials
    
    load_dotenv()
    
    refresh_token = os.getenv('QUESTRADE_REFRESH_TOKEN')
    if not refresh_token:
        print("Error: QUESTRADE_REFRESH_TOKEN not found.")
        exit()

    credentials = QuestradeCredentials(refresh_token=refresh_token)
    
    try:
        fetcher = QuestradeDataFetcher(credentials)
        
        # --- Test Bull Call Spread Analyzer ---
        bull_analyzer = BullCallSpreadAnalyzer(fetcher)
        bull_candidates = bull_analyzer.find_suitable_stocks(['MSFT', 'AAPL', 'GOOG'])
        print("\n--- Suitable Bull Call Spread Candidates ---")
        if bull_candidates:
            for stock in bull_candidates:
                print(f"  - {stock['symbol']} (Price: ${stock['current_price']:.2f}): Score {stock['score']}/11, Momentum {stock['details']['momentum_score']:.2f}, Rec: '{stock['recommendation']}'")
                if stock.get('suggested_strikes'):
                    s = stock['suggested_strikes']
                    print(f"    - Suggested Spread (DTE: {s['days_to_expiry']}): Buy {s['long_call_strike']} Call / Sell {s['short_call_strike']} Call")
        else:
            print("  No suitable bullish candidates found in the sample.")

        # --- Test Bear Put Spread Analyzer ---
        bear_analyzer = BearPutSpreadAnalyzer(fetcher)
        bear_candidates = bear_analyzer.find_suitable_stocks(['INTC', 'PFE', 'DIS'])
        print("\n--- Suitable Bear Put Spread Candidates ---")
        if bear_candidates:
            for stock in bear_candidates:
                print(f"  - {stock['symbol']} (Price: ${stock['current_price']:.2f}): Score {stock['score']}/11, Momentum {stock['details']['momentum_score']:.2f}, Rec: '{stock['recommendation']}'")
                if stock.get('suggested_strikes'):
                    s = stock['suggested_strikes']
                    print(f"    - Suggested Spread (DTE: {s['days_to_expiry']}): Buy {s['long_put_strike']} Put / Sell {s['short_put_strike']} Put")
        else:
            print("  No suitable bearish candidates found in the sample.")

    except Exception as e:
        print(f"An error occurred: {e}") 