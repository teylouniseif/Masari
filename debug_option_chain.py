#!/usr/bin/env python3
"""
Debug script to examine Questrade option chain data structure
"""

import os
from dotenv import load_dotenv
from investment_strategies.questrade_data_fetcher import QuestradeDataFetcher, QuestradeCredentials
import json

def debug_option_chain():
    """Debug the option chain data structure"""
    
    # Load environment variables
    load_dotenv()
    
    # Get refresh token from environment
    refresh_token = os.getenv('QUESTRADE_REFRESH_TOKEN')
    
    try:
        # Initialize credentials and data fetcher
        credentials = QuestradeCredentials(refresh_token=refresh_token)
        fetcher = QuestradeDataFetcher(credentials)
        
        # Get option chain for AAPL
        print("Getting option chain for AAPL...")
        option_chain = fetcher.get_option_chain("AAPL")
        
        if option_chain:
            print("✓ Option chain retrieved successfully")
            print(f"Type: {type(option_chain)}")
            
            if isinstance(option_chain, dict):
                print(f"Keys: {list(option_chain.keys())}")
                for key, value in option_chain.items():
                    if isinstance(value, list):
                        print(f"  {key}: List with {len(value)} items")
                        if value and len(value) > 0:
                            print(f"    First item type: {type(value[0])}")
                            if isinstance(value[0], dict):
                                print(f"    First item keys: {list(value[0].keys())}")
                    else:
                        print(f"  {key}: {type(value)} - {value}")
            elif isinstance(option_chain, list):
                print(f"List with {len(option_chain)} items")
                if option_chain and len(option_chain) > 0:
                    print(f"First item type: {type(option_chain[0])}")
                    if isinstance(option_chain[0], dict):
                        print(f"First item keys: {list(option_chain[0].keys())}")
            
            # Print a sample of the data (first few items)
            print("\nSample data:")
            if isinstance(option_chain, dict):
                for key, value in option_chain.items():
                    if isinstance(value, list) and value:
                        print(f"\n{key} (first 2 items):")
                        for i, item in enumerate(value[:2]):
                            print(f"  Item {i+1}: {item}")
                        break
            elif isinstance(option_chain, list) and option_chain:
                print("First 2 items:")
                for i, item in enumerate(option_chain[:2]):
                    print(f"  Item {i+1}: {item}")
        else:
            print("✗ No option chain data received")
            
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("Questrade Option Chain Debug")
    print("=" * 40)
    debug_option_chain() 