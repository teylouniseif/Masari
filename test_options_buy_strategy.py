#!/usr/bin/env python3
"""
Test script for the Options Buying Strategy
This demonstrates how to use the strategy to find options buying opportunities.
"""

from investment_strategies.questrade_data_fetcher import QuestradeDataFetcher
from investment_strategies.options_buy_strategy import OptionsBuyStrategy

def test_options_buying_strategy():
    """Test the options buying strategy"""
    print("=" * 60)
    print("OPTIONS BUYING STRATEGY - TEST")
    print("=" * 60)
    
    try:
        fetcher = QuestradeDataFetcher()
        strategy = OptionsBuyStrategy(fetcher)
        
        print(f"Strategy Criteria:")
        print(f"  - Min Stock Price: ${strategy.criteria.min_price}")
        print(f"  - Max Stock Price: ${strategy.criteria.max_price}")
        print(f"  - Min DTE: {strategy.criteria.min_days_to_expiry} days")
        print(f"  - Max DTE: {strategy.criteria.max_days_to_expiry} days")
        print(f"  - Min IV: {strategy.criteria.min_implied_volatility:.1%}")
        print(f"  - Max IV: {strategy.criteria.max_implied_volatility:.1%}")
        print(f"  - Max Bid-Ask Spread: {strategy.criteria.max_bid_ask_spread:.1%}")
        print(f"  - Profit Target: {strategy.criteria.profit_target_multiplier:.1f}x")
        print(f"  - Stop Loss: {strategy.criteria.stop_loss_multiplier:.1f}x")
        
        # Test symbols
        test_symbols = ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA', 'AMZN', 'META', 'NFLX']
        
        print(f"\nAnalyzing {len(test_symbols)} symbols for options opportunities...")
        print("=" * 60)
        
        opportunities = strategy.find_best_opportunities(test_symbols)
        
        if opportunities:
            print(f"\n🎯 Found {len(opportunities)} opportunities:")
            print("-" * 100)
            
            for i, opp in enumerate(opportunities[:15], 1):  # Show top 15
                option = opp['option']
                metrics = opp['metrics']
                
                print(f"\n{i}. {opp['symbol']} - {option['type'].upper()} ${option['strike']} ({option['dte']} DTE)")
                print(f"   Stock Price: ${opp['current_price']:.2f} | Option Price: ${metrics['mid_price']:.2f}")
                print(f"   Delta: {metrics['delta']:.3f} | Prob Profit: {metrics['prob_profit']:.1%} | R/R: {metrics['risk_reward']:.2f}")
                print(f"   RSI: {opp['rsi']:.1f} | Momentum: {opp['momentum']:.3f} | Score: {opp['score']:.1f}")
                print(f"   IV: {option['iv']:.1%} | Volume: {option['volume']:,} | OI: {option['open_interest']:,}")
                
                # Add trading recommendation
                if opp['score'] >= 80:
                    print(f"   🟢 STRONG BUY - High probability setup")
                elif opp['score'] >= 60:
                    print(f"   🟡 MODERATE BUY - Good opportunity")
                elif opp['score'] >= 40:
                    print(f"   🔵 WATCH - Monitor for better entry")
                else:
                    print(f"   🔴 AVOID - Low quality setup")
        else:
            print("\n❌ No suitable opportunities found in this scan.")
            print("This could be due to:")
            print("  - Market conditions not suitable for options buying")
            print("  - Stocks not meeting the strict criteria")
            print("  - Options not meeting liquidity requirements")
            print("  - API data issues")
        
        # Test position management
        print(f"\n" + "=" * 60)
        print("POSITION MANAGEMENT TEST")
        print("=" * 60)
        
        if opportunities:
            # Create a mock position from the best opportunity
            best_opp = opportunities[0]
            position = strategy.create_position(best_opp, position_size=10)
            
            print(f"Created mock position:")
            print(f"  - ID: {position['id']}")
            print(f"  - Symbol: {position['symbol']}")
            print(f"  - Option: {position['option_type'].upper()} ${position['strike']}")
            print(f"  - Entry Price: ${position['entry_price']:.2f}")
            print(f"  - Position Size: {position['position_size']} contracts")
            print(f"  - Max Risk: ${position['max_risk']:.2f}")
            print(f"  - Profit Target: ${position['profit_target']:.2f}")
            print(f"  - Stop Loss: ${position['stop_loss']:.2f}")
            
            # Test monitoring
            positions_to_close = strategy.monitor_positions()
            
            if positions_to_close:
                print(f"\nPositions to close: {len(positions_to_close)}")
                for pos_data in positions_to_close:
                    pos = pos_data['position']
                    print(f"  - {pos['symbol']}: {pos_data['exit_reason']} (P&L: {pos['current_pnl']:.2f})")
            else:
                print(f"\nNo positions need to be closed at this time.")
        
        print(f"\n" + "=" * 60)
        print("STRATEGY SUMMARY")
        print("=" * 60)
        print("The Options Buying Strategy is designed to:")
        print("1. Identify high-probability options buying opportunities")
        print("2. Use technical analysis (RSI, momentum) for entry timing")
        print("3. Target 2:1 risk/reward ratio with proper stop losses")
        print("4. Monitor positions for optimal exit points")
        print("5. Manage risk with position sizing and stop losses")
        
        print(f"\nTo run the full strategy:")
        print("python investment_strategies/options_buy_strategy.py")
        
    except Exception as e:
        print(f"Error testing strategy: {e}")
        print("Please ensure your Questrade credentials are valid.")

if __name__ == '__main__':
    test_options_buying_strategy()




