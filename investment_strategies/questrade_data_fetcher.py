import requests
import pandas as pd
import yfinance as yf
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import time
from dataclasses import dataclass
import os
import json

# Constants
CREDENTIALS_FILE = 'questrade_credentials.json'
QUESTRADE_TOKEN_URL = "https://login.questrade.com/oauth2/token"


def refresh_questrade_tokens(refresh_token: str, timeout_seconds: int = 20) -> Dict:
    """
    Exchange a refresh token for a new token bundle.
    Questrade rotates refresh tokens, so callers should persist the returned refresh token.
    """
    response = requests.post(
        QUESTRADE_TOKEN_URL,
        params={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return response.json()

@dataclass
class PriceData:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass
class QuestradeCredentials:
    refresh_token: str
    access_token: str = ""
    api_server: str = ""
    token_expiry: datetime = None

    def to_dict(self) -> Dict:
        """Convert credentials to dictionary for storage"""
        return {
            'refresh_token': self.refresh_token,
            'access_token': self.access_token,
            'api_server': self.api_server,
            'token_expiry': self.token_expiry.isoformat() if self.token_expiry else None
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'QuestradeCredentials':
        """Create credentials from dictionary"""
        return cls(
            refresh_token=data['refresh_token'],
            access_token=data.get('access_token', ""),
            api_server=data.get('api_server', ""),
            token_expiry=datetime.fromisoformat(data['token_expiry']) if data.get('token_expiry') else None
        )

@dataclass
class FundamentalMetrics:
    pe_ratio: float
    pb_ratio: float
    dividend_yield: float
    debt_to_equity: float
    eps: float
    book_value_per_share: float
    total_debt: float
    total_equity: float
    market_share: float
    revenue_growth: float
    earnings_growth: float
    profit_margin: float
    revenue: float
    net_income: float
    total_revenue: float
    industry_revenue: float

class QuestradeDataFetcher:
    def __init__(self, credentials: Optional[QuestradeCredentials] = None):
        self.credentials = self._load_credentials() or credentials
        if not self.credentials or not self.credentials.refresh_token:
            raise ValueError(
                f"No Questrade refresh token available. Add one to {CREDENTIALS_FILE}."
            )
        # Always refresh at startup so each run rotates refresh_token and access_token.
        self._authenticate()
        self._save_credentials()
    
    def _load_credentials(self) -> Optional[QuestradeCredentials]:
        """Load credentials from file if exists"""
        try:
            if os.path.exists(CREDENTIALS_FILE):
                with open(CREDENTIALS_FILE, 'r') as f:
                    data = json.load(f)
                    return QuestradeCredentials.from_dict(data)
        except Exception as e:
            print(f"Warning: Could not load credentials: {str(e)}")
        return None
    
    def _save_credentials(self):
        """Save credentials to file"""
        try:
            with open(CREDENTIALS_FILE, 'w') as f:
                json.dump(self.credentials.to_dict(), f)
        except Exception as e:
            print(f"Warning: Could not save credentials: {str(e)}")
    
    def _is_token_expired(self) -> bool:
        """Check if the current access token is expired or about to expire"""
        if not self.credentials.token_expiry:
            return True
        # Consider token expired if it's within 5 minutes of expiry
        return datetime.now() >= (self.credentials.token_expiry - timedelta(minutes=5))
    
    def _authenticate(self):
        """Authenticate with Questrade API and get access token"""
        try:
            data = refresh_questrade_tokens(self.credentials.refresh_token)
            self.credentials.access_token = data['access_token']
            self.credentials.api_server = data['api_server']
            self.credentials.refresh_token = data.get('refresh_token', self.credentials.refresh_token)
            expires_in_seconds = int(data.get('expires_in', 1800))
            self.credentials.token_expiry = datetime.now() + timedelta(seconds=expires_in_seconds)
            
        except requests.exceptions.RequestException as e:
            status_code = e.response.status_code if getattr(e, "response", None) is not None else None
            if status_code == 400:
                print("Error: Invalid refresh token or token already used. Please generate a new refresh token.")
                # Remove invalid credentials file
                if os.path.exists(CREDENTIALS_FILE):
                    os.remove(CREDENTIALS_FILE)
            raise Exception(f"Authentication failed: {str(e)}")
    
    def _make_request(self, endpoint: str, params: Dict = None) -> Dict:
        """Make authenticated request to Questrade API"""
        # Check if token needs refresh
        if self._is_token_expired():
            self._authenticate()
            self._save_credentials()
            
        headers = {
            'Authorization': f'Bearer {self.credentials.access_token}'
        }
        
        url = f"{self.credentials.api_server}v1/{endpoint}"
        try:
            response = requests.get(url, headers=headers, params=params)
            
            # If we get a 401, try to refresh the token and retry once
            if response.status_code == 401:
                self._authenticate()
                self._save_credentials()
                headers['Authorization'] = f'Bearer {self.credentials.access_token}'
                response = requests.get(url, headers=headers, params=params)
                
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"API request failed: {str(e)}")
    
    def get_symbol_id(self, symbol: str) -> int:
        """Get Questrade symbol ID for a given ticker symbol, preferring US equity listings"""
        endpoint = f"symbols/search?prefix={symbol}"
        data = self._make_request(endpoint)
        matches = data['symbols']
        print(f"[DEBUG] Symbol search results for '{symbol}':")
        for s in matches:
            print(f"  symbol: {s['symbol']}, id: {s['symbolId']}, desc: {s.get('description','')}, exch: {s.get('exchange','')}, type: {s.get('securityType','')}, currency: {s.get('currency','')}")
        # Prefer US equity listings
        for symbol_data in matches:
            if (symbol_data['symbol'].upper() == symbol.upper() and
                symbol_data.get('securityType', '').upper() == 'STOCK' and
                symbol_data.get('exchange', '').upper() in ['NYSE', 'NASDAQ', 'ARCA', 'BATS', 'IEX'] and
                symbol_data.get('currency', '').upper() == 'USD'):
                print(f"[DEBUG] Selected US equity symbol: {symbol_data['symbol']} (ID: {symbol_data['symbolId']})")
                return symbol_data['symbolId']
        # Fallback: first exact symbol match
        for symbol_data in matches:
            if symbol_data['symbol'].upper() == symbol.upper():
                print(f"[DEBUG] Selected fallback symbol: {symbol_data['symbol']} (ID: {symbol_data['symbolId']})")
                return symbol_data['symbolId']
        raise Exception(f"Symbol {symbol} not found")
    
    def get_historical_data(self, 
                          symbol: str, 
                          start_date: str, 
                          end_date: str,
                          interval: str = 'OneDay') -> List[Dict]:
        """
        Get historical market data (candles) for a symbol
        Dates must be in ISO 8601 format with time and timezone (e.g., 2024-01-01T00:00:00Z)
        """
        symbol_id = self.get_symbol_id(symbol)
        # Ensure ISO 8601 format with time and UTC timezone
        def to_iso8601(date_str):
            if 'T' in date_str:
                # Already has time
                if date_str.endswith('Z') or '+' in date_str or '-' in date_str[10:]:
                    return date_str
                else:
                    return date_str + 'Z'
            else:
                return date_str + 'T00:00:00Z'
        start_iso = to_iso8601(start_date)
        end_iso = to_iso8601(end_date)
        endpoint = f"markets/candles/{symbol_id}?startTime={start_iso}&endTime={end_iso}&interval={interval}"
        data = self._make_request(endpoint)
        return data.get('candles', [])
    
    def get_quote(self, symbol: str) -> Dict:
        """Get current quote for a symbol"""
        symbol_id = self.get_symbol_id(symbol)
        endpoint = f"markets/quotes/{symbol_id}"
        
        data = self._make_request(endpoint)
        return data['quotes'][0]
    
    def get_market_data_for_strategy(self,
                                   symbol: str,
                                   lookback_days: int = 30) -> Tuple[List[Dict], Dict]:
        """
        Get all necessary market data for strategy analysis
        Returns historical data and current quote
        """
        end_date = datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%dT%H:%M:%SZ')
        
        # Get historical data
        historical_data = self.get_historical_data(symbol, start_date, end_date)
        
        # Get current quote
        current_quote = self.get_quote(symbol)
        
        return historical_data, current_quote
    
    def get_account_positions(self) -> List[Dict]:
        """Get current positions in the account"""
        endpoint = "accounts/positions"
        data = self._make_request(endpoint)
        return data['positions']
    
    def get_account_balances(self) -> Dict:
        """Get account balances"""
        endpoint = "accounts/balances"
        data = self._make_request(endpoint)
        return data['perCurrencyBalances']
    
    def get_option_chain(self, symbol: str) -> Dict:
        """
        Get option chain for a symbol
        """
        try:
            symbol_id = self.get_symbol_id(symbol)
            endpoint = f"symbols/{symbol_id}/options"
            data = self._make_request(endpoint)
            
            # Handle different possible response structures
            if isinstance(data, dict):
                # Check for different possible keys
                if 'options' in data:
                    return data
                elif 'optionChain' in data:
                    return {'options': data['optionChain']}
                elif 'option' in data:
                    return {'options': [data['option']]}
                else:
                    # If no expected keys, return the data as is
                    return {'options': data if isinstance(data, list) else []}
            elif isinstance(data, list):
                return {'options': data}
            else:
                return {'options': []}
                
        except Exception as e:
            print(f"Error getting option chain for {symbol}: {str(e)}")
            return {'options': []}

    def get_fundamental_metrics(self, symbol: str) -> FundamentalMetrics:
        """
        Get fundamental metrics by combining Questrade data with Yahoo Finance data
        """
        try:
            # Get current quote from Questrade
            current_quote = self.get_quote(symbol)
            current_price = current_quote['lastTradePrice']
            
            # Get additional data from Yahoo Finance
            yf_ticker = yf.Ticker(symbol)
            info = yf_ticker.info
            
            # Calculate basic metrics
            pe_ratio = info.get('trailingPE', current_price / info.get('trailingEps', 1))
            pb_ratio = info.get('priceToBook', current_price / info.get('bookValue', 1))
            dividend_yield = info.get('dividendYield', 0) * 100 if info.get('dividendYield') else 0
            debt_to_equity = info.get('debtToEquity', 0)
            
            # Get financial data
            eps = info.get('trailingEps', 0)
            book_value_per_share = info.get('bookValue', 0)
            total_debt = info.get('totalDebt', 0)
            total_equity = info.get('totalEquity', 0)
            
            # Get revenue and growth metrics
            revenue = info.get('totalRevenue', 0)
            net_income = info.get('netIncome', 0)
            total_revenue = info.get('totalRevenue', 0)
            industry_revenue = info.get('industryRevenue', 0)
            
            # Calculate growth rates
            financials = yf_ticker.financials
            if not financials.empty and len(financials.columns) >= 2:
                current_revenue = financials.loc['Total Revenue'].iloc[0]
                previous_revenue = financials.loc['Total Revenue'].iloc[1]
                revenue_growth = ((current_revenue - previous_revenue) / previous_revenue) * 100
                
                current_earnings = financials.loc['Net Income'].iloc[0]
                previous_earnings = financials.loc['Net Income'].iloc[1]
                earnings_growth = ((current_earnings - previous_earnings) / previous_earnings) * 100
            else:
                revenue_growth = 0.0
                earnings_growth = 0.0
            
            # Calculate profit margin
            profit_margin = (net_income / revenue * 100) if revenue != 0 else 0.0
            
            # Calculate market share
            market_share = (revenue / industry_revenue * 100) if industry_revenue != 0 else 0.0
            
            return FundamentalMetrics(
                pe_ratio=pe_ratio,
                pb_ratio=pb_ratio,
                dividend_yield=dividend_yield,
                debt_to_equity=debt_to_equity,
                eps=eps,
                book_value_per_share=book_value_per_share,
                total_debt=total_debt,
                total_equity=total_equity,
                market_share=market_share,
                revenue_growth=revenue_growth,
                earnings_growth=earnings_growth,
                profit_margin=profit_margin,
                revenue=revenue,
                net_income=net_income,
                total_revenue=total_revenue,
                industry_revenue=industry_revenue
            )
            
        except Exception as e:
            print(f"Error getting fundamental metrics: {str(e)}")
            return FundamentalMetrics(
                pe_ratio=0.0,
                pb_ratio=0.0,
                dividend_yield=0.0,
                debt_to_equity=0.0,
                eps=0.0,
                book_value_per_share=0.0,
                total_debt=0.0,
                total_equity=0.0,
                market_share=0.0,
                revenue_growth=0.0,
                earnings_growth=0.0,
                profit_margin=0.0,
                revenue=0.0,
                net_income=0.0,
                total_revenue=0.0,
                industry_revenue=0.0
            )
    
    def get_industry_averages(self, symbol: str) -> Dict[str, float]:
        """
        Get industry average metrics for comparison
        """
        try:
            yf_ticker = yf.Ticker(symbol)
            info = yf_ticker.info
            
            # Get industry averages from Yahoo Finance
            industry_pe = info.get('industryPE')
            industry_pb = info.get('industryPriceToBook')
            industry_dy = info.get('industryDividendYield')
            industry_de = info.get('industryDebtToEquity')
            industry_profit_margin = info.get('industryProfitMargin')
            industry_revenue_growth = info.get('industryRevenueGrowth')
            industry_earnings_growth = info.get('industryEarningsGrowth')
            
            # If industry data is not available, try to get sector data
            if not any([industry_pe, industry_pb, industry_dy, industry_de]):
                sector_pe = info.get('sectorPE')
                sector_pb = info.get('sectorPriceToBook')
                sector_dy = info.get('sectorDividendYield')
                sector_de = info.get('sectorDebtToEquity')
                
                industry_pe = sector_pe if industry_pe is None else industry_pe
                industry_pb = sector_pb if industry_pb is None else industry_pb
                industry_dy = sector_dy if industry_dy is None else industry_dy
                industry_de = sector_de if industry_de is None else industry_de
            
            # If still no data, try to calculate from similar companies
            if not any([industry_pe, industry_pb, industry_dy, industry_de]):
                print(f"Warning: No industry data available for {symbol}. Using sector peers for comparison.")
                peers = info.get('peerList', [])
                if peers:
                    peer_metrics = []
                    for peer in peers[:5]:  # Use up to 5 peers
                        try:
                            peer_ticker = yf.Ticker(peer)
                            peer_info = peer_ticker.info
                            peer_metrics.append({
                                'pe': peer_info.get('trailingPE', 0),
                                'pb': peer_info.get('priceToBook', 0),
                                'dy': peer_info.get('dividendYield', 0),
                                'de': peer_info.get('debtToEquity', 0)
                            })
                        except:
                            continue
                    
                    if peer_metrics:
                        industry_pe = sum(m['pe'] for m in peer_metrics) / len(peer_metrics)
                        industry_pb = sum(m['pb'] for m in peer_metrics) / len(peer_metrics)
                        industry_dy = sum(m['dy'] for m in peer_metrics) / len(peer_metrics)
                        industry_de = sum(m['de'] for m in peer_metrics) / len(peer_metrics)
            
            # Set default values if still no data
            industry_pe = industry_pe if industry_pe is not None else 0.0
            industry_pb = industry_pb if industry_pb is not None else 0.0
            industry_dy = industry_dy if industry_dy is not None else 0.0
            industry_de = industry_de if industry_de is not None else 0.0
            industry_profit_margin = industry_profit_margin if industry_profit_margin is not None else 0.0
            industry_revenue_growth = industry_revenue_growth if industry_revenue_growth is not None else 0.0
            industry_earnings_growth = industry_earnings_growth if industry_earnings_growth is not None else 0.0
            
            # Print data source information
            if any([industry_pe, industry_pb, industry_dy, industry_de]):
                print(f"Industry metrics source: {'Sector' if 'sector' in str(info) else 'Industry' if 'industry' in str(info) else 'Peer companies'}")
            else:
                print(f"Warning: No industry comparison data available for {symbol}")
            
            return {
                'pe_ratio': industry_pe,
                'pb_ratio': industry_pb,
                'dividend_yield': industry_dy,
                'debt_to_equity': industry_de,
                'profit_margin': industry_profit_margin,
                'revenue_growth': industry_revenue_growth,
                'earnings_growth': industry_earnings_growth
            }
            
        except Exception as e:
            print(f"Error getting industry averages: {str(e)}")
            return {
                'pe_ratio': 0.0,
                'pb_ratio': 0.0,
                'dividend_yield': 0.0,
                'debt_to_equity': 0.0,
                'profit_margin': 0.0,
                'revenue_growth': 0.0,
                'earnings_growth': 0.0
            }
    
    def get_dividend_history(self, symbol: str, years: int = 5) -> List[Dict]:
        """
        Get dividend history for a symbol
        """
        try:
            yf_ticker = yf.Ticker(symbol)
            dividends = yf_ticker.dividends
            
            # Get last 'years' years of dividends
            end_date = datetime.now()
            start_date = end_date - timedelta(days=years*365)
            
            dividends = dividends[start_date:end_date]
            
            return [{
                'date': str(date),
                'amount': amount
            } for date, amount in dividends.items()]
            
        except Exception as e:
            print(f"Error getting dividend history: {str(e)}")
            return []
    
    def get_financial_statements(self, symbol: str) -> Dict[str, pd.DataFrame]:
        """
        Get financial statements (income statement, balance sheet, cash flow)
        """
        try:
            yf_ticker = yf.Ticker(symbol)
            
            return {
                'income_statement': yf_ticker.financials,
                'balance_sheet': yf_ticker.balance_sheet,
                'cash_flow': yf_ticker.cashflow
            }
            
        except Exception as e:
            print(f"Error getting financial statements: {str(e)}")
            return {
                'income_statement': pd.DataFrame(),
                'balance_sheet': pd.DataFrame(),
                'cash_flow': pd.DataFrame()
            }

def create_price_data_from_questrade(historical_data: List[Dict]) -> List[PriceData]:
    """Convert Questrade historical data to PriceData objects"""
    price_data_list = []
    
    for candle in historical_data:
        price_data = PriceData(
            date=candle['start'],
            open=candle['open'],
            high=candle['high'],
            low=candle['low'],
            close=candle['close'],
            volume=candle['volume']
        )
        price_data_list.append(price_data)
    
    return price_data_list

# Example usage
if __name__ == "__main__":
    fetcher = QuestradeDataFetcher()
    
    # Example: Get fundamental metrics for AAPL
    try:
        # Get fundamental metrics
        metrics = fetcher.get_fundamental_metrics('AAPL')
        print("\nFundamental Metrics:")
        print(f"P/E Ratio: {metrics.pe_ratio:.2f}")
        print(f"P/B Ratio: {metrics.pb_ratio:.2f}")
        print(f"Dividend Yield: {metrics.dividend_yield:.2f}%")
        print(f"Debt to Equity: {metrics.debt_to_equity:.2f}")
        print(f"Market Share: {metrics.market_share:.2f}%")
        print(f"Revenue Growth: {metrics.revenue_growth:.2f}%")
        print(f"Earnings Growth: {metrics.earnings_growth:.2f}%")
        print(f"Profit Margin: {metrics.profit_margin:.2f}%")
        
        # Get industry averages
        industry_avgs = fetcher.get_industry_averages('AAPL')
        print("\nIndustry Averages:")
        print(f"Industry P/E: {industry_avgs['pe_ratio']:.2f}")
        print(f"Industry P/B: {industry_avgs['pb_ratio']:.2f}")
        print(f"Industry Dividend Yield: {industry_avgs['dividend_yield']:.2f}%")
        print(f"Industry Debt to Equity: {industry_avgs['debt_to_equity']:.2f}")
        print(f"Industry Profit Margin: {industry_avgs['profit_margin']:.2f}%")
        print(f"Industry Revenue Growth: {industry_avgs['revenue_growth']:.2f}%")
        print(f"Industry Earnings Growth: {industry_avgs['earnings_growth']:.2f}%")
        
    except Exception as e:
        print(f"Error: {str(e)}") 
