#!/usr/bin/env python3
"""
Test script to verify Questrade API connection and data retrieval
"""

import os
from dotenv import load_dotenv
from investment_strategies.questrade_data_fetcher import QuestradeDataFetcher, QuestradeCredentials
from investment_strategies.iron_condor_analyzer import IronCondorAnalyzer

def test_questrade_connection():
    """Test the Questrade API connection and basic data retrieval"""
    
    # Load environment variables
    load_dotenv()
    
    # Get refresh token from environment
    refresh_token = os.getenv('QUESTRADE_REFRESH_TOKEN')
    if not refresh_token:
        print("Error: QUESTRADE_REFRESH_TOKEN not found in environment variables")
        print("Please add your refresh token to the .env file")
        return False
    
    print(f"Using refresh token: {refresh_token[:10]}...")
    
    try:
        # Initialize credentials and data fetcher
        credentials = QuestradeCredentials(refresh_token=refresh_token)
        print("✓ Credentials initialized")
        
        fetcher = QuestradeDataFetcher(credentials)
        print("✓ Data fetcher initialized")
        
        # Test basic quote retrieval
        print("\nTesting quote retrieval for AAPL...")
        quote = fetcher.get_quote("AAPL")
        if quote:
            print(f"✓ Quote retrieved successfully")
            print(f"  Current Price: ${quote.get('lastTradePrice', 'N/A')}")
            print(f"  Volume: {quote.get('volume', 'N/A'):,}")
        else:
            print("✗ Failed to retrieve quote")
            return False
        
        # Test option chain retrieval
        print("\nTesting option chain retrieval for AAPL...")
        option_chain = fetcher.get_option_chain("AAPL")
        if option_chain:
            print(f"✓ Option chain retrieved successfully")
            # Check the structure of the option chain
            if isinstance(option_chain, dict):
                print(f"  Option chain keys: {list(option_chain.keys())}")
                if 'options' in option_chain:
                    print(f"  Number of options: {len(option_chain['options'])}")
                elif 'optionChain' in option_chain:
                    print(f"  Number of options: {len(option_chain['optionChain'])}")
            elif isinstance(option_chain, list):
                print(f"  Number of options: {len(option_chain)}")
        else:
            print("✗ Failed to retrieve option chain")
            return False
        
        # Test historical data retrieval
        print("\nTesting historical data retrieval for AAPL...")
        historical_data = fetcher.get_historical_data("AAPL", "2024-01-01", "2024-01-31")
        if historical_data:
            print(f"✓ Historical data retrieved successfully")
            print(f"  Number of data points: {len(historical_data)}")
        else:
            print("✗ Failed to retrieve historical data")
            return False
        
        # Test iron condor analyzer
        print("\nTesting iron condor analyzer...")
        analyzer = IronCondorAnalyzer(fetcher)
        analysis = analyzer.analyze_stock_suitability("AAPL")
        
        if 'error' in analysis:
            print(f"✗ Analysis failed: {analysis['error']}")
            return False
        else:
            print(f"✓ Analysis completed successfully")
            print(f"  Score: {analysis['score']}/11")
            print(f"  Recommendation: {analysis['recommendation']}")
        
        print("\n🎉 All tests passed! Questrade API connection is working correctly.")
        return True
        
    except Exception as e:
        print(f"✗ Error during testing: {str(e)}")
        return False

if __name__ == "__main__":
    print("Questrade API Connection Test")
    print("=" * 40)
    
    success = test_questrade_connection()
    
    if success:
        print("\nYou can now use the iron condor analyzer with your Questrade account!")
    else:
        print("\nPlease check your refresh token and try again.")
        print("Make sure to:")
        print("1. Generate a new refresh token from Questrade API Access")
        print("2. Add it to your .env file as QUESTRADE_REFRESH_TOKEN")
        print("3. Run this test again") 