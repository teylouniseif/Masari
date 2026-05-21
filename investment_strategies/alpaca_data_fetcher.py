import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from requests.auth import _basic_auth_str
from requests.exceptions import HTTPError

ALPACA_CREDENTIALS_FILE = "alpaca_credentials.json"
DEFAULT_PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
DEFAULT_DATA_URL = "https://data.alpaca.markets"
DEFAULT_BROKER_SANDBOX_URL = "https://broker-api.sandbox.alpaca.markets"


@dataclass
class AlpacaCredentials:
    api_key: str
    api_secret: str
    trading_base_url: str = DEFAULT_PAPER_TRADING_URL
    data_base_url: str = DEFAULT_DATA_URL
    broker_api_key: str = ""
    broker_api_secret: str = ""
    broker_base_url: str = DEFAULT_BROKER_SANDBOX_URL

    def to_dict(self) -> Dict[str, str]:
        return {
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "trading_base_url": self.trading_base_url,
            "data_base_url": self.data_base_url,
            "broker_api_key": self.broker_api_key,
            "broker_api_secret": self.broker_api_secret,
            "broker_base_url": self.broker_base_url,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AlpacaCredentials":
        def _clean(value: Any) -> str:
            return str(value or "").strip()

        return cls(
            api_key=_clean(data.get("api_key", "")),
            api_secret=_clean(data.get("api_secret", "")),
            trading_base_url=_clean(data.get("trading_base_url", DEFAULT_PAPER_TRADING_URL))
            or DEFAULT_PAPER_TRADING_URL,
            data_base_url=_clean(data.get("data_base_url", DEFAULT_DATA_URL)) or DEFAULT_DATA_URL,
            broker_api_key=_clean(data.get("broker_api_key", "")),
            broker_api_secret=_clean(data.get("broker_api_secret", "")),
            broker_base_url=_clean(data.get("broker_base_url", DEFAULT_BROKER_SANDBOX_URL))
            or DEFAULT_BROKER_SANDBOX_URL,
        )


class AlpacaDataFetcher:
    def __init__(
        self,
        credentials: Optional[AlpacaCredentials] = None,
        credentials_file: str = ALPACA_CREDENTIALS_FILE,
        data_feed: str = "iex",
        timeout_seconds: int = 20,
    ):
        self.credentials_file = credentials_file
        self.timeout_seconds = timeout_seconds
        self.data_feed = data_feed

        loaded = self._load_credentials(credentials_file)
        self.credentials = credentials or loaded
        if not self.credentials or not self.credentials.api_key or not self.credentials.api_secret:
            raise ValueError(
                "Missing Alpaca credentials. Populate alpaca_credentials.json with api_key/api_secret."
            )

    def _load_credentials(self, path: str) -> Optional[AlpacaCredentials]:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return AlpacaCredentials.from_dict(json.load(f))

    def _save_credentials(self) -> None:
        with open(self.credentials_file, "w", encoding="utf-8") as f:
            json.dump(self.credentials.to_dict(), f, indent=2)

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.credentials.api_key,
            "APCA-API-SECRET-KEY": self.credentials.api_secret,
        }

    def _build_unauthorized_hint(self, url: str) -> str:
        hints: List[str] = [
            "401 from Alpaca API. Check api_key/api_secret in alpaca_credentials.json."
        ]
        if "paper-api.alpaca.markets" in url and self.credentials.api_key.startswith("C"):
            hints.append(
                "The configured api_key starts with 'C', which is usually a Broker API key. "
                "paper-api.alpaca.markets/v2/account requires Trading API paper keys."
            )
        hints.append(
            "Generate paper Trading API keys in the Alpaca dashboard and place them in "
            "api_key/api_secret (not broker_api_key/broker_api_secret)."
        )
        return " ".join(hints)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        base_url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        url = f"{(base_url or self.credentials.trading_base_url).rstrip('/')}/{path.lstrip('/')}"
        req_headers = headers or self._headers()
        response = requests.request(
            method=method.upper(),
            url=url,
            headers=req_headers,
            params=params,
            json=payload,
            timeout=self.timeout_seconds,
        )
        try:
            response.raise_for_status()
        except HTTPError as exc:
            if response.status_code == 401:
                message = self._build_unauthorized_hint(url)
                raise ValueError(message) from exc
            raise
        if not response.text:
            return {}
        return response.json()

    def _data_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._request(
            method=method,
            path=path,
            params=params,
            base_url=self.credentials.data_base_url,
        )

    def _chunked(self, values: List[str], size: int) -> List[List[str]]:
        return [values[idx : idx + size] for idx in range(0, len(values), size)]

    def get_account(self) -> Dict[str, Any]:
        return self._request("GET", "v2/account")

    def get_accounts(self) -> List[Dict[str, Any]]:
        account = self.get_account()
        return [
            {
                "number": account.get("account_number", "paper"),
                "id": account.get("id"),
                "status": account.get("status"),
                "currency": account.get("currency"),
                "isPrimary": True,
            }
        ]

    def ensure_paper_account(self) -> Dict[str, Any]:
        account = self.get_account()
        if account.get("account_blocked"):
            raise ValueError("Alpaca paper account is blocked.")
        if account.get("trading_blocked"):
            raise ValueError("Alpaca paper account has trading blocked.")
        return account

    def create_paper_account(self, account_payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.credentials.broker_api_key or not self.credentials.broker_api_secret:
            raise ValueError(
                "broker_api_key/broker_api_secret are required to create paper accounts via Alpaca Broker API."
            )

        headers = {
            "Authorization": _basic_auth_str(
                self.credentials.broker_api_key, self.credentials.broker_api_secret
            )
        }
        return self._request(
            method="POST",
            path="v1/accounts",
            payload=account_payload,
            base_url=self.credentials.broker_base_url,
            headers=headers,
        )

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        data = self._data_request(
            "GET",
            "v2/stocks/snapshots",
            params={"symbols": symbol, "feed": self.data_feed},
        )
        snapshot = data.get(symbol, {})
        if not snapshot:
            raise ValueError(f"No snapshot returned for {symbol}")

        latest_trade = snapshot.get("latestTrade", {})
        daily_bar = snapshot.get("dailyBar", {})
        return {
            "symbol": symbol,
            "lastTradePrice": latest_trade.get("p", 0.0),
            "volume": daily_bar.get("v", 0),
            "timestamp": latest_trade.get("t"),
        }

    def get_historical_data(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        period: Optional[str] = None,
        interval: str = "1d",
    ) -> List[Dict[str, Any]]:
        timeframe = "1Day"
        if interval and interval.lower() in {"1h", "1hour"}:
            timeframe = "1Hour"

        if period and not start_date:
            period_map = {
                "1m": 30,
                "3m": 90,
                "6m": 180,
                "1y": 365,
            }
            days = period_map.get(period.lower(), 90)
            start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
            end_date = datetime.utcnow().strftime("%Y-%m-%d")

        params: Dict[str, Any] = {
            "timeframe": timeframe,
            "adjustment": "raw",
            "feed": self.data_feed,
            "limit": 1000,
        }
        if start_date:
            params["start"] = f"{start_date}T00:00:00Z" if "T" not in start_date else start_date
        if end_date:
            params["end"] = f"{end_date}T23:59:59Z" if "T" not in end_date else end_date

        data = self._data_request("GET", f"v2/stocks/{symbol}/bars", params=params)
        bars = data.get("bars", [])
        return [
            {
                "start": bar.get("t"),
                "open": bar.get("o", 0.0),
                "high": bar.get("h", 0.0),
                "low": bar.get("l", 0.0),
                "close": bar.get("c", 0.0),
                "volume": bar.get("v", 0),
            }
            for bar in bars
        ]

    def _parse_option_symbol(self, option_symbol: str) -> Optional[Dict[str, Any]]:
        # OCC: ROOT(1-6) + YYMMDD + C/P + strike*1000 (8 digits)
        match = re.match(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$", option_symbol)
        if not match:
            return None
        _, expiry_raw, cp_flag, strike_raw = match.groups()
        expiry = datetime.strptime(expiry_raw, "%y%m%d")
        return {
            "type": "call" if cp_flag == "C" else "put",
            "strike": int(strike_raw) / 1000,
            "expiry": expiry,
            "expiry_date": expiry.strftime("%Y-%m-%dT00:00:00Z"),
        }

    def _normalize_option_snapshots(self, snapshots: Dict[str, Any]) -> Dict[str, Any]:
        grouped: Dict[str, Dict[float, Dict[str, Any]]] = {}

        for option_symbol, snapshot in snapshots.items():
            parsed = self._parse_option_symbol(option_symbol)
            if not parsed:
                continue

            expiry_key = parsed["expiry"].strftime("%Y-%m-%dT00:00:00Z")
            strike = parsed["strike"]
            opt_type = parsed["type"]

            quote = snapshot.get("latestQuote", {})
            day_bar = snapshot.get("dailyBar", {})
            greeks = snapshot.get("greeks", {})

            option_payload = {
                "symbol": option_symbol,
                "bid": quote.get("bp", 0.0) or 0.0,
                "ask": quote.get("ap", 0.0) or 0.0,
                "volume": day_bar.get("v", snapshot.get("volume", 0)) or 0,
                "openInterest": snapshot.get("open_interest", 0) or 0,
                "impliedVolatility": snapshot.get("impliedVolatility", 0) or 0,
                "delta": greeks.get("delta"),
                "gamma": greeks.get("gamma"),
                "theta": greeks.get("theta"),
                "vega": greeks.get("vega"),
                "rho": greeks.get("rho"),
            }

            grouped.setdefault(expiry_key, {})
            grouped[expiry_key].setdefault(strike, {"strikePrice": strike})
            grouped[expiry_key][strike][opt_type] = option_payload

        option_chain = []
        for expiry, strike_map in sorted(grouped.items()):
            chain_per_strike = [
                strike_map[strike] for strike in sorted(strike_map.keys())
            ]
            option_chain.append(
                {
                    "expiryDate": expiry,
                    "chainPerRoot": [{"chainPerStrikePrice": chain_per_strike}],
                }
            )

        return {"optionChain": option_chain, "options": option_chain}

    def get_option_chain(self, symbol: str) -> Dict[str, Any]:
        data = self._data_request(
            "GET",
            f"v1beta1/options/snapshots/{symbol}",
            params={"feed": "indicative"},
        )
        snapshots = data.get("snapshots", {})
        return self._normalize_option_snapshots(snapshots)

    def get_option_contracts(
        self,
        underlying_symbol: str,
        expiration_date: Optional[str] = None,
        expiration_date_gte: Optional[str] = None,
        expiration_date_lte: Optional[str] = None,
        status: str = "active",
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "underlying_symbols": underlying_symbol,
            "status": status,
            "limit": limit,
        }
        if expiration_date:
            params["expiration_date"] = expiration_date
        if expiration_date_gte:
            params["expiration_date_gte"] = expiration_date_gte
        if expiration_date_lte:
            params["expiration_date_lte"] = expiration_date_lte

        data = self._request("GET", "v2/options/contracts", params=params)
        return data.get("option_contracts", [])

    def get_option_snapshots(
        self,
        option_symbols: List[str],
        feed: str = "indicative",
    ) -> Dict[str, Dict[str, Any]]:
        snapshots: Dict[str, Dict[str, Any]] = {}
        for chunk in self._chunked(option_symbols, 100):
            if not chunk:
                continue
            data = self._data_request(
                "GET",
                "v1beta1/options/snapshots",
                params={
                    "symbols": ",".join(chunk),
                    "feed": feed,
                    "limit": len(chunk),
                },
            )
            snapshots.update(data.get("snapshots", {}))
        return snapshots

    def get_weekly_option_window(
        self,
        underlying_symbol: str,
        start_date: Optional[str] = None,
        days: int = 7,
        feed: str = "indicative",
    ) -> Dict[str, Any]:
        start_dt = (
            datetime.strptime(start_date, "%Y-%m-%d")
            if start_date
            else datetime.utcnow()
        )
        end_dt = start_dt + timedelta(days=days)
        start_str = start_dt.strftime("%Y-%m-%d")
        end_str = end_dt.strftime("%Y-%m-%d")

        contracts = self.get_option_contracts(
            underlying_symbol=underlying_symbol,
            expiration_date_gte=start_str,
            expiration_date_lte=end_str,
        )
        option_symbols = [contract["symbol"] for contract in contracts if contract.get("symbol")]
        snapshots = self.get_option_snapshots(option_symbols, feed=feed) if option_symbols else {}

        return {
            "underlying_symbol": underlying_symbol,
            "start_date": start_str,
            "end_date": end_str,
            "contracts": contracts,
            "snapshots": snapshots,
        }

    def get_order_impact(self, account_id: str, order_payload: Dict[str, Any]) -> Dict[str, Any]:
        qty = float(order_payload.get("qty") or order_payload.get("quantity") or 0)
        price = float(order_payload.get("limit_price") or order_payload.get("limitPrice") or 0)
        estimated_cost = round(qty * price * 100, 2)
        account = self.get_account()
        buying_power = float(account.get("buying_power", 0.0))
        return {
            "estimated_cost": estimated_cost,
            "buying_power_before": buying_power,
            "buying_power_after": round(buying_power - estimated_cost, 2),
            "currency": account.get("currency", "USD"),
            "note": "Estimated impact (Alpaca paper API has no direct order-impact endpoint).",
        }

    def place_order(self, account_id: str, order_payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "v2/orders", payload=order_payload)
