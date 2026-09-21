"""
Options Buying Strategy - Buy and Sell Options for Profit
This strategy identifies optimal entry points for buying options and determines when to sell them.
"""

import json
import os
import pandas as pd
import numpy as np
from typing import Any, Dict, List, Optional, Protocol, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

OPTION_CONTRACT_MULTIPLIER = 100  # shares represented per option contract
DEFAULT_POSITIONS_STORE = "alpaca_sandbox_positions.json"


class OptionsDataFetcher(Protocol):
    def get_quote(self, symbol: str) -> Dict[str, Any]:
        ...

    def get_historical_data(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        period: Optional[str] = None,
        interval: str = "1d",
    ) -> List[Dict[str, Any]]:
        ...

    def get_option_chain(self, symbol: str) -> Dict[str, Any]:
        ...

@dataclass
class OptionsBuyCriteria:
    """
    Criteria for identifying optimal options buying opportunities
    """
    # Stock Criteria
    min_price: float = 20.0
    max_price: float = 500.0
    min_volume: int = 10000
    min_market_cap: float = 1_000_000_000  # $1B
    
    # Options Criteria
    min_days_to_expiry: int = 7  # TESTING value — raise to 21 (3 weeks) before going live.
    # Must stay > 5: the time-decay exit fires at (dte_at_entry - days_held) <= 5,
    # so anything at/below 5 forces an exit almost immediately after entry.
    max_days_to_expiry: int = 45  # Maximum 6.5 weeks
    min_option_volume: int = 100
    min_open_interest: int = 200
    max_bid_ask_spread: float = 0.20  # Max 20% spread
    
    # Volatility Criteria
    min_implied_volatility: float = 0.15  # 15% minimum
    max_implied_volatility: float = 0.80  # 80% maximum
    # Reject a symbol when its option premium is expensive relative to how much
    # that stock has actually been moving. Ranked against the name's own trailing
    # realized volatility, so it adapts per ticker rather than using a flat IV cut.
    # Long options lose value when implied volatility falls regardless of
    # direction, so buying into rich premium is a structural drag.
    # Calibrated against a 30-name sample where the rank distribution ran
    # median 56 / p75 68: this keeps ~70% of candidates and excludes the tail
    # (83+) where premium is genuinely rich for the name.
    max_iv_percentile: float = 65.0

    # Moneyness / Delta Criteria
    # Constrains contracts to a directional-leverage sweet spot. Without this,
    # nothing stops the scanner from picking deep-ITM contracts that are almost
    # pure intrinsic value (little leverage, and often too expensive to size
    # under max_risk_per_trade at all).
    min_delta: float = 0.30
    max_delta: float = 0.70
    
    # Technical Criteria
    momentum_period: int = 20
    rsi_period: int = 14
    min_rsi: float = 30
    max_rsi: float = 70
    
    # Position Sizing
    max_risk_per_trade: float = 2.0  # 2% of account
    profit_target_multiplier: float = 2.0  # 2:1 risk/reward
    stop_loss_multiplier: float = 0.7  # 30% loss stop

