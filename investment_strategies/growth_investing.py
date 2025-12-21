import pandas as pd
import numpy as np
from typing import Dict, List, Optional

class GrowthInvestingStrategy:
    def __init__(self):
        self.growth_metrics = {
            'revenue_growth': None,
            'earnings_growth': None,
            'profit_margin': None,
            'market_share': None
        }
    
    def calculate_growth_rate(self, 
                            historical_data: List[float],
                            periods: int = 5) -> float:
        """
        Calculate the compound annual growth rate (CAGR)
        """
        if len(historical_data) < 2:
            return 0.0
            
        start_value = historical_data[0]
        end_value = historical_data[-1]
        return (end_value / start_value) ** (1 / periods) - 1
    
    def analyze_company_growth(self, 
                             company_data: Dict[str, float],
                             industry_data: Dict[str, float]) -> Dict[str, str]:
        """
        Analyze a company's growth potential
        """
        analysis = {}
        
        # Revenue Growth Analysis
        if company_data['revenue_growth'] > industry_data['revenue_growth']:
            analysis['revenue_growth'] = "Strong"
        else:
            analysis['revenue_growth'] = "Below Industry"
            
        # Earnings Growth Analysis
        if company_data['earnings_growth'] > industry_data['earnings_growth']:
            analysis['earnings_growth'] = "Strong"
        else:
            analysis['earnings_growth'] = "Below Industry"
            
        # Profit Margin Analysis
        if company_data['profit_margin'] > industry_data['profit_margin']:
            analysis['profit_margin'] = "High"
        else:
            analysis['profit_margin'] = "Low"
            
        # Market Share Analysis
        if company_data['market_share'] > industry_data['market_share']:
            analysis['market_share'] = "Dominant"
        else:
            analysis['market_share'] = "Growing"
            
        return analysis
    
    def calculate_future_value(self,
                             current_value: float,
                             growth_rate: float,
                             years: int) -> float:
        """
        Calculate future value based on growth rate
        """
        return current_value * (1 + growth_rate) ** years
    
    def assess_growth_potential(self,
                              company_data: Dict[str, float],
                              industry_data: Dict[str, float]) -> str:
        """
        Assess overall growth potential of the company
        """
        analysis = self.analyze_company_growth(company_data, industry_data)
        strong_indicators = sum(1 for value in analysis.values() 
                              if value in ["Strong", "High", "Dominant"])
        
        if strong_indicators >= 3:
            return "High Growth Potential"
        elif strong_indicators >= 2:
            return "Moderate Growth Potential"
        else:
            return "Limited Growth Potential" 