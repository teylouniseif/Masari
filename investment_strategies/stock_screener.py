import pandas as pd
from typing import List, Dict, Type
from .questrade_data_fetcher import QuestradeDataFetcher
from .iron_condor_analyzer import IronCondorAnalyzer
from .directional_spread_analyzer import BullCallSpreadAnalyzer, BearPutSpreadAnalyzer, BaseDirectionalAnalyzer

class StockScreener:
    """
    A stock screener to discover investment opportunities across different sectors
    for various options strategies.
    """
    def __init__(self, data_fetcher: QuestradeDataFetcher):
        self.data_fetcher = data_fetcher

    def get_sp500_stocks(self) -> pd.DataFrame:
        """
        Scrapes Wikipedia for the list of S&P 500 companies and their sectors.
        This provides a diverse, high-quality list of stocks for screening.
        """
        try:
            print("Fetching S&P 500 stock list from Wikipedia...")
            url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
            table = pd.read_html(url)
            sp500_df = table[0]
            # Clean up tickers that have a '.' (e.g., BRK.B -> BRK-B)
            sp500_df['Symbol'] = sp500_df['Symbol'].str.replace('.', '-', regex=False)
            print(f"Successfully fetched {len(sp500_df)} S&P 500 stocks.")
            return sp500_df[['Symbol', 'Security', 'GICS Sector']]
        except Exception as e:
            print(f"Error fetching S&P 500 list: {e}")
            return pd.DataFrame()

    def screen_stocks(self, stocks_df: pd.DataFrame, analyzer, num_per_sector: int = 3) -> Dict[str, List[Dict]]:
        """
        Screens stocks from the provided DataFrame using a given analyzer,
        picking a sample from each sector to find suitable candidates.
        """
        if stocks_df.empty:
            print("Stock DataFrame is empty, cannot screen.")
            return {}

        sectors = stocks_df['GICS Sector'].unique()
        all_suitable_stocks = {}

        print("\nStarting stock screen for suitable candidates...")
        for sector in sectors:
            print(f"\n--- Screening Sector: {sector} ---")
            sector_stocks = stocks_df[stocks_df['GICS Sector'] == sector]
            
            # Take a random sample of stocks from the sector to analyze
            sample_stocks = sector_stocks.sample(min(num_per_sector, len(sector_stocks)))
            
            symbols_to_analyze = sample_stocks['Symbol'].tolist()
            print(f"Analyzing symbols: {', '.join(symbols_to_analyze)}")
            
            suitable_in_sector = analyzer.find_suitable_stocks(symbols_to_analyze)
            
            if suitable_in_sector:
                all_suitable_stocks[sector] = suitable_in_sector
        
        return all_suitable_stocks

if __name__ == '__main__':
    from dotenv import load_dotenv
    import os
    from .questrade_data_fetcher import QuestradeCredentials

    load_dotenv()
    
    # This screener requires the 'lxml' package to read HTML from Wikipedia.
    try:
        import lxml
    except ImportError:
        print("\nError: The 'lxml' package is required for the stock screener.")
        print("Please install it by running: pip install lxml")
        exit()

    print("\nInitializing Questrade connection for the screener...")
    
    refresh_token = os.getenv('QUESTRADE_REFRESH_TOKEN')
    if not refresh_token:
        print("Error: QUESTRADE_REFRESH_TOKEN not found in your .env file.")
        exit()

    credentials = QuestradeCredentials(refresh_token=refresh_token)
    
    try:
        fetcher = QuestradeDataFetcher(credentials)
        screener = StockScreener(fetcher)

        sp500_stocks = screener.get_sp500_stocks()

        if not sp500_stocks.empty:
            # --- Initialize All Analyzers ---
            analyzers = {
                "Iron Condor": IronCondorAnalyzer(fetcher),
                "Bull Call Spread": BullCallSpreadAnalyzer(fetcher),
                "Bear Put Spread": BearPutSpreadAnalyzer(fetcher)
            }

            # --- Run Screen for Each Strategy ---
            for strategy_name, analyzer in analyzers.items():
                print(f"\n\n{'='*20} SCREENING FOR: {strategy_name.upper()} {'='*20}")
                suitable_stocks = screener.screen_stocks(sp500_stocks, analyzer, num_per_sector=10)

                print(f"\n\n--- TOP CANDIDATES FOR: {strategy_name.upper()} ---")
                if not suitable_stocks:
                    print("\nNo suitable stocks found matching the criteria in the sampled stocks.")
                else:
                    for sector, stocks in suitable_stocks.items():
                        print(f"\nSector: {sector}")
                        for stock in stocks:
                            details = ""
                            if 'momentum_score' in stock.get('details', {}):
                                details = f"Momentum: {stock['details']['momentum_score']:.2f}"
                            print(f"  - {stock['symbol']}: Score {stock['score']}/11, {details} Rec: '{stock['recommendation']}'")

    except Exception as e:
        print(f"\nAn error occurred during the screening process: {e}")
        print("Please ensure your Questrade refresh token is valid and try again.") 