class OptionsBuyStrategy:
    """
    Strategy for buying options and finding optimal exit points
    """
    
    def __init__(self, data_fetcher: OptionsDataFetcher):
        self.data_fetcher = data_fetcher
        self.criteria = OptionsBuyCriteria()
        self._realized_vol_cache: Dict[str, List[float]] = {}

        # Track open positions
        self.open_positions = []
        self.closed_positions = []
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """Calculate RSI indicator"""
        if len(prices) < period + 1:
            return 50.0
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def _calculate_momentum(self, prices: List[float], period: int = 20) -> float:
        """Calculate momentum score"""
        if len(prices) < period:
            return 0.5
        
        current_price = prices[-1]
        past_price = prices[-period]
        
        momentum = (current_price - past_price) / past_price
        # Normalize to 0-1 scale
        normalized_momentum = 1 / (1 + np.exp(-momentum * 10))
        return normalized_momentum
    
    def _get_option_chain_analysis(self, symbol: str, current_price: float) -> Dict:
        """Get and analyze option chain for a symbol"""
        try:
            option_chain = self.data_fetcher.get_option_chain(symbol)
            
            if not option_chain or 'optionChain' not in option_chain:
                return {'error': 'No option chain data'}
            
            # Find options within our DTE range
            suitable_options = []
            option_quote_lookup = self._build_option_quote_lookup(option_chain)
            
            for chain in option_chain['optionChain']:
                expiry_date = pd.to_datetime(chain['expiryDate'])
                now = datetime.now(expiry_date.tz) if expiry_date.tzinfo else datetime.now()
                dte = (expiry_date - now).days
                
                if self.criteria.min_days_to_expiry <= dte <= self.criteria.max_days_to_expiry:
                    for root in chain.get('chainPerRoot', []):
                        for strike_data in root.get('chainPerStrikePrice', []):
                            strike_price = strike_data['strikePrice']
                            
                            # Analyze calls
                            call_data = self._resolve_option_leg_data(strike_data, "call", option_quote_lookup)
                            if call_data and self._is_option_suitable(
                                call_data, strike_price, "call", current_price, dte
                            ):
                                suitable_options.append({
                                    'type': 'call',
                                    'strike': strike_price,
                                    'dte': dte,
                                    'bid': call_data.get('bid', 0),
                                    'ask': call_data.get('ask', 0),
                                    'volume': call_data.get('volume', 0),
                                    'open_interest': call_data.get('openInterest', call_data.get('open_interest', 0)),
                                    'iv': call_data.get('volatility', call_data.get('impliedVolatility', 0)),
                                })

                            # Analyze puts
                            put_data = self._resolve_option_leg_data(strike_data, "put", option_quote_lookup)
                            if put_data and self._is_option_suitable(
                                put_data, strike_price, "put", current_price, dte
                            ):
                                suitable_options.append({
                                    'type': 'put',
                                    'strike': strike_price,
                                    'dte': dte,
                                    'bid': put_data.get('bid', 0),
                                    'ask': put_data.get('ask', 0),
                                    'volume': put_data.get('volume', 0),
                                    'open_interest': put_data.get('openInterest', put_data.get('open_interest', 0)),
                                    'iv': put_data.get('volatility', put_data.get('impliedVolatility', 0)),
                                })
            
            return {'options': suitable_options}
            
        except Exception as e:
            return {'error': f'Error analyzing option chain: {str(e)}'}

    def _build_option_quote_lookup(self, option_chain: Dict) -> Dict[int, Dict]:
        """
        Build option quote lookup by symbolId.
        Supports chains that already include quote blocks and falls back to
        fetching by callSymbolId/putSymbolId when available.
        """
        lookup: Dict[int, Dict] = {}

        for key in ("quotes", "optionQuotes"):
            for quote in option_chain.get(key, []):
                symbol_id = quote.get("symbolId")
                if symbol_id is not None:
                    lookup[int(symbol_id)] = quote

        symbol_ids = []
        for chain in option_chain.get("optionChain", []):
            for root in chain.get("chainPerRoot", []):
                for strike_data in root.get("chainPerStrikePrice", []):
                    call_id = strike_data.get("callSymbolId") or strike_data.get("callsymbolId")
                    put_id = strike_data.get("putSymbolId") or strike_data.get("putsymbolId")
                    if call_id is not None:
                        symbol_ids.append(int(call_id))
                    if put_id is not None:
                        symbol_ids.append(int(put_id))

        if symbol_ids and hasattr(self.data_fetcher, "get_quotes_by_ids"):
            try:
                fetched = self.data_fetcher.get_quotes_by_ids(symbol_ids)
                lookup.update(fetched)
            except Exception:
                pass

        return lookup

    def _resolve_option_leg_data(
        self, strike_data: Dict, option_type: str, quote_lookup: Dict[int, Dict]
    ) -> Dict:
        """Resolve leg data from nested leg block or callSymbolId/putSymbolId lookup."""
        leg = strike_data.get(option_type, {})
        if leg:
            return leg

        symbol_key = f"{option_type}SymbolId"
        symbol_key_alt = f"{option_type}symbolId"
        symbol_id = strike_data.get(symbol_key) or strike_data.get(symbol_key_alt)
        if symbol_id is None:
            return {}

        return quote_lookup.get(int(symbol_id), {})
    
    def _is_option_suitable(
        self,
        option_data: Dict,
        strike_price: float,
        option_type: str,
        current_price: float,
        dte: int,
    ) -> bool:
        """Check if an individual option meets our criteria"""
        bid = option_data.get('bid', 0)
        ask = option_data.get('ask', 0)
        volume = option_data.get('volume', 0)
        open_interest = option_data.get('openInterest', 0)
        iv = option_data.get('impliedVolatility', option_data.get('volatility', 0))

        if bid <= 0 or ask <= 0:
            return False

        # Check bid-ask spread
        spread = (ask - bid) / ask
        if spread > self.criteria.max_bid_ask_spread:
            return False

        # Check volume and open interest
        if volume < self.criteria.min_option_volume:
            return False

        if open_interest < self.criteria.min_open_interest:
            return False

        # Check implied volatility
        if iv < self.criteria.min_implied_volatility or iv > self.criteria.max_implied_volatility:
            return False

        # Check moneyness via delta: excludes deep-ITM contracts (almost pure
        # intrinsic value, little leverage, often too expensive to size under
        # max_risk_per_trade) and deep-OTM contracts (low probability of profit).
        if option_type == 'call':
            delta = self._calculate_call_delta(current_price, strike_price, iv, dte)
        else:
            delta = -self._calculate_put_delta(current_price, strike_price, iv, dte)  # magnitude: put_delta is <= 0
        if delta < self.criteria.min_delta or delta > self.criteria.max_delta:
            return False

        return True
    
    def _realized_volatility_distribution(self, symbol: str, window: int = 20) -> List[float]:
        """
        Rolling annualized realized volatility over the past year, used as the
        reference distribution for ranking today's implied volatility.

        True IV percentile would rank IV against a year of past IV readings, but
        that history is not available from the data feed, so the stock's own
        realized volatility stands in for it. The two are correlated but not
        equal: implied normally sits a few points above realized, which biases
        the resulting rank upward. max_iv_percentile is calibrated for that bias.
        """
        if symbol in self._realized_vol_cache:
            return self._realized_vol_cache[symbol]

        bars = self.data_fetcher.get_historical_data(symbol, period='1y', interval='1d') or []
        closes = [bar['close'] for bar in bars if bar.get('close')]

        distribution: List[float] = []
        if len(closes) > window + 1:
            returns = np.diff(np.log(closes))
            distribution = [
                float(np.std(returns[i - window:i]) * np.sqrt(252))
                for i in range(window, len(returns) + 1)
            ]

        self._realized_vol_cache[symbol] = distribution
        return distribution

    def _current_implied_volatility(
        self, options: List[Dict], current_price: float, sample_size: int = 6
    ) -> Optional[float]:
        """
        Median IV of the contracts nearest the money. Median rather than the
        single closest strike so one stale or crossed quote cannot swing the
        reading for the whole symbol.
        """
        nearest = sorted(options, key=lambda o: abs(o['strike'] - current_price))[:sample_size]
        ivs = [o['iv'] for o in nearest if o.get('iv')]
        return float(np.median(ivs)) if ivs else None

    def _implied_volatility_percentile(
        self, implied_volatility: float, distribution: List[float]
    ) -> Optional[float]:
        """Where implied volatility ranks within the reference distribution, 0-100."""
        if not distribution:
            return None
        below = sum(1 for value in distribution if value < implied_volatility)
        return 100.0 * below / len(distribution)

    def _calculate_option_metrics(self, symbol: str, option: Dict, current_price: float) -> Dict:
        """Calculate key metrics for an option"""
        try:
            # Calculate Greeks approximation
            strike = option['strike']
            iv = option['iv']
            dte = option['dte']
            mid_price = (option['bid'] + option['ask']) / 2
            
            # Simplified delta calculation
            if option['type'] == 'call':
                delta = self._calculate_call_delta(current_price, strike, iv, dte)
                intrinsic_value = max(0, current_price - strike)
            else:  # put
                delta = self._calculate_put_delta(current_price, strike, iv, dte)
                intrinsic_value = max(0, strike - current_price)
            
            time_value = mid_price - intrinsic_value
            
            # Calculate probability of profit (simplified)
            prob_profit = self._calculate_probability_of_profit(
                current_price, strike, iv, dte, option['type']
            )
            
            # Calculate risk/reward
            max_risk = mid_price
            potential_profit = intrinsic_value if intrinsic_value > 0 else mid_price * 2
            risk_reward = potential_profit / max_risk if max_risk > 0 else 0
            
            return {
                'mid_price': mid_price,
                'delta': delta,
                'intrinsic_value': intrinsic_value,
                'time_value': time_value,
                'prob_profit': prob_profit,
                'risk_reward': risk_reward,
                'time_decay_daily': time_value / dte if dte > 0 else 0
            }
            
        except Exception as e:
            return {'error': f'Error calculating metrics: {str(e)}'}
    
    def _calculate_call_delta(self, spot: float, strike: float, iv: float, dte: int) -> float:
        """Simplified call delta calculation"""
        try:
            # Using simplified Black-Scholes delta approximation
            d1 = (np.log(spot / strike) + (iv**2 / 2) * (dte / 365)) / (iv * np.sqrt(dte / 365))
            delta = 0.5 * (1 + np.tanh(d1))  # Sigmoid approximation of normal CDF
            return delta
        except:
            return 0.5
    
    def _calculate_put_delta(self, spot: float, strike: float, iv: float, dte: int) -> float:
        """Simplified put delta calculation"""
        call_delta = self._calculate_call_delta(spot, strike, iv, dte)
        return call_delta - 1
    
    def _calculate_probability_of_profit(self, spot: float, strike: float, iv: float, dte: int, option_type: str) -> float:
        """Calculate probability of profit"""
        try:
            # Simplified probability calculation
            expected_move = spot * iv * np.sqrt(dte / 365)
            
            if option_type == 'call':
                # Probability spot moves above strike
                prob = 1 / (1 + np.exp(-(spot - strike + expected_move/2) / (expected_move/3)))
            else:  # put
                # Probability spot moves below strike
                prob = 1 / (1 + np.exp((spot - strike - expected_move/2) / (expected_move/3)))
            
            return max(0.1, min(0.9, prob))
        except:
            return 0.5
    
    def analyze_stock_for_options(self, symbol: str) -> Dict:
        """Analyze a stock for options buying opportunities"""
        try:
            # Get stock data
            quote = self.data_fetcher.get_quote(symbol)
            if not quote:
                return {'symbol': symbol, 'error': 'Could not fetch quote'}
            
            current_price = quote['lastTradePrice']
            volume = quote.get('volume', 0)
            
            # Check basic stock criteria
            if current_price < self.criteria.min_price or current_price > self.criteria.max_price:
                return {'symbol': symbol, 'error': f'Price ${current_price:.2f} outside range'}
            
            if volume < self.criteria.min_volume:
                return {'symbol': symbol, 'error': f'Volume {volume:,} too low'}
            
            # Get historical data for technical analysis
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=100)).strftime('%Y-%m-%d')
            historical_data = self.data_fetcher.get_historical_data(symbol, start_date, end_date)
            
            if not historical_data:
                return {'symbol': symbol, 'error': 'Could not fetch historical data'}
            
            prices = [bar['close'] for bar in historical_data]
            rsi = self._calculate_rsi(prices, self.criteria.rsi_period)
            momentum = self._calculate_momentum(prices, self.criteria.momentum_period)
            
            # Get option chain analysis
            option_analysis = self._get_option_chain_analysis(symbol, current_price)
            if 'error' in option_analysis:
                return {'symbol': symbol, 'error': option_analysis['error']}
            
            suitable_options = option_analysis['options']
            if not suitable_options:
                return {'symbol': symbol, 'error': 'No suitable options found'}

            implied_volatility = self._current_implied_volatility(suitable_options, current_price)
            iv_percentile = None
            if implied_volatility is not None:
                iv_percentile = self._implied_volatility_percentile(
                    implied_volatility, self._realized_volatility_distribution(symbol)
                )
            if iv_percentile is not None and iv_percentile > self.criteria.max_iv_percentile:
                return {
                    'symbol': symbol,
                    'error': (
                        f'Premium too rich: IV {implied_volatility:.1%} ranks at the '
                        f'{iv_percentile:.0f}th percentile of this name\'s realized '
                        f'volatility (max {self.criteria.max_iv_percentile:.0f})'
                    ),
                }

            # Score the opportunities
            best_opportunities = []
            for option in suitable_options:
                metrics = self._calculate_option_metrics(symbol, option, current_price)
                if 'error' in metrics:
                    continue
                
                # Calculate opportunity score
                score = self._calculate_opportunity_score(option, metrics, rsi, momentum)
                
                best_opportunities.append({
                    'option': option,
                    'metrics': metrics,
                    'score': score
                })
            
            # Sort by score and return top opportunities
            best_opportunities.sort(key=lambda x: x['score'], reverse=True)
            
            return {
                'symbol': symbol,
                'current_price': current_price,
                'rsi': rsi,
                'momentum': momentum,
                'implied_volatility': implied_volatility,
                'iv_percentile': iv_percentile,
                'opportunities': best_opportunities[:5],  # Top 5
                'total_options': len(suitable_options)
            }
            
        except Exception as e:
            return {'symbol': symbol, 'error': f'Analysis error: {str(e)}'}
    
    def _calculate_opportunity_score(self, option: Dict, metrics: Dict, rsi: float, momentum: float) -> float:
        """Calculate a score for the option opportunity"""
        score = 0.0
        
        # RSI scoring (prefer oversold for calls, overbought for puts)
        if option['type'] == 'call':
            if rsi < 40:  # Oversold - good for calls
                score += 30
            elif rsi > 60:  # Overbought - bad for calls
                score -= 20
        else:  # put
            if rsi > 60:  # Overbought - good for puts
                score += 30
            elif rsi < 40:  # Oversold - bad for puts
                score -= 20
        
        # Momentum scoring
        if option['type'] == 'call':
            score += momentum * 20  # Higher momentum = better for calls
        else:  # put
            score += (1 - momentum) * 20  # Lower momentum = better for puts
        
        # Risk/reward scoring
        if metrics['risk_reward'] > 2.0:
            score += 25
        elif metrics['risk_reward'] > 1.5:
            score += 15
        elif metrics['risk_reward'] < 1.0:
            score -= 15
        
        # Probability of profit
        if metrics['prob_profit'] > 0.6:
            score += 20
        elif metrics['prob_profit'] < 0.4:
            score -= 15
        
        # Time value (prefer less time value for buying)
        time_value_ratio = metrics['time_value'] / metrics['mid_price']
        if time_value_ratio < 0.5:  # Less than 50% time value
            score += 15
        
        # Volume and open interest
        if option['volume'] > 1000:
            score += 10
        if option['open_interest'] > 1000:
            score += 10

        return max(0, score)  # Don't allow negative scores
    
    def find_best_opportunities(self, symbols: List[str]) -> List[Dict]:
        """Find the best options buying opportunities from a list of symbols"""
        opportunities = []
        
        for symbol in symbols:
            analysis = self.analyze_stock_for_options(symbol)
            
            if 'error' not in analysis and analysis.get('opportunities'):
                for opp in analysis['opportunities']:
                    opportunities.append({
                        'symbol': symbol,
                        'current_price': analysis['current_price'],
                        'rsi': analysis['rsi'],
                        'momentum': analysis['momentum'],
                        'option': opp['option'],
                        'metrics': opp['metrics'],
                        'score': opp['score']
                    })
        
        # Sort by score and return top opportunities
        opportunities.sort(key=lambda x: x['score'], reverse=True)
        return opportunities
    
    def create_position(self, opportunity: Dict, position_size: float = 1.0) -> Dict:
        """Create a new options position"""
        # Get technical levels for exit signals
        support_level, resistance_level = self._get_support_resistance_levels(opportunity['symbol'])
        
        position = {
            'id': f"{opportunity['symbol']}_{opportunity['option']['type']}_{opportunity['option']['strike']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            'symbol': opportunity['symbol'],
            'option_type': opportunity['option']['type'],
            'strike': opportunity['option']['strike'],
            'dte': opportunity['option']['dte'],
            'entry_price': opportunity['metrics']['mid_price'],
            'entry_date': datetime.now(),
            'position_size': position_size,
            'max_risk': opportunity['metrics']['mid_price'] * position_size * OPTION_CONTRACT_MULTIPLIER,
            'profit_target': opportunity['metrics']['mid_price'] * self.criteria.profit_target_multiplier,
            'stop_loss': opportunity['metrics']['mid_price'] * self.criteria.stop_loss_multiplier,
            'status': 'open',
            'current_pnl': 0.0,
            'exit_price': None,
            'exit_date': None,
            'exit_reason': None,
            # Technical levels for exit signals
            'support_level': support_level,
            'resistance_level': resistance_level,
            'entry_momentum': opportunity['momentum'],
            'entry_rsi': opportunity['rsi']
        }
        
        self.open_positions.append(position)
        return position
    
    def _get_support_resistance_levels(self, symbol: str) -> Tuple[Optional[float], Optional[float]]:
        """Get support and resistance levels for technical exit signals"""
        try:
            historical_data = self.data_fetcher.get_historical_data(symbol, period='3M', interval='1d')
            if not historical_data or len(historical_data) < 20:
                return None, None
            
            prices = [float(candle['close']) for candle in historical_data]
            
            # Simple support/resistance calculation using recent highs and lows
            recent_highs = []
            recent_lows = []
            
            # Look at last 20 days for levels
            lookback = min(20, len(prices))
            for i in range(len(prices) - lookback, len(prices) - 1):
                if prices[i] > prices[i-1] and prices[i] > prices[i+1]:
                    recent_highs.append(prices[i])
                elif prices[i] < prices[i-1] and prices[i] < prices[i+1]:
                    recent_lows.append(prices[i])
            
            # Calculate levels
            resistance = max(recent_highs) if recent_highs else None
            support = min(recent_lows) if recent_lows else None
            
            return support, resistance
            
        except Exception as e:
            print(f"Error calculating support/resistance for {symbol}: {e}")
            return None, None
    
    def monitor_positions(self) -> List[Dict]:
        """Monitor open positions and identify exit opportunities"""
        positions_to_close = []
        
        for position in self.open_positions:
            try:
                # Get current option price
                current_price = self._get_current_option_price(position)
                if current_price is None:
                    continue
                
                # Calculate current P&L
                current_pnl = (
                    (current_price - position['entry_price'])
                    * position['position_size']
                    * OPTION_CONTRACT_MULTIPLIER
                )
                position['current_pnl'] = current_pnl
                
                # Check exit conditions
                exit_reason = self._should_exit_position(position, current_price)
                if exit_reason:
                    positions_to_close.append({
                        'position': position,
                        'current_price': current_price,
                        'exit_reason': exit_reason
                    })
                
            except Exception as e:
                print(f"Error monitoring position {position['id']}: {e}")
        
        return positions_to_close
    
    def _get_current_option_price(self, position: Dict) -> Optional[float]:
        """Get current option price from market data"""
        try:
            option_chain = self.data_fetcher.get_option_chain(position['symbol'])
            if not option_chain:
                return None
            option_quote_lookup = self._build_option_quote_lookup(option_chain)
            
            # Find the matching option
            for chain in option_chain.get('optionChain', []):
                for root in chain.get('chainPerRoot', []):
                    for strike_data in root.get('chainPerStrikePrice', []):
                        if strike_data['strikePrice'] == position['strike']:
                            option_data = self._resolve_option_leg_data(
                                strike_data, position['option_type'], option_quote_lookup
                            )
                            if option_data:
                                return (option_data.get('bid', 0) + option_data.get('ask', 0)) / 2
            
            return None
        except Exception as e:
            print(f"Error getting current option price: {e}")
            return None
    
    def evaluate_exit(self, position: Dict, current_price: float) -> Optional[str]:
        """Public entry point for exit-condition checks against an externally-sourced current price."""
        return self._should_exit_position(position, current_price)

    def _should_exit_position(self, position: Dict, current_price: float) -> Optional[str]:
        """Determine if position should be exited"""
        current_pnl = (current_price - position['entry_price']) * position['position_size']
        pnl_percent = current_pnl / position['max_risk'] * 100
        
        # Profit target hit
        if current_price >= position['profit_target']:
            return "Profit Target Hit"
        
        # Stop loss hit
        if current_price <= position['stop_loss']:
            return "Stop Loss Hit"
        
        # Time decay exit (close within 5 days of expiry)
        days_held = (datetime.now() - position['entry_date']).days
        if position['dte'] - days_held <= 5:
            return "Time Decay Exit"
        
        # High profit exit (50% profit)
        if pnl_percent >= 50:
            return "High Profit Exit"
        
        # Technical exit signals
        technical_exit = self._check_technical_exit_signals(position)
        if technical_exit:
            return technical_exit
        
        return None
    
    def _check_technical_exit_signals(self, position: Dict) -> Optional[str]:
        """Check for technical exit signals"""
        try:
            # Get recent price data for technical analysis
            historical_data = self.data_fetcher.get_historical_data(
                position['symbol'], 
                period='1M', 
                interval='1d'
            )
            
            if not historical_data or len(historical_data) < 20:
                return None
            
            prices = [float(candle['close']) for candle in historical_data]
            current_price = prices[-1]
            
            # Calculate current RSI
            rsi = self._calculate_rsi(prices)
            
            # RSI reversal signals
            if position['option_type'] == 'call':
                # For calls, exit if RSI becomes overbought (>70) and starts declining
                if rsi > 70 and len(prices) >= 3:
                    recent_rsi = self._calculate_rsi(prices[-3:])
                    if recent_rsi < rsi:  # RSI declining from overbought
                        return "RSI Reversal Signal"
            
            elif position['option_type'] == 'put':
                # For puts, exit if RSI becomes oversold (<30) and starts rising
                if rsi < 30 and len(prices) >= 3:
                    recent_rsi = self._calculate_rsi(prices[-3:])
                    if recent_rsi > rsi:  # RSI rising from oversold
                        return "RSI Reversal Signal"
            
            # Support/Resistance breaks
            resistance_level = position.get('resistance_level')
            support_level = position.get('support_level')
            
            if resistance_level and current_price > resistance_level * 1.02:  # 2% break
                if position['option_type'] == 'put':
                    return "Resistance Break - Put Exit"
            
            if support_level and current_price < support_level * 0.98:  # 2% break
                if position['option_type'] == 'call':
                    return "Support Break - Call Exit"
            
            # Momentum divergence (simplified)
            momentum = self._calculate_momentum(prices)
            entry_momentum = position.get('entry_momentum', 0.5)
            
            if position['option_type'] == 'call' and momentum < entry_momentum * 0.7:
                return "Momentum Divergence - Call Exit"
            elif position['option_type'] == 'put' and momentum > entry_momentum * 1.3:
                return "Momentum Divergence - Put Exit"
            
            return None
            
        except Exception as e:
            print(f"Error checking technical exit signals: {e}")
            return None
    
    def close_position(self, position_data: Dict) -> bool:
        """Close a position"""
        try:
            position = position_data['position']
            current_price = position_data['current_price']
            exit_reason = position_data['exit_reason']
            
            # Update position
            position['exit_price'] = current_price
            position['exit_date'] = datetime.now()
            position['exit_reason'] = exit_reason
            position['status'] = 'closed'
            position['final_pnl'] = (
                (current_price - position['entry_price'])
                * position['position_size']
                * OPTION_CONTRACT_MULTIPLIER
            )
            
            # Move to closed positions
            self.open_positions.remove(position)
            self.closed_positions.append(position)
            
            print(f"Position closed: {position['id']} - {exit_reason}")
            print(f"P&L: ${position['final_pnl']:.2f} ({position['final_pnl']/position['max_risk']*100:.1f}%)")
            
            return True
            
        except Exception as e:
            print(f"Error closing position: {e}")
            return False

    def save_positions(self, path: str = DEFAULT_POSITIONS_STORE) -> None:
        """Persist open/closed positions to disk so they survive across separate CLI runs."""
        def _default(value: Any) -> str:
            if isinstance(value, datetime):
                return value.isoformat()
            return str(value)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"open_positions": self.open_positions, "closed_positions": self.closed_positions},
                f,
                indent=2,
                default=_default,
            )

    def load_positions(self, path: str = DEFAULT_POSITIONS_STORE) -> None:
        """Load previously persisted open/closed positions, if present."""
        if not os.path.exists(path):
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        def _parse_dates(position: Dict) -> Dict:
            for key in ("entry_date", "exit_date"):
                value = position.get(key)
                if isinstance(value, str):
                    position[key] = datetime.fromisoformat(value)
            return position

        self.open_positions = [_parse_dates(p) for p in data.get("open_positions", [])]
        self.closed_positions = [_parse_dates(p) for p in data.get("closed_positions", [])]

