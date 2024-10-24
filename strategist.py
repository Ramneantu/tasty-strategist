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

        await self._build_options_around(session)

        return self


    async def _build_options_around(self, session: Session, search_interval: int = 50, price_threshold: float = 3.5, insurance_offset: int = 30):
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
        # Options return in ascending order of strike price. We want them in descending order of premium
        lower_options.sort(key=lambda o: o.strike_price, reverse=True)
        higher_options.sort(key=lambda o: o.strike_price, reverse=False)

        lower_streamer_symbols = [o.streamer_symbol for o in lower_options]
        higher_streamer_symbols = [o.streamer_symbol for o in higher_options]

        await self.live_prices.add_symbols(lower_streamer_symbols + higher_streamer_symbols)

        for option in lower_options:
            price = self.live_prices.quotes[option.streamer_symbol].bidPrice
            if price < price_threshold:
                print(f'Price {price} OK for PUT at {option.strike_price}')
                self.put_to_sell = option
                insurance_strike_price = option.strike_price - insurance_offset
                tt_put_to_buy = TTOption('SPXW', exp_date, TTOptionSide.PUT, insurance_strike_price)
                self.put_to_buy = await Option.a_get_option(session, tt_put_to_buy.symbol)
                break
            else:
                print(f'Price {price} too high for PUT at {option.strike_price}')

        for option in higher_options:
            price = self.live_prices.quotes[option.streamer_symbol].bidPrice
            if price < price_threshold:
                print(f'Price {price} OK for CALL at {option.strike_price}')
                self.call_to_sell = option
                insurance_strike_price = option.strike_price + insurance_offset
                tt_call_to_buy = TTOption('SPXW', exp_date, TTOptionSide.CALL, insurance_strike_price)
                self.call_to_buy = await Option.a_get_option(session, tt_call_to_buy.symbol)
                break
            else:
                print(f'Price {price} too high for CALL at {option.strike_price}')

        print(f'Adding insurance options to live prices: {self.call_to_buy.streamer_symbol}, {self.put_to_buy.streamer_symbol}')
        await self.live_prices.add_symbols([self.call_to_buy.streamer_symbol, self.put_to_buy.streamer_symbol])

    def show_stategy(self):
        print('====================')
        print('>>>>> STRATEGY <<<<<')
        print(f'Buy {self.put_to_buy.streamer_symbol} for {self.live_prices.quotes[self.put_to_buy.streamer_symbol].askPrice}')
        print(f'Sell {self.put_to_sell.streamer_symbol} for {self.live_prices.quotes[self.put_to_sell.streamer_symbol].bidPrice}')
        print(f'Sell {self.call_to_sell.streamer_symbol} for {self.live_prices.quotes[self.call_to_sell.streamer_symbol].bidPrice}')
        print(f'Buy {self.call_to_buy.streamer_symbol} for {self.live_prices.quotes[self.call_to_buy.streamer_symbol].askPrice}')
        pocket = (
            -self.live_prices.quotes[self.put_to_buy.streamer_symbol].askPrice
            +self.live_prices.quotes[self.put_to_sell.streamer_symbol].bidPrice
            +self.live_prices.quotes[self.call_to_sell.streamer_symbol].bidPrice
            -self.live_prices.quotes[self.call_to_buy.streamer_symbol].askPrice
        ) * 100
        print(f'-->> We pocket ${pocket}')
        print('====================')

        
async def main():
    config = TTConfig()
    session = Session(config.username, config.password, is_test=not config.use_prod)

    strategist = await Strategist.create(session, 'SPX', 5)

    strategist.show_stategy()

    await strategist.live_prices.close_channel()

    session.destroy()


if __name__=='__main__':
    asyncio.run(main())
        

        


