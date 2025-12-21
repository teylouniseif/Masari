import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from dataclasses import dataclass

@dataclass
class Asset:
    symbol: str
    quantity: float
    current_price: float
    asset_type: str
    sector: str

class PortfolioAnalyzer:
    def __init__(self):
        self.assets: List[Asset] = []
        self.total_value: float = 0.0
    
    def add_asset(self, asset: Asset):
        """Add an asset to the portfolio"""
        self.assets.append(asset)
        self._update_total_value()
    
    def _update_total_value(self):
        """Update the total portfolio value"""
        self.total_value = sum(asset.quantity * asset.current_price 
                             for asset in self.assets)
    
    def get_asset_allocation(self) -> Dict[str, float]:
        """Calculate the percentage allocation of each asset"""
        if not self.total_value:
            return {}
            
        return {
            asset.symbol: (asset.quantity * asset.current_price) / self.total_value * 100
            for asset in self.assets
        }
    
    def get_sector_allocation(self) -> Dict[str, float]:
        """Calculate the percentage allocation by sector"""
        if not self.total_value:
            return {}
            
        sector_values = {}
        for asset in self.assets:
            sector_values[asset.sector] = sector_values.get(asset.sector, 0) + \
                                        (asset.quantity * asset.current_price)
        
        return {
            sector: value / self.total_value * 100
            for sector, value in sector_values.items()
        }
    
    def get_asset_type_allocation(self) -> Dict[str, float]:
        """Calculate the percentage allocation by asset type"""
        if not self.total_value:
            return {}
            
        type_values = {}
        for asset in self.assets:
            type_values[asset.asset_type] = type_values.get(asset.asset_type, 0) + \
                                          (asset.quantity * asset.current_price)
        
        return {
            asset_type: value / self.total_value * 100
            for asset_type, value in type_values.items()
        }
    
    def calculate_portfolio_metrics(self) -> Dict[str, float]:
        """Calculate key portfolio metrics"""
        if not self.assets:
            return {}
            
        # Calculate portfolio beta (simplified version)
        betas = [1.0 for _ in self.assets]  # Placeholder for actual beta values
        portfolio_beta = sum(
            beta * (asset.quantity * asset.current_price) / self.total_value
            for beta, asset in zip(betas, self.assets)
        )
        
        # Calculate portfolio volatility (simplified version)
        volatilities = [0.2 for _ in self.assets]  # Placeholder for actual volatility values
        portfolio_volatility = np.sqrt(sum(
            (vol * (asset.quantity * asset.current_price) / self.total_value) ** 2
            for vol, asset in zip(volatilities, self.assets)
        ))
        
        return {
            'portfolio_beta': portfolio_beta,
            'portfolio_volatility': portfolio_volatility,
            'number_of_assets': len(self.assets),
            'total_value': self.total_value
        }
    
    def rebalance_portfolio(self, target_allocations: Dict[str, float]) -> Dict[str, float]:
        """
        Calculate the required trades to rebalance the portfolio
        Returns the difference in value needed for each asset
        """
        current_allocations = self.get_asset_allocation()
        rebalance_actions = {}
        
        for symbol, target_percent in target_allocations.items():
            current_value = current_allocations.get(symbol, 0)
            target_value = target_percent * self.total_value / 100
            rebalance_actions[symbol] = target_value - current_value
            
        return rebalance_actions 