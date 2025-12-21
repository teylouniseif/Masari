"""
Questrade API Credentials Management
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional
import json
import os

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

    def save_to_file(self, filename: str = 'questrade_credentials.json'):
        """Save credentials to file"""
        try:
            with open(filename, 'w') as f:
                json.dump(self.to_dict(), f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save credentials: {str(e)}")

    @classmethod
    def load_from_file(cls, filename: str = 'questrade_credentials.json') -> Optional['QuestradeCredentials']:
        """Load credentials from file"""
        try:
            if os.path.exists(filename):
                with open(filename, 'r') as f:
                    data = json.load(f)
                    return cls.from_dict(data)
        except Exception as e:
            print(f"Warning: Could not load credentials: {str(e)}")
        return None 