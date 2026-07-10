from sinopac_api import get_api, get_realtime_data_from_sinopac, get_simulation_default


DEFAULT_MARKET_DATA = {
    "current_price": 0.0,
    "volume": 0,
    "vix": 0.0,
    "vwap": 0.0,
    "source": "Sinopac",
    "updated_at": None,
    "error": None,
}


def get_realtime_data(api=None, simulation=None):
    if api is None:
        api, login_error = get_api(
            simulation=get_simulation_default() if simulation is None else simulation
        )
        if login_error:
            data = DEFAULT_MARKET_DATA.copy()
            data["error"] = login_error
            return data

    return get_realtime_data_from_sinopac(api)
