"""
Options Buying Strategy - Buy and Sell Options for Profit
This strategy identifies optimal entry points for buying options and determines when to sell them.
"""

import json
import math
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

    # Breakout handling.
    # The RSI signal is mean reversion: it buys puts on overbought names. A stock
    # clearing resistance is overbought by construction, so without this the
    # scanner fades every breakout (three NVDA puts in a week at RSI 66-84).
    # Resistance/support are the highest high / lowest low of the breakout_lookback
    # bars before a given day. A break stays in force for breakout_memory bars as
    # long as price holds beyond the broken level, since the day after a breakout
    # the stock is usually consolidating under its new high, not printing another.
    breakout_lookback: int = 20
    breakout_memory: int = 5
    # A breakout trade is abandoned once the underlying falls back through the
    # level it broke by more than this fraction (a plain retest is tolerated).
    breakout_failure_buffer: float = 0.01

    # Bollinger Bands on daily closes.
    bollinger_period: int = 20
    bollinger_std: float = 2.0

    # Theta burn: daily decay as a fraction of the premium paid. An at-the-money
    # option loses roughly 1/(2*DTE) of its value per day, so 5% is about 10 DTE
    # and 2.5% about 20 DTE.
    max_theta_burn: float = 0.05
    low_theta_burn: float = 0.025
    
    # Position Sizing
    max_risk_per_trade: float = 2.0  # 2% of account
    profit_target_multiplier: float = 2.0  # 2:1 risk/reward
    # 50% loss stop. Tighter is tempting but backfires: these contracts swing
    # far enough that a 30% stop gets tripped by noise, and replaying closed
    # trades against their hourly bars put -30% about $3k behind -50% because
    # it forced exits on positions that dipped and went on to be the biggest
    # winners. Both tighter and looser measured worse, including no stop at all.
    stop_loss_multiplier: float = 0.5

    # Scaling out to a free ride.
    # Expectancy here rests on rare outsized winners, so closing the whole
    # position at a fixed gain caps the trades that pay for everything else.
    # Instead, once price reaches scale_out_multiplier, sell only the contracts
    # needed to take the original cash back off the table and let the rest run
    # with no target and no stop - it is playing with money already banked.
    scale_out_multiplier: float = 1.5  # trigger at +50%
    scale_out_recoup_multiple: float = 1.0  # 1.0 = recover the entry cost exactly

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
                                    **self._leg_greeks(call_data),
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
                                    **self._leg_greeks(put_data),
                                })
            
            return {'options': suitable_options}
            
        except Exception as e:
            return {'error': f'Error analyzing option chain: {str(e)}'}

    @staticmethod
    def _leg_greeks(leg: Dict) -> Dict[str, Optional[float]]:
        """Broker-supplied Greeks for a leg; None where the feed has none."""
        return {greek: leg.get(greek) for greek in ('delta', 'gamma', 'theta', 'vega')}

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
        delta = abs(self._option_delta(option_data, option_type, current_price, strike_price, iv, dte))
        if delta < self.criteria.min_delta or delta > self.criteria.max_delta:
            return False

        return True
    
    def _option_delta(
        self, leg: Dict, option_type: str, spot: float, strike: float, iv: float, dte: int
    ) -> float:
        """
        Signed delta. Uses the broker's figure, which comes from a real pricing
        model; the closed-form approximation below is only a fallback for the
        legs the feed returns without Greeks.
        """
        delta = leg.get('delta')
        if delta is not None:
            return float(delta)
        if option_type == 'call':
            return self._calculate_call_delta(spot, strike, iv, dte)
        return self._calculate_put_delta(spot, strike, iv, dte)

    def _price_structure(self, bars: List[Dict], current_price: float) -> Dict[str, Any]:
        """
        Where price sits relative to its recent range: whether it has broken
        resistance or support, and its position inside the Bollinger Bands.

        regime is 'breakout_up', 'breakout_down' or 'range'. percent_b is 0 at
        the lower band and 1 at the upper, and runs outside 0-1 beyond them.
        """
        structure: Dict[str, Any] = {
            'regime': 'range', 'breakout_level': None,
            'bollinger_upper': None, 'bollinger_lower': None, 'percent_b': None,
        }
        closes = [float(bar['close']) for bar in bars]
        if not closes:
            return structure
        # The last bar is the session in progress, so the live price stands in
        # for its close.
        closes[-1] = current_price

        period = self.criteria.bollinger_period
        if len(closes) >= period:
            window = closes[-period:]
            middle = float(np.mean(window))
            width = float(np.std(window)) * self.criteria.bollinger_std
            structure['bollinger_upper'] = middle + width
            structure['bollinger_lower'] = middle - width
            if width > 0:
                structure['percent_b'] = (current_price - (middle - width)) / (2 * width)

        lookback, memory = self.criteria.breakout_lookback, self.criteria.breakout_memory
        if len(bars) < lookback + memory:
            return structure
        highs = [float(bar['high']) for bar in bars]
        lows = [float(bar['low']) for bar in bars]

        # Oldest day first: the first break in a direction fixes the level (that
        # is the resistance that actually gave way), and a later break the other
        # way replaces it.
        regime, level = 'range', None
        for i in range(len(bars) - memory, len(bars)):
            if closes[i] > max(highs[i - lookback:i]):
                if regime != 'breakout_up':
                    regime, level = 'breakout_up', max(highs[i - lookback:i])
            elif closes[i] < min(lows[i - lookback:i]):
                if regime != 'breakout_down':
                    regime, level = 'breakout_down', min(lows[i - lookback:i])

        still_beyond = (
            (regime == 'breakout_up' and current_price > level)
            or (regime == 'breakout_down' and current_price < level)
        )
        if still_beyond:
            structure['regime'], structure['breakout_level'] = regime, level
        return structure

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
            
            delta = self._option_delta(option, option['type'], current_price, strike, iv, dte)
            if option['type'] == 'call':
                intrinsic_value = max(0, current_price - strike)
            else:  # put
                intrinsic_value = max(0, strike - current_price)
            
            time_value = mid_price - intrinsic_value
            
            # |delta| is the standard proxy for the chance of finishing in the
            # money. The home-made estimate is kept only for legs without Greeks:
            # it reads ~0.8 for an at-the-money contract, so it flatters everything.
            if option.get('delta') is not None:
                prob_profit = abs(delta)
            else:
                prob_profit = self._calculate_probability_of_profit(
                    current_price, strike, iv, dte, option['type']
                )

            theta = option.get('theta')
            theta_burn = abs(theta) / mid_price if theta is not None and mid_price > 0 else None
            
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
                'gamma': option.get('gamma'),
                'theta': theta,
                'vega': option.get('vega'),
                'theta_burn': theta_burn,
                'time_decay_daily': abs(theta) if theta is not None else (time_value / dte if dte > 0 else 0)
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
            structure = self._price_structure(historical_data, current_price)
            
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

            # On a breakout only trade with it: calls above broken resistance,
            # puts below broken support. The opposing side is not scored at all.
            with_breakout = {'breakout_up': 'call', 'breakout_down': 'put'}.get(structure['regime'])
            if with_breakout:
                suitable_options = [o for o in suitable_options if o['type'] == with_breakout]
                if not suitable_options:
                    return {
                        'symbol': symbol,
                        'error': (
                            f"{structure['regime']} through {structure['breakout_level']:.2f} "
                            f"but no suitable {with_breakout}s"
                        ),
                    }

            # Score the opportunities
            best_opportunities = []
            for option in suitable_options:
                metrics = self._calculate_option_metrics(symbol, option, current_price)
                if 'error' in metrics:
                    continue
                
                # Calculate opportunity score
                score = self._calculate_opportunity_score(option, metrics, rsi, momentum, structure)
                
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
                'structure': structure,
                'opportunities': best_opportunities[:5],  # Top 5
                'total_options': len(suitable_options)
            }
            
        except Exception as e:
            return {'symbol': symbol, 'error': f'Analysis error: {str(e)}'}
    
    def _calculate_opportunity_score(
        self,
        option: Dict,
        metrics: Dict,
        rsi: float,
        momentum: float,
        structure: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Calculate a score for the option opportunity"""
        score = 0.0
        structure = structure or {}
        regime = structure.get('regime', 'range')
        percent_b = structure.get('percent_b')
        is_call = option['type'] == 'call'

        if regime != 'range':
            # Trading with a breakout (the opposing side was already dropped).
            # It scores like a full RSI signal, and RSI itself is ignored: an
            # overbought reading is what a breakout looks like, not a reason to
            # mark the call down.
            score += 30
            # Price outside the band on the breakout side confirms the range is
            # expanding rather than just being poked.
            if percent_b is not None and (percent_b > 1 if is_call else percent_b < 0):
                score += 15
        # RSI scoring (prefer oversold for calls, overbought for puts)
        elif option['type'] == 'call':
            if rsi < 40:  # Oversold - good for calls
                score += 30
            elif rsi > 60:  # Overbought - bad for calls
                score -= 20
        else:  # put
            if rsi > 60:  # Overbought - good for puts
                score += 30
            elif rsi < 40:  # Oversold - bad for puts
                score -= 20

        # Inside a range a call near the lower band backs up the mean-reversion
        # read. Calls only: the mirror case, a put near the upper band, lost on
        # all eight entries since late September, so it earns nothing.
        if regime == 'range' and is_call and percent_b is not None and percent_b < 0.2:
            score += 15

        # Theta: how much of the premium is lost per day just by holding.
        theta_burn = metrics.get('theta_burn')
        if theta_burn is not None:
            if theta_burn > self.criteria.max_theta_burn:
                score -= 15
            elif theta_burn < self.criteria.low_theta_burn:
                score += 10
        
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
                        'structure': analysis['structure'],
                        'option': opp['option'],
                        'metrics': opp['metrics'],
                        'score': opp['score']
                    })
        
        # Sort by score and return top opportunities
        opportunities.sort(key=lambda x: x['score'], reverse=True)
        return opportunities
    
    def create_position(self, opportunity: Dict, position_size: float = 1.0) -> Dict:
        """Create a new options position"""
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
            # Set once the position has been scaled out to a free ride; the
            # banked proceeds live in realized_pnl so the final figure stays whole.
            'scaled_out': False,
            'realized_pnl': 0.0,
            'entry_momentum': opportunity['momentum'],
            'entry_rsi': opportunity['rsi'],
            # Why this contract was picked, kept so a trade can be explained later.
            'entry_score': opportunity.get('score'),
            'entry_regime': (opportunity.get('structure') or {}).get('regime', 'range'),
            'breakout_level': (opportunity.get('structure') or {}).get('breakout_level'),
            'entry_percent_b': (opportunity.get('structure') or {}).get('percent_b'),
            'entry_delta': opportunity['metrics'].get('delta'),
            'entry_theta': opportunity['metrics'].get('theta'),
            'entry_gamma': opportunity['metrics'].get('gamma'),
            'entry_vega': opportunity['metrics'].get('vega'),
        }
        
        self.open_positions.append(position)
        return position
    
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
        """
        Determine if position should be exited.

        A position that has been scaled out carries no profit target and no stop:
        its cost is already recovered, so the remainder is left alone to chase an
        outsized move and only time decay retires it.
        """
        profit_target = position.get('profit_target')
        if profit_target is not None and current_price >= profit_target:
            return "Profit Target Hit"

        stop_loss = position.get('stop_loss')
        if stop_loss is not None and current_price <= stop_loss:
            return "Stop Loss Hit"

        # Too few contracts to sell part and keep a runner, so bank the whole
        # gain rather than leave it exposed to a round trip it cannot hedge.
        if self._at_scale_out_price(position, current_price) and self._cost_recovery_quantity(
            position, current_price
        ) >= int(position['position_size']):
            return "Profit Exit - position too small to scale out"

        # Time decay exit (close within 5 days of expiry)
        days_held = (datetime.now() - position['entry_date']).days
        if position['dte'] - days_held <= 5:
            return "Time Decay Exit"

        # Technical exit signals
        technical_exit = self._check_technical_exit_signals(position)
        if technical_exit:
            return technical_exit

        return None

    def _at_scale_out_price(self, position: Dict, current_price: float) -> bool:
        return (
            not position.get('scaled_out')
            and current_price >= position['entry_price'] * self.criteria.scale_out_multiplier
        )

    def _cost_recovery_quantity(self, position: Dict, current_price: float) -> int:
        """Contracts that must be sold at current_price to return the entry cost."""
        quantity = int(position['position_size'])
        recoup = position['entry_price'] * quantity * self.criteria.scale_out_recoup_multiple
        return math.ceil(recoup / current_price)

    def evaluate_scale_out(self, position: Dict, current_price: float) -> int:
        """
        Contracts to sell to take the original cash back off the table, or 0.

        Selling qty * entry / current contracts returns the entry cost at the
        current price, so the higher the price runs before this triggers, the
        fewer contracts it takes and the bigger the free-riding remainder.
        Returns 0 when no runner would be left over, which is handled as a full
        exit by _should_exit_position instead.
        """
        if not self._at_scale_out_price(position, current_price):
            return 0

        to_sell = self._cost_recovery_quantity(position, current_price)
        if to_sell >= int(position['position_size']):
            return 0
        return to_sell

    def scale_out_position(self, position: Dict, current_price: float, quantity: int) -> None:
        """Bank the proceeds of a partial sale and free the remainder to run."""
        position['realized_pnl'] = position.get('realized_pnl', 0.0) + (
            (current_price - position['entry_price']) * quantity * OPTION_CONTRACT_MULTIPLIER
        )
        position['position_size'] = int(position['position_size']) - quantity
        position['scaled_out'] = True
        position['profit_target'] = None
        position['stop_loss'] = None

        print(
            f"Scaled out: {position['id']} - sold {quantity} at ${current_price:.2f}, "
            f"{position['position_size']} left running (banked ${position['realized_pnl']:.2f})"
        )
    
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

            # A breakout trade is held on a different thesis. RSI being stretched
            # is expected there, so the RSI exit below would close it the moment
            # it opened; what ends it is price falling back through the level.
            breakout_level = position.get('breakout_level')
            on_breakout = position.get('entry_regime', 'range') != 'range' and breakout_level
            if on_breakout:
                buffer = self.criteria.breakout_failure_buffer
                if position['option_type'] == 'call' and current_price < breakout_level * (1 - buffer):
                    return "Failed Breakout - back below resistance"
                if position['option_type'] == 'put' and current_price > breakout_level * (1 + buffer):
                    return "Failed Breakout - back above support"

            # RSI reversal signals
            if on_breakout:
                pass
            elif position['option_type'] == 'call':
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
            ) + position.get('realized_pnl', 0.0)
            
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
