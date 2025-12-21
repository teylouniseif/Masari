import pandas as pd
import numpy as np
from typing import Dict, List, Optional

class ValueInvestingStrategy:
    def __init__(self):
        self.fundamental_metrics = {
            'pe_ratio': None,
            'pb_ratio': None,
            'dividend_yield': None,
            'debt_to_equity': None
        }
    
    def calculate_intrinsic_value(self, 
                                earnings: float, 
                                growth_rate: float, 
                                discount_rate: float,
                                years: int = 10) -> float:
        """
        Calculate the intrinsic value using discounted cash flow analysis
        """
        present_value = 0
        for year in range(1, years + 1):
            future_earnings = earnings * (1 + growth_rate) ** year
            present_value += future_earnings / (1 + discount_rate) ** year
        
        return present_value
    
    def analyze_stock(self, 
                     stock_data: Dict[str, float],
                     industry_averages: Dict[str, float]) -> Dict[str, str]:
        """
        Analyze a stock based on value investing principles
        """
        analysis = {}
        
        # Price to Earnings Ratio Analysis
        if stock_data['pe_ratio'] < industry_averages['pe_ratio']:
            analysis['pe_ratio'] = "Undervalued"
        else:
            analysis['pe_ratio'] = "Overvalued"
            
        # Price to Book Ratio Analysis
        if stock_data['pb_ratio'] < industry_averages['pb_ratio']:
            analysis['pb_ratio'] = "Undervalued"
        else:
            analysis['pb_ratio'] = "Overvalued"
            
        # Dividend Yield Analysis
        if stock_data['dividend_yield'] > industry_averages['dividend_yield']:
            analysis['dividend_yield'] = "Attractive"
        else:
            analysis['dividend_yield'] = "Below Average"
            
        # Debt to Equity Analysis
        if stock_data['debt_to_equity'] < industry_averages['debt_to_equity']:
            analysis['debt_to_equity'] = "Healthy"
        else:
            analysis['debt_to_equity'] = "High Risk"
            
        return analysis
    
    def get_margin_of_safety(self, 
                           intrinsic_value: float, 
                           current_price: float) -> float:
        """
        Calculate the margin of safety
        """
        return (intrinsic_value - current_price) / intrinsic_value * 100 