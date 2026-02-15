import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import time

from investment_strategies.questrade_data_fetcher import QuestradeDataFetcher
from investment_strategies.iron_condor_analyzer import IronCondorAnalyzer
from investment_strategies.strategy_config import StrategyConfig

class ETFIronCondorStrategy:
    """
    ETF Iron Condor Strategy Implementation
    
    Strategy Overview:
    1. Scan 10 uncorrelated ETFs for iron condor opportunities
    2. Sell up to 6 iron condors targeting monthly contracts
    3. Target 70% probability of profit with <$300 risk
    4. Take profits at 30% when possible
    """
    
    def __init__(self, data_fetcher: QuestradeDataFetcher, config: StrategyConfig = None):
        self.data_fetcher = data_fetcher
        self.analyzer = IronCondorAnalyzer(data_fetcher)
        self.config = config or StrategyConfig()
        
        # Strategy Parameters from config
        self.max_positions = self.config.MAX_POSITIONS
        self.target_probability = self.config.TARGET_PROBABILITY_OF_PROFIT
        self.max_risk = self.config.MAX_RISK_PER_POSITION
        self.profit_target = self.config.PROFIT_TARGET_PERCENT / 100
        self.monthly_dte_target = self.config.TARGET_DAYS_TO_EXPIRY
        
        # ETF universe from config
        self.etf_universe = self.config.ETF_UNIVERSE
        
        # Position tracking
        self.current_positions = []
        self.position_history = []
    
    def get_current_positions(self) -> List[Dict]:
        """Get current iron condor positions from account"""
        try:
            positions = self.data_fetcher.get_account_positions()
            
            if not positions:
                print("No positions found in account (this is normal for new accounts)")
                return []
            
            iron_condor_positions = []
            
            for position in positions:
                # Filter for iron condor positions (would need position type identification)
                if self._is_iron_condor_position(position):
                    iron_condor_positions.append(position)
            
            print(f"Found {len(iron_condor_positions)} iron condor positions out of {len(positions)} total positions")
            return iron_condor_positions
            
        except Exception as e:
            print(f"Error fetching current positions: {e}")
            print("This could be due to:")
            print("  - No Questrade account linked")
            print("  - API permissions issues")
            print("  - Network connectivity problems")
            return []
    
    def _is_iron_condor_position(self, position: Dict) -> bool:
        """Check if a position is an iron condor (simplified logic)"""
        # This would need to be implemented based on your position structure
        # For now, we'll assume any option position could be part of an iron condor
        return position.get('productType') == 'Option'
    
    def calculate_iron_condor_metrics(self, symbol: str, strikes: Dict) -> Dict:
        """
        Calculate key metrics for an iron condor position:
        - Probability of profit
        - Maximum risk
        - Expected value
        - Alpha
        """
        try:
            # Get current market data
            quote = self.data_fetcher.get_quote(symbol)
            option_chain = self.data_fetcher.get_option_chain(symbol)
            
            if not quote or not option_chain:
                return None
            
            current_price = quote['lastTradePrice']
            
            # Calculate position metrics
            metrics = self._calculate_position_metrics(
                symbol, current_price, strikes, option_chain
            )
            
            return metrics
            
        except Exception as e:
            print(f"Error calculating iron condor metrics for {symbol}: {e}")
            return None
    
    def _calculate_position_metrics(self, symbol: str, current_price: float, 
                                  strikes: Dict, option_chain: Dict) -> Dict:
        """Calculate detailed position metrics"""
        
        # Extract option prices from chain
        options = self._extract_option_prices(option_chain, strikes)
        
        if not options:
            return None
        
        # Calculate position P&L
        short_put_premium = options['short_put']['bid']
        long_put_premium = options['long_put']['ask']
        short_call_premium = options['short_call']['bid']
        long_call_premium = options['long_call']['ask']
        
        # Net credit received
        net_credit = (short_put_premium + short_call_premium) - (long_put_premium + long_call_premium)
        
        # Maximum risk (width of spreads minus net credit)
        put_spread_width = strikes['short_put_strike'] - strikes['long_put_strike']
        call_spread_width = strikes['long_call_strike'] - strikes['short_call_strike']
        max_risk = max(put_spread_width, call_spread_width) - net_credit
        
        # Calculate probability of profit (simplified)
        # This would need more sophisticated calculation using option pricing models
        prob_profit = self._estimate_probability_of_profit(
            current_price, strikes, options
        )
        
        # Calculate expected value
        expected_value = (prob_profit * net_credit) - ((1 - prob_profit) * max_risk)
        
        # Calculate alpha (excess return vs risk-free rate)
        risk_free_rate = 0.05  # Assume 5% risk-free rate
        days_to_expiry = strikes['days_to_expiry']
        alpha = (expected_value / max_risk) - (risk_free_rate * days_to_expiry / 365)
        
        return {
            'symbol': symbol,
            'current_price': current_price,
            'net_credit': net_credit,
            'max_risk': max_risk,
            'probability_of_profit': prob_profit,
            'expected_value': expected_value,
            'alpha': alpha,
            'risk_reward_ratio': net_credit / max_risk if max_risk > 0 else 0,
            'strikes': strikes,
            'options': options
        }
    
    def _extract_option_prices(self, option_chain: Dict, strikes: Dict) -> Optional[Dict]:
        """Extract option prices for the specified strikes"""
        try:
            # Find the option chain with matching expiry
            target_expiry = datetime.now() + timedelta(days=strikes['days_to_expiry'])
            
            for chain in option_chain.get('optionChain', []):
                expiry_date = pd.to_datetime(chain['expiryDate'])
                if abs((expiry_date - target_expiry).days) <= 5:  # Within 5 days
                    
                    # Find options with matching strikes
                    options = {}
                    for strike_group in chain.get('chainPerRoot', []):
                        for strike_data in strike_group.get('chainPerStrikePrice', []):
                            strike_price = strike_data['strikePrice']
                            
                            # Match short put
                            if abs(strike_price - strikes['short_put_strike']) < 0.01:
                                options['short_put'] = {
                                    'bid': strike_data.get('put', {}).get('bid', 0),
                                    'ask': strike_data.get('put', {}).get('ask', 0)
                                }
                            
                            # Match long put
                            elif abs(strike_price - strikes['long_put_strike']) < 0.01:
                                options['long_put'] = {
                                    'bid': strike_data.get('put', {}).get('bid', 0),
                                    'ask': strike_data.get('put', {}).get('ask', 0)
                                }
                            
                            # Match short call
                            elif abs(strike_price - strikes['short_call_strike']) < 0.01:
                                options['short_call'] = {
                                    'bid': strike_data.get('call', {}).get('bid', 0),
                                    'ask': strike_data.get('call', {}).get('ask', 0)
                                }
                            
                            # Match long call
                            elif abs(strike_price - strikes['long_call_strike']) < 0.01:
                                options['long_call'] = {
                                    'bid': strike_data.get('call', {}).get('bid', 0),
                                    'ask': strike_data.get('call', {}).get('ask', 0)
                                }
                    
                    if len(options) == 4:  # All options found
                        return options
            
            return None
            
        except Exception as e:
            print(f"Error extracting option prices: {e}")
            return None
    
    def _estimate_probability_of_profit(self, current_price: float, 
                                      strikes: Dict, options: Dict) -> float:
        """
        Estimate probability of profit for iron condor
        Simplified calculation - in practice, this would use option pricing models
        """
        # Calculate the range where the position is profitable
        profit_range_start = strikes['short_put_strike']
        profit_range_end = strikes['short_call_strike']
        profit_range_width = profit_range_end - profit_range_start
        
        # Estimate probability based on current price position and volatility
        # This is a simplified approach - real implementation would use IV
        if current_price < profit_range_start:
            # Price below profit range
            distance_to_range = profit_range_start - current_price
            prob_profit = max(0.1, 1 - (distance_to_range / current_price))
        elif current_price > profit_range_end:
            # Price above profit range
            distance_to_range = current_price - profit_range_end
            prob_profit = max(0.1, 1 - (distance_to_range / current_price))
        else:
            # Price within profit range
            prob_profit = 0.85  # High probability if already in range
        
        return min(0.95, max(0.05, prob_profit))
    
    def scan_for_opportunities(self) -> List[Dict]:
        """
        Scan the ETF universe for iron condor opportunities
        Returns opportunities that meet the strategy criteria
        """
        opportunities = []
        current_positions = self.get_current_positions()
        
        print(f"Scanning {len(self.etf_universe)} ETFs for iron condor opportunities...")
        print(f"Current positions: {len(current_positions)}")
        
        for symbol in self.etf_universe:
            try:
                print(f"\nAnalyzing {symbol}...")
                
                # Check if we already have a position in this symbol
                if self._has_position_in_symbol(symbol, current_positions):
                    print(f"  Skipping {symbol} - already have position")
                    continue
                
                # Check if we've reached max positions
                if len(current_positions) >= self.max_positions:
                    print(f"  Skipping {symbol} - max positions reached")
                    continue
                
                # Analyze stock suitability
                analysis = self.analyzer.analyze_stock_suitability(symbol)
                
                if analysis.get('score', 0) < 70:  # Minimum score threshold
                    print(f"  {symbol} score too low: {analysis.get('score', 0)}")
                    continue
                
                # Get suggested strikes
                suggested_strikes = analysis.get('suggested_strikes')
                if not suggested_strikes:
                    print(f"  {symbol} - no suitable strikes found")
                    continue
                
                # Calculate position metrics
                metrics = self.calculate_iron_condor_metrics(symbol, suggested_strikes)
                if not metrics:
                    print(f"  {symbol} - could not calculate metrics")
                    continue
                
                # Check strategy criteria
                if self._meets_strategy_criteria(metrics):
                    opportunities.append({
                        'symbol': symbol,
                        'analysis': analysis,
                        'metrics': metrics,
                        'priority_score': self._calculate_priority_score(metrics)
                    })
                    print(f"  ✓ {symbol} - QUALIFIED OPPORTUNITY")
                    print(f"    Prob Profit: {metrics['probability_of_profit']:.1%}")
                    print(f"    Max Risk: ${metrics['max_risk']:.2f}")
                    print(f"    Expected Value: ${metrics['expected_value']:.2f}")
                else:
                    print(f"  {symbol} - does not meet criteria")
                
                # Rate limiting to avoid API limits
                time.sleep(1)
                
            except Exception as e:
                print(f"  Error analyzing {symbol}: {e}")
                continue
        
        # Sort opportunities by priority score
        opportunities.sort(key=lambda x: x['priority_score'], reverse=True)
        
        return opportunities
    
    def _has_position_in_symbol(self, symbol: str, positions: List[Dict]) -> bool:
        """Check if we already have a position in the given symbol"""
        for position in positions:
            if position.get('symbol') == symbol:
                return True
        return False
    
    def _meets_strategy_criteria(self, metrics: Dict) -> bool:
        """Check if position meets strategy criteria"""
        return (
            metrics['probability_of_profit'] >= self.target_probability and
            metrics['max_risk'] <= self.max_risk and
            metrics['expected_value'] >= self.config.MIN_EXPECTED_VALUE and
            metrics['alpha'] > 0 and
            metrics['risk_reward_ratio'] >= self.config.MIN_RISK_REWARD_RATIO
        )
    
    def _calculate_priority_score(self, metrics: Dict) -> float:
        """Calculate priority score for opportunity ranking"""
        # Weighted scoring based on multiple factors
        prob_score = metrics['probability_of_profit'] * 0.3
        risk_score = (1 - (metrics['max_risk'] / self.max_risk)) * 0.2
        ev_score = min(1.0, metrics['expected_value'] / 100) * 0.3
        alpha_score = min(1.0, metrics['alpha'] / 0.1) * 0.2
        
        return prob_score + risk_score + ev_score + alpha_score
    
    def execute_trade(self, opportunity: Dict) -> bool:
        """
        Execute the iron condor trade
        Returns True if successful, False otherwise
        """
        try:
            symbol = opportunity['symbol']
            metrics = opportunity['metrics']
            strikes = metrics['strikes']
            
            print(f"\nExecuting iron condor trade for {symbol}...")
            print(f"Strikes: Put {strikes['long_put_strike']}/{strikes['short_put_strike']}")
            print(f"        Call {strikes['short_call_strike']}/{strikes['long_call_strike']}")
            print(f"Net Credit: ${metrics['net_credit']:.2f}")
            print(f"Max Risk: ${metrics['max_risk']:.2f}")
            
            # Here you would implement the actual trade execution
            # This would involve calling your broker's API to place the orders
            
            # For now, we'll simulate the trade
            trade_result = self._simulate_trade_execution(opportunity)
            
            if trade_result:
                # Record the position
                position = {
                    'symbol': symbol,
                    'entry_date': datetime.now(),
                    'strikes': strikes,
                    'net_credit': metrics['net_credit'],
                    'max_risk': metrics['max_risk'],
                    'entry_price': metrics['current_price'],
                    'status': 'open'
                }
                self.current_positions.append(position)
                
                print(f"✓ Trade executed successfully for {symbol}")
                return True
            else:
                print(f"✗ Trade execution failed for {symbol}")
                return False
                
        except Exception as e:
            print(f"Error executing trade for {symbol}: {e}")
            return False
    
    def _simulate_trade_execution(self, opportunity: Dict) -> bool:
        """Simulate trade execution (replace with actual broker API calls)"""
        # Simulate 90% success rate
        import random
        return random.random() > 0.1
    
    def monitor_positions(self) -> List[Dict]:
        """
        Monitor current positions and identify exit opportunities
        Returns list of positions that should be closed
        """
        positions_to_close = []
        
        print(f"\nMonitoring {len(self.current_positions)} positions...")
        
        for position in self.current_positions:
            try:
                symbol = position['symbol']
                
                # Get current market data
                quote = self.data_fetcher.get_quote(symbol)
                if not quote:
                    continue
                
                current_price = quote['lastTradePrice']
                
                # Calculate current P&L
                current_pnl = self._calculate_current_pnl(position, current_price)
                
                # Check exit conditions
                if self._should_exit_position(position, current_pnl):
                    positions_to_close.append({
                        'position': position,
                        'current_pnl': current_pnl,
                        'exit_reason': self._get_exit_reason(position, current_pnl)
                    })
                    print(f"  {symbol}: EXIT SIGNAL - {current_pnl:.2f}% P&L")
                else:
                    print(f"  {symbol}: HOLD - {current_pnl:.2f}% P&L")
                
            except Exception as e:
                print(f"Error monitoring position {position['symbol']}: {e}")
                continue
        
        return positions_to_close
    
    def _calculate_current_pnl(self, position: Dict, current_price: float) -> float:
        """Calculate current P&L percentage for position"""
        # Simplified P&L calculation
        # In practice, you'd need to get current option prices
        entry_price = position['entry_price']
        price_change = (current_price - entry_price) / entry_price
        
        # For iron condor, P&L is more complex and depends on option prices
        # This is a simplified approximation
        return price_change * 100
    
    def _should_exit_position(self, position: Dict, current_pnl: float) -> bool:
        """Determine if position should be exited"""
        # Exit at profit target
        if current_pnl >= self.profit_target * 100:
            return True
        
        # Exit at stop loss
        if current_pnl <= -self.config.STOP_LOSS_PERCENT:
            return True
        
        # Exit if approaching expiration
        days_held = (datetime.now() - position['entry_date']).days
        if days_held >= (self.config.TARGET_DAYS_TO_EXPIRY - self.config.CLOSE_BEFORE_EXPIRY_DAYS):
            return True
        
        return False
    
    def _get_exit_reason(self, position: Dict, current_pnl: float) -> str:
        """Get the reason for exit"""
        if current_pnl >= self.profit_target * 100:
            return "Profit Target Reached"
        elif current_pnl <= -50:
            return "Stop Loss"
        else:
            return "Approaching Expiration"
    
    def close_position(self, position_data: Dict) -> bool:
        """Close a position"""
        try:
            position = position_data['position']
            symbol = position['symbol']
            
            print(f"\nClosing position for {symbol}...")
            print(f"Reason: {position_data['exit_reason']}")
            print(f"P&L: {position_data['current_pnl']:.2f}%")
            
            # Here you would implement the actual position closing
            # This would involve calling your broker's API to close the position
            
            # Simulate position closing
            success = self._simulate_position_closing(position)
            
            if success:
                # Remove from current positions and add to history
                self.current_positions.remove(position)
                position['exit_date'] = datetime.now()
                position['exit_pnl'] = position_data['current_pnl']
                position['exit_reason'] = position_data['exit_reason']
                position['status'] = 'closed'
                self.position_history.append(position)
                
                print(f"✓ Position closed successfully for {symbol}")
                return True
            else:
                print(f"✗ Position closing failed for {symbol}")
                return False
                
        except Exception as e:
            print(f"Error closing position for {position['symbol']}: {e}")
            return False
    
    def _simulate_position_closing(self, position: Dict) -> bool:
        """Simulate position closing (replace with actual broker API calls)"""
        # Simulate 95% success rate
        import random
        return random.random() > 0.05
    
    def run_strategy(self):
        """Main strategy execution loop"""
        print("=" * 60)
        print("ETF IRON CONDOR STRATEGY")
        print("=" * 60)
        
        while True:
            try:
                print(f"\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Strategy Cycle")
                
                # Step 1: Monitor existing positions
                positions_to_close = self.monitor_positions()
                
                # Step 2: Close positions that meet exit criteria
                for position_data in positions_to_close:
                    self.close_position(position_data)
                
                # Step 3: Scan for new opportunities
                opportunities = self.scan_for_opportunities()
                
                # Step 4: Execute new trades (up to max positions)
                current_positions = self.get_current_positions()
                available_slots = self.max_positions - len(current_positions)
                
                for opportunity in opportunities[:available_slots]:
                    if self.execute_trade(opportunity):
                        print(f"New position opened for {opportunity['symbol']}")
                    else:
                        print(f"Failed to open position for {opportunity['symbol']}")
                
                # Step 5: Wait before next cycle
                wait_time = self.config.SCAN_INTERVAL_HOURS * 3600
                print(f"\nWaiting {self.config.SCAN_INTERVAL_HOURS} hour(s) before next cycle...")
                time.sleep(wait_time)
                
            except KeyboardInterrupt:
                print("\nStrategy stopped by user")
                break
            except Exception as e:
                print(f"Error in strategy loop: {e}")
                time.sleep(300)  # Wait 5 minutes before retrying

if __name__ == '__main__':
    try:
        fetcher = QuestradeDataFetcher()
        strategy = ETFIronCondorStrategy(fetcher)
        
        # Run the strategy
        strategy.run_strategy()
        
    except Exception as e:
        print(f"Error running strategy: {e}") 
