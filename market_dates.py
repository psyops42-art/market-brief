"""Expected observations follow the instrument's own trading calendar."""
from datetime import date, timedelta
from functools import lru_cache
import pandas_market_calendars as mcal

CALENDARS = {
    **dict.fromkeys(("^GSPC", "^NDX", "^DJI", "^IXIC", "^VIX"), "NYSE"),
    **dict.fromkeys(("^TNX", "^TYX"), "SIFMA_US"),
    **dict.fromkeys(("^KS11", "^KQ11", "ktb3y", "ktb10y"), "XKRX"),
    "000001.SS": "SSE", "^STOXX50E": "XETR", "^GDAXI": "XETR",
}

@lru_cache(maxsize=128)
def expected_close(symbol: str, cutoff: str) -> str:
    name = CALENDARS.get(symbol)
    if not name:
        return cutoff
    return _expected_session(name, cutoff)


@lru_cache(maxsize=16)
def _calendar(name):
    return mcal.get_calendar(name)


@lru_cache(maxsize=128)
def _expected_session(name, cutoff):
    end = date.fromisoformat(cutoff)
    sessions = _calendar(name).valid_days(end - timedelta(days=40), end)
    if sessions.empty:
        raise ValueError(f"No trading sessions for {name} before {cutoff}")
    return sessions[-1].date().isoformat()


@lru_cache(maxsize=128)
def session_close(symbol: str, day: str):
    """UTC close for validating a timestamped quote, not just its date."""
    schedule = _calendar(CALENDARS[symbol]).schedule(day, day)
    return None if schedule.empty else schedule.iloc[-1]["market_close"].to_pydatetime()