if __name__ == '__main__':
    try:
        try:
            from .questrade_data_fetcher import QuestradeDataFetcher
        except ImportError:
            from questrade_data_fetcher import QuestradeDataFetcher

        fetcher = QuestradeDataFetcher()
        strategy = OptionsBuyStrategy(fetcher)
        
        # Test symbols
        test_symbols = ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA']
        
        print("=" * 60)
        print("OPTIONS BUYING STRATEGY - FINDING OPPORTUNITIES")
        print("=" * 60)
        
        opportunities = strategy.find_best_opportunities(test_symbols)
        
        if opportunities:
            print(f"\nFound {len(opportunities)} opportunities:")
            print("-" * 80)
            
            for i, opp in enumerate(opportunities[:10], 1):  # Show top 10
                option = opp['option']
                metrics = opp['metrics']
                
                print(f"\n{i}. {opp['symbol']} - {option['type'].upper()} ${option['strike']} ({option['dte']} DTE)")
                print(f"   Current Price: ${opp['current_price']:.2f}")
                print(f"   Option Price: ${metrics['mid_price']:.2f}")
                print(f"   Delta: {metrics['delta']:.3f}")
                print(f"   Prob Profit: {metrics['prob_profit']:.1%}")
                print(f"   Risk/Reward: {metrics['risk_reward']:.2f}")
                print(f"   RSI: {opp['rsi']:.1f}, Momentum: {opp['momentum']:.3f}")
                print(f"   Score: {opp['score']:.1f}")
        else:
            print("No suitable opportunities found.")
        
        print(f"\nStrategy ready for live trading!")
        
    except Exception as e:
        print(f"Error running strategy: {e}")
