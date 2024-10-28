import asyncio
from datetime import date
from dataclasses import dataclass

from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Greeks, Quote
from tastytrade import Session, Account
from tastytrade.instruments import Option, Equity

from lib import TTConfig, TTOption, TTOptionSide

from live_prices import LivePrices, TastytradeWrapper


@dataclass
class Strategist:
    live_prices: LivePrices
    underlying_streamer_symbol: str
    options_interval: int
    reference_price: float
    options_streamer_symbols: list[str]
    put_to_sell: Option | None = None
    put_to_buy: Option | None = None
    call_to_sell: Option | None = None
    call_to_buy: Option | None = None

    @classmethod
    async def create(
        cls,
        session: Session,
        underlying_symbol: str,
        options_interval: int
    ):
        streamer_symbols = await TastytradeWrapper.get_streamer_symbols_equities(session, [underlying_symbol])
        underlying_streamer_symbol = streamer_symbols[0]
        live_prices = await LivePrices.create(session, streamer_symbols)

        reference_price = (live_prices.quotes[underlying_streamer_symbol].bidPrice + live_prices.quotes[underlying_streamer_symbol].askPrice) / 2
        print(f'{underlying_streamer_symbol} is at {reference_price}')

        self = cls(live_prices, underlying_streamer_symbol, options_interval, reference_price, [])

        # Start the continuous build options loop
        asyncio.create_task(self.run_build_options_loop(session))

        return self

    async def run_build_options_loop(self, session: Session, interval: int = 1):
        while True:
            await self._build_options_around(session)
            await asyncio.sleep(interval)

    async def _build_options_around(self, session: Session, search_interval: int = 50, price_threshold: float = 3.5, insurance_offset: int = 30):
        print(f'DEBUG: running updates')
        today = date.today()
        dd = str(today.day)
        mm = str(today.month)
        yy = str(today.year)[-2:]
        exp_date = yy + mm + dd
        nearest_strike_price = round(self.reference_price / self.options_interval) * self.options_interval
        lower_strikes = [strike for strike in range(nearest_strike_price, nearest_strike_price - search_interval, -self.options_interval)]
        higher_strikes = [strike for strike in range(nearest_strike_price, nearest_strike_price + search_interval, self.options_interval)]
        
        lower_tt_options = [TTOption('SPXW', exp_date, TTOptionSide.PUT, strike) for strike in lower_strikes]
        higher_tt_options = [TTOption('SPXW', exp_date, TTOptionSide.CALL, strike) for strike in higher_strikes]

        lower_options = await TastytradeWrapper.get_options(session, lower_tt_options)
        higher_options = await TastytradeWrapper.get_options(session, higher_tt_options)
        
        # Sorting and filtering logic
        lower_options.sort(key=lambda o: o.strike_price, reverse=True)
        higher_options.sort(key=lambda o: o.strike_price, reverse=False)

        lower_streamer_symbols = [o.streamer_symbol for o in lower_options]
        higher_streamer_symbols = [o.streamer_symbol for o in higher_options]

        await self.live_prices.add_symbols(lower_streamer_symbols + higher_streamer_symbols)

        # Logic for selecting put and call options based on the price threshold
        for option in lower_options:
            price = self.live_prices.quotes[option.streamer_symbol].bidPrice
            if price < price_threshold:
                self.put_to_sell = option
                insurance_strike_price = option.strike_price - insurance_offset
                tt_put_to_buy = TTOption('SPXW', exp_date, TTOptionSide.PUT, insurance_strike_price)
                self.put_to_buy = await Option.a_get_option(session, tt_put_to_buy.symbol)
                break

        for option in higher_options:
            price = self.live_prices.quotes[option.streamer_symbol].bidPrice
            if price < price_threshold:
                self.call_to_sell = option
                insurance_strike_price = option.strike_price + insurance_offset
                tt_call_to_buy = TTOption('SPXW', exp_date, TTOptionSide.CALL, insurance_strike_price)
                self.call_to_buy = await Option.a_get_option(session, tt_call_to_buy.symbol)
                break

        await self.live_prices.add_symbols([self.call_to_buy.streamer_symbol, self.put_to_buy.streamer_symbol])

    def winnings(self):
        return (
            -self.live_prices.quotes[self.put_to_buy.streamer_symbol].askPrice
            + self.live_prices.quotes[self.put_to_sell.streamer_symbol].bidPrice
            + self.live_prices.quotes[self.call_to_sell.streamer_symbol].bidPrice
            - self.live_prices.quotes[self.call_to_buy.streamer_symbol].askPrice
        ) * 100

    def show_strategy(self):
        legs_are_available = self.put_to_buy is not None and \
            self.put_to_sell is not None and \
            self.call_to_sell is not None and \
            self.call_to_buy is not None
        leg_prices_are_available = legs_are_available and self.put_to_buy.streamer_symbol in self.live_prices.quotes and \
            self.put_to_sell.streamer_symbol in self.live_prices.quotes and \
            self.call_to_sell.streamer_symbol in self.live_prices.quotes and \
            self.call_to_buy.streamer_symbol in self.live_prices.quotes
        if not legs_are_available or not leg_prices_are_available:
            print('Wait strategy.')
            return
        
        print('====================')
        print('>>>>> STRATEGY <<<<<')
        print(f'Buy {self.put_to_buy.symbol} for {self.live_prices.quotes[self.put_to_buy.streamer_symbol].askPrice}')
        print(f'Sell {self.put_to_sell.symbol} for {self.live_prices.quotes[self.put_to_sell.streamer_symbol].bidPrice}')
        print(f'Sell {self.call_to_sell.symbol} for {self.live_prices.quotes[self.call_to_sell.streamer_symbol].bidPrice}')
        print(f'Buy {self.call_to_buy.symbol} for {self.live_prices.quotes[self.call_to_buy.streamer_symbol].askPrice}')
        print(f'-->> We pocket ${self.winnings()}')
        print('====================')


async def main():
    config = TTConfig()
    session = Session(config.username, config.password, is_test=not config.use_prod)

    strategist = await Strategist.create(session, 'SPX', 5)

    try:
        while True:
            strategist.show_strategy()
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        print("Stopping strategy display loop.")
    finally:
        await strategist.live_prices.close_channel()
        session.destroy()

if __name__ == '__main__':
    asyncio.run(main())