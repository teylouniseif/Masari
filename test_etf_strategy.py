#!/usr/bin/env python3
"""
Test script for the ETF Iron Condor Strategy
This demonstrates how to use the strategy to scan for opportunities and manage positions.
"""

import os
from dotenv import load_dotenv
from investment_strategies.questrade_data_fetcher import QuestradeCredentials, QuestradeDataFetcher
from etf_iron_condor_strategy import ETFIronCondorStrategy

def test_strategy_scanning():
    """Test the strategy's scanning capabilities"""
    print("=" * 60)
    print("ETF IRON CONDOR STRATEGY - SCANNING TEST")
    print("=" * 60)
    
    # Initialize the strategy
    load_dotenv()
    refresh_token = os.getenv('QUESTRADE_REFRESH_TOKEN')
    
    if not refresh_token:
        print("Error: QUESTRADE_REFRESH_TOKEN not found in your .env file.")
        return
    
    try:
        credentials = QuestradeCredentials(refresh_token=refresh_token)
        fetcher = QuestradeDataFetcher(credentials)
        strategy = ETFIronCondorStrategy(fetcher)
        
        print(f"ETF Universe: {', '.join(strategy.etf_universe)}")
        print(f"Strategy Parameters:")
        print(f"  - Max Positions: {strategy.max_positions}")
        print(f"  - Target Probability: {strategy.target_probability:.1%}")
        print(f"  - Max Risk: ${strategy.max_risk}")
        print(f"  - Profit Target: {strategy.profit_target:.1%}")
        print(f"  - Target DTE: {strategy.monthly_dte_target} days")
        
        # Test scanning for opportunities
        print("\n" + "=" * 40)
        print("SCANNING FOR OPPORTUNITIES")
        print("=" * 40)
        
        # First check if we can connect to the account
        account_info = fetcher.get_account_info()
        if account_info:
            print(f"Connected to account: {account_info.get('type', 'Unknown')} - {account_info.get('number', 'Unknown')}")
        else:
            print("Warning: Could not retrieve account information")
        
        opportunities = strategy.scan_for_opportunities()
        
        if opportunities:
            print(f"\nFound {len(opportunities)} qualified opportunities:")
            print("-" * 80)
            
            for i, opp in enumerate(opportunities, 1):
                symbol = opp['symbol']
                metrics = opp['metrics']
                analysis = opp['analysis']
                
                print(f"\n{i}. {symbol} (Score: {analysis['score']}/100)")
                print(f"   Current Price: ${metrics['current_price']:.2f}")
                print(f"   Probability of Profit: {metrics['probability_of_profit']:.1%}")
                print(f"   Max Risk: ${metrics['max_risk']:.2f}")
                print(f"   Net Credit: ${metrics['net_credit']:.2f}")
                print(f"   Expected Value: ${metrics['expected_value']:.2f}")
                print(f"   Alpha: {metrics['alpha']:.4f}")
                print(f"   Risk/Reward: {metrics['risk_reward_ratio']:.2f}")
                
                if metrics['strikes']:
                    strikes = metrics['strikes']
                    print(f"   Suggested Strikes (DTE: {strikes['days_to_expiry']}):")
                    print(f"     Put Spread: {strikes['long_put_strike']} / {strikes['short_put_strike']}")
                    print(f"     Call Spread: {strikes['short_call_strike']} / {strikes['long_call_strike']}")
                    print(f"     Expected Move: ±${strikes['expected_move']:.2f}")
                
                print(f"   Priority Score: {opp['priority_score']:.3f}")
        else:
            print("\nNo qualified opportunities found in this scan.")
            print("This could be due to:")
            print("  - Market conditions not suitable for iron condors")
            print("  - ETFs not meeting the strict criteria")
            print("  - API data issues")
        
        # Test position monitoring (with mock data)
        print("\n" + "=" * 40)
        print("POSITION MONITORING TEST")
        print("=" * 40)
        
        # Add some mock positions for testing
        strategy.current_positions = [
            {
                'symbol': 'SPY',
                'entry_date': '2024-01-15',
                'entry_price': 450.0,
                'net_credit': 2.50,
                'max_risk': 250,
                'status': 'open'
            },
            {
                'symbol': 'GLD',
                'entry_date': '2024-01-10',
                'entry_price': 185.0,
                'net_credit': 1.80,
                'max_risk': 200,
                'status': 'open'
            }
        ]
        
        print(f"Monitoring {len(strategy.current_positions)} mock positions...")
        positions_to_close = strategy.monitor_positions()
        
        if positions_to_close:
            print(f"\nPositions to close: {len(positions_to_close)}")
            for pos_data in positions_to_close:
                symbol = pos_data['position']['symbol']
                pnl = pos_data['current_pnl']
                reason = pos_data['exit_reason']
                print(f"  - {symbol}: {pnl:.2f}% P&L, Reason: {reason}")
        else:
            print("No positions need to be closed at this time.")
        
        print("\n" + "=" * 40)
        print("STRATEGY SUMMARY")
        print("=" * 40)
        print("The ETF Iron Condor Strategy is designed to:")
        print("1. Scan 10 uncorrelated ETFs for iron condor opportunities")
        print("2. Target 70% probability of profit with <$300 risk")
        print("3. Take profits at 30% when possible")
        print("4. Manage up to 6 positions simultaneously")
        print("5. Use monthly contracts (30-45 DTE) for optimal theta decay")
        
        print("\nTo run the full automated strategy:")
        print("python investment_strategies/etf_iron_condor_strategy.py")
        
    except Exception as e:
        print(f"Error testing strategy: {e}")
        print("Please ensure your Questrade credentials are valid.")

if __name__ == '__main__':
    test_strategy_scanning() 