import sinopac_api


DEFAULT_FUTURES_ROOT = getattr(sinopac_api, "DEFAULT_FUTURES_ROOT", "TMF")


DEFAULT_MARKET_DATA = {
    "current_price": 0.0,
    "last_price": 0.0,
    "bid_price": 0.0,
    "ask_price": 0.0,
    "bid_volume": 0,
    "ask_volume": 0,
    "spread": 0.0,
    "volume": 0,
    "vix": 0.0,
    "vwap": 0.0,
    "source": "Sinopac",
    "updated_at": None,
    "quote_received_at": None,
    "exchange_timestamp": "",
    "contract_code": "",
    "delivery_date": "",
    "error": None,
}


def get_realtime_data(api=None, simulation=None, product_root=DEFAULT_FUTURES_ROOT):
    if api is None:
        api, login_error = sinopac_api.get_api(
            simulation=sinopac_api.get_simulation_default() if simulation is None else simulation
        )
        if login_error:
            data = DEFAULT_MARKET_DATA.copy()
            data["error"] = login_error
            return data

    try:
        return sinopac_api.get_realtime_data_from_sinopac(api, product_root=product_root)
    except TypeError:
        return sinopac_api.get_realtime_data_from_sinopac(api)